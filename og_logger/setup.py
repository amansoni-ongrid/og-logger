"""
Logger Setup using Loguru

Provides structured JSON logging with:
- ECS (Elastic Common Schema) compatible output
- Automatic rotation by file size (configurable, default 15MB)
- Configurable retention by time (days, hours, weeks) or file count
- Request context injection (request_id, client_ip, etc.)
- Thread-safe logging via enqueue
- Process-safe file logging via file locking (multi-worker compatible)
- Non-blocking async-safe file writes via background thread
- Graceful shutdown on SIGTERM (for Docker/Kubernetes containers)

Usage:
    from og_logger import setup_logger
    
    logger = setup_logger(service_name="my-api", level="DEBUG")

Environment variables:
    LOG_LEVEL: DEBUG, INFO, WARNING, ERROR (default: INFO)
    LOG_OUTPUT: stdout, file, both (default: stdout in prod, both in dev)
    LOG_DIR: Base directory for logs (default: logs)
    LOG_MAX_MB: Max file size in MB before rotation (default: 15)
    LOG_RETENTION_COUNT: Number of units to retain (default: 7)
    LOG_RETENTION_TYPE: days, hours, weeks, or files (default: days)
        - days: Keep logs from the last N days
        - hours: Keep logs from the last N hours
        - weeks: Keep logs from the last N weeks
        - files: Keep only the N most recent rotated files
    JSON_LOGS: true/false - force JSON output (default: auto based on ENVIRONMENT)
    ENVIRONMENT: development, staging, production (default: development)
"""
import os
import sys
import json
import glob
import atexit
import signal
import threading
import queue
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from filelock import FileLock

from .context import get_context

# Module-level tracking for cleanup on reconfiguration
_current_file_sink = None


def _build_log_dict(record) -> dict:
    """Build ECS-compatible log dict from loguru record."""
    log_dict = {
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "log.level": record["level"].name,
        "message": record["message"],
        "service.name": record["extra"].get("service_name", "app"),
        "service.environment": os.getenv("ENVIRONMENT", "development"),
        "log.origin.file": record["file"].name,
        "log.origin.line": record["line"],
        "log.origin.function": record["function"],
        **get_context(),  # Inject request context
    }
    
    # Add exception info if present
    if record["exception"]:
        log_dict["error.type"] = record["exception"].type.__name__ if record["exception"].type else None
        log_dict["error.message"] = str(record["exception"].value) if record["exception"].value else None
        log_dict["error.stack_trace"] = "".join(record["exception"].traceback) if record["exception"].traceback else None
    
    # Add extra fields (excluding internal ones)
    for key, value in record["extra"].items():
        if key not in ("service_name",):
            try:
                json.dumps(value)
                log_dict[key] = value
            except (TypeError, ValueError):
                log_dict[key] = str(value)
    
    return log_dict


def _json_sink(message) -> None:
    """Custom sink that writes ECS-compatible JSON to stdout."""
    log_dict = _build_log_dict(message.record)
    sys.stdout.write(json.dumps(log_dict, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _make_json_serializer():
    """Create a JSON serializer function for file output."""
    def serialize(record):
        log_dict = _build_log_dict(record)
        return json.dumps(log_dict, ensure_ascii=False, default=str)
    return serialize


def _console_formatter(record) -> str:
    """
    Format log record for colored console output.
    
    Output example:
    2026-02-02 10:30:00 | INFO     | [req:abc123] User logged in
    """
    ctx = get_context()
    
    # Build compact context prefix
    parts = [f"req:{ctx['request.id']}"] if ctx.get('request.id', '-') != '-' else []
    parts += [
        f"{k[:4]}:{v}"
        for k, v in ctx.items()
        if k not in ('request.id', 'client.ip') and v and v != '-'
    ]
    prefix = f"[{' | '.join(parts)}] " if parts else ""
    
    return (
        f"<green>{{time:YYYY-MM-DD HH:mm:ss}}</green> | "
        f"<level>{{level: <8}}</level> | "
        f"{prefix}{{message}}"
    )


class AsyncSafeFileSink:
    """
    A non-blocking, process-safe file sink for multi-worker async applications.
    
    Features:
    - Non-blocking: Log calls just enqueue messages (fast, async-safe)
    - Process-safe: Background thread uses file locking for multi-worker writes
    - Safe shutdown: atexit handler flushes remaining messages before exit
    - Automatic rotation by file size
    - Automatic cleanup of old log files based on retention policy
    
    This allows multiple Gunicorn/uvicorn workers to safely write to the same log file
    without blocking the async event loop.
    """
    
    # Class-level registry to track all sinks for cleanup
    # Protected by a lock for thread-safety when setup_logger() called from multiple threads
    _instances: list = []
    _instances_lock: threading.Lock = threading.Lock()
    _shutdown_registered: bool = False
    
    def __init__(
        self,
        path: str,
        max_size_mb: int = 15,
        retention_count: int = 7,
        retention_type: str = "days",
        serialize_func=None,
    ):
        self.base_path = Path(path)
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.retention_count = retention_count
        self.retention_type = retention_type
        self.serialize_func = serialize_func or (lambda r: r["message"])
        
        # Ensure directory exists
        self.base_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Lock file path (separate from log file)
        self.lock_path = self.base_path.with_suffix(".lock")
        self.lock = FileLock(str(self.lock_path), timeout=10)
        
        # Queue for non-blocking writes
        self._queue: queue.Queue = queue.Queue()
        self._shutdown = threading.Event()
        self._flushed = False
        
        # Start background writer thread
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="og-logger-file-writer",
            daemon=True,  # Daemon so it doesn't block exit, but we flush in atexit
        )
        self._writer_thread.start()
        
        # Register for cleanup (thread-safe)
        with AsyncSafeFileSink._instances_lock:
            AsyncSafeFileSink._instances.append(self)
            if not AsyncSafeFileSink._shutdown_registered:
                # Register atexit for normal Python exit
                atexit.register(AsyncSafeFileSink._cleanup_all)
                # Register signal handlers for container shutdown (SIGTERM)
                # SIGTERM is sent by Docker/Kubernetes before SIGKILL
                AsyncSafeFileSink._register_signal_handlers()
                AsyncSafeFileSink._shutdown_registered = True
    
    @classmethod
    def _register_signal_handlers(cls) -> None:
        """
        Register signal handlers for graceful shutdown in containers.
        
        SIGTERM: Sent by Docker/Kubernetes before SIGKILL (usually 30s grace period)
        SIGINT: Sent on Ctrl+C (for local development)
        
        Without this, atexit handlers don't run on SIGTERM and logs are lost.
        """
        def signal_handler(signum, frame):
            cls._cleanup_all()
            # Re-raise the signal to allow normal shutdown after cleanup
            # Reset to default handler to avoid infinite loop
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        
        # Only register if we're in the main thread (signals can only be registered there)
        if threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGTERM, signal_handler)
                signal.signal(signal.SIGINT, signal_handler)
            except (ValueError, OSError):
                # Signal handling not available (e.g., not main thread, or Windows limitations)
                pass
    
    @classmethod
    def _cleanup_all(cls) -> None:
        """Flush all sinks on application shutdown."""
        with cls._instances_lock:
            for sink in cls._instances:
                sink.flush()
    
    def _get_rotated_path(self) -> Path:
        """Generate a rotated log file path with timestamp."""
        # Include microseconds to avoid collision if multiple rotations in same second
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return self.base_path.with_suffix(f".{timestamp}.log")
    
    def _should_rotate(self) -> bool:
        """Check if current log file exceeds max size."""
        try:
            return self.base_path.stat().st_size >= self.max_size_bytes
        except (OSError, FileNotFoundError):
            # File doesn't exist or was just rotated by another process
            return False
    
    def _rotate(self) -> None:
        """Rotate the current log file."""
        try:
            if self.base_path.exists():
                rotated_path = self._get_rotated_path()
                self.base_path.rename(rotated_path)
        except OSError:
            # Another process might have rotated it already - that's fine
            pass
    
    def _cleanup_old_files(self) -> None:
        """Remove old log files based on retention policy."""
        pattern = str(self.base_path.with_suffix(".*.log"))
        rotated_files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        
        if self.retention_type == "files":
            # Keep only N most recent files
            for old_file in rotated_files[self.retention_count:]:
                try:
                    os.remove(old_file)
                except OSError:
                    pass
        else:
            # Time-based retention
            now = datetime.now()
            if self.retention_type == "days":
                max_age_seconds = self.retention_count * 86400
            elif self.retention_type == "hours":
                max_age_seconds = self.retention_count * 3600
            elif self.retention_type == "weeks":
                max_age_seconds = self.retention_count * 604800
            else:
                max_age_seconds = self.retention_count * 86400  # Default to days
            
            for old_file in rotated_files:
                try:
                    file_age = now.timestamp() - os.path.getmtime(old_file)
                    if file_age > max_age_seconds:
                        os.remove(old_file)
                except OSError:
                    pass
    
    def _write_batch(self, messages: list) -> None:
        """Write a batch of messages to file with locking."""
        if not messages:
            return
        
        try:
            with self.lock:
                # Check rotation (inside lock to prevent race conditions)
                if self._should_rotate():
                    self._rotate()
                    self._cleanup_old_files()
                
                # Write all messages in batch
                with open(self.base_path, "a", encoding="utf-8") as f:
                    for msg in messages:
                        f.write(msg + "\n")
                    f.flush()
        except Exception:
            # Lock timeout or I/O error - write directly without lock as fallback
            # Better to have potentially interleaved logs than lost logs
            try:
                with open(self.base_path, "a", encoding="utf-8") as f:
                    for msg in messages:
                        f.write(msg + "\n")
                    f.flush()
            except Exception:
                # Last resort failed - messages will be lost
                pass
    
    def _writer_loop(self) -> None:
        """Background thread that processes the queue and writes to file."""
        batch = []
        batch_timeout = 0.1  # Flush every 100ms or when batch is full
        batch_max_size = 100  # Max messages per batch
        
        while not self._shutdown.is_set():
            try:
                # Wait for message with timeout
                msg = self._queue.get(timeout=batch_timeout)
                batch.append(msg)
                
                # Drain queue up to batch size
                while len(batch) < batch_max_size:
                    try:
                        msg = self._queue.get_nowait()
                        batch.append(msg)
                    except queue.Empty:
                        break
                
                # Write batch
                self._write_batch(batch)
                batch = []
                
            except queue.Empty:
                # Timeout - flush any pending messages
                if batch:
                    self._write_batch(batch)
                    batch = []
            except Exception:
                # Don't let exceptions kill the writer thread
                batch = []
        
        # Flush remaining batch on shutdown
        if batch:
            self._write_batch(batch)
    
    def write(self, message) -> None:
        """Non-blocking write - just enqueue the message."""
        record = message.record
        serialized = self.serialize_func(record)
        
        try:
            self._queue.put_nowait(serialized)
        except queue.Full:
            # Queue is full - drop message (shouldn't happen with unbounded queue)
            pass
    
    def flush(self, timeout: float = 5.0) -> None:
        """
        Flush all pending messages to disk.
        
        Called automatically on application shutdown via atexit.
        Can also be called manually if needed.
        
        Args:
            timeout: Maximum seconds to wait for queue to drain
        """
        # Guard against double-flush
        if self._flushed:
            return
        self._flushed = True
        
        self._shutdown.set()
        
        # Wait for writer thread to finish processing
        self._writer_thread.join(timeout=timeout)
        
        # Write any remaining messages directly (in case thread didn't finish)
        remaining = []
        while True:
            try:
                remaining.append(self._queue.get_nowait())
            except queue.Empty:
                break
        
        if remaining:
            self._write_batch(remaining)


def setup_logger(
    service_name: str = None,
    level: str = None,
    json_output: bool = None,
    log_output: str = None,
    log_dir: str = None,
    max_mb: int = None,
    retention_count: int = None,
    retention_type: str = None,
):
    """
    Configure and return the loguru logger.
    
    Args:
        service_name: Service identifier included in JSON logs as "service.name".
            Useful for filtering logs in multi-service environments.
            Default: "app" or SERVICE_NAME env var.
        
        level: Minimum log level to output. Messages below this level are ignored.
            "DEBUG": Most verbose. Development details, variable values, flow tracing.
            "INFO": General operational messages. Request logs, startup/shutdown.
            "WARNING": Potential issues that don't stop execution. Deprecations, retries.
            "ERROR": Failures that need attention. Exceptions, failed operations.
            Default: "INFO" or LOG_LEVEL env var.
        
        json_output: Controls output format for stdout logs.
            True: Force JSON output (ECS-compatible, good for log aggregators).
            False: Force colored console output (human-readable, good for dev).
            None (default): Auto-detect based on ENVIRONMENT:
                * "production"/"staging" → JSON
                * "development" → Colored console
                Can also be forced via JSON_LOGS=true env var.
        
        log_output: Where to send logs - "stdout", "file", or "both".
            Default: "stdout" in production (safer for multi-worker), "both" in development.
        
        log_dir: Base directory for log files. Default: "logs" or LOG_DIR env var.
        
        max_mb: Max file size in MB before rotation. Default: 15 or LOG_MAX_MB env var.
        
        retention_count: Number of retention units. Default: 7 or LOG_RETENTION_COUNT env var.
        
        retention_type: Type of retention - "days", "hours", "weeks", or "files".
            Default: "days" or LOG_RETENTION_TYPE env var.
    
    Returns:
        Configured loguru logger instance.
    
    Example:
        # Development: colored console + file
        setup_logger(service_name="my-api", level="DEBUG")
        
        # Production: JSON to stdout only
        setup_logger(service_name="my-api", level="INFO", json_output=True, log_output="stdout")
    """
    # Load config from args or environment
    service_name = service_name or os.getenv("SERVICE_NAME", "app")
    log_level = level or os.getenv("LOG_LEVEL", "INFO").upper()
    env = os.getenv("ENVIRONMENT", "development")

    # Default to stdout-only in production (safer for multi-worker), file in dev
    default_output = "stdout" if env in ("production", "staging") else "both"
    output = log_output or os.getenv("LOG_OUTPUT", default_output).lower()
    base_dir = log_dir or os.getenv("LOG_DIR", "logs")
    max_size_mb = max_mb or int(os.getenv("LOG_MAX_MB", 15))
    ret_count = retention_count or int(os.getenv("LOG_RETENTION_COUNT", 7))
    ret_type = (retention_type or os.getenv("LOG_RETENTION_TYPE", "days")).lower()
    
    # Validate retention type
    if ret_type not in ("files", "days", "hours", "weeks"):
        raise ValueError(f"Invalid retention_type: {ret_type}. Must be: days, hours, weeks, or files")
    
    # Auto-detect JSON: use JSON in production/staging
    use_json = json_output if json_output is not None else (
        env in ("production", "staging") or os.getenv("JSON_LOGS", "").lower() == "true"
    )
    
    # Remove default handler (only once)
    logger.remove()
    
    # Bind service name to all logs
    configured_logger = logger.bind(service_name=service_name)
    
    # Stdout handler
    # enqueue=True ensures thread-safe logging within a single process
    if output in ("stdout", "both"):
        if use_json:
            logger.add(
                _json_sink,
                level=log_level,
                colorize=False,
                enqueue=True,
            )
        else:
            logger.add(
                sys.stdout,
                format=_console_formatter({}),  # Returns format string
                level=log_level,
                colorize=True,
                enqueue=True,
            )
    
    # File handler with rotation (async-safe and process-safe)
    if output in ("file", "both"):
        global _current_file_sink
        
        # Clean up previous file sink if reconfiguring
        if _current_file_sink is not None:
            _current_file_sink.flush(timeout=2.0)
            with AsyncSafeFileSink._instances_lock:
                if _current_file_sink in AsyncSafeFileSink._instances:
                    AsyncSafeFileSink._instances.remove(_current_file_sink)
        
        json_serializer = _make_json_serializer()
        
        # Create async-safe file sink with background thread and file locking
        # - Non-blocking: log calls just enqueue messages
        # - Process-safe: background thread uses file locking
        # - Safe shutdown: atexit flushes remaining messages
        _current_file_sink = AsyncSafeFileSink(
            path=f"{base_dir}/app.log",
            max_size_mb=max_size_mb,
            retention_count=ret_count,
            retention_type=ret_type,
            serialize_func=json_serializer,
        )
        
        logger.add(
            _current_file_sink.write,
            level=log_level,
            enqueue=False,  # Already handled by our queue
        )
    
    return configured_logger
