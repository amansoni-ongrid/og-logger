"""
Logger Setup using Loguru

Provides structured JSON logging with:
- ECS (Elastic Common Schema) compatible output
- Automatic rotation by size (15MB) and time (daily)
- Configurable retention (by time or file count)
- Request context injection (request_id, client_ip, etc.)
- Thread-safe logging via enqueue

Multi-worker/container note:
    File logging is NOT safe when multiple processes write to the same file.
    In production with multiple Docker workers, use LOG_OUTPUT=stdout and let
    Docker/k8s handle log aggregation (to ELK, Loki, CloudWatch, etc.).

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
    JSON_LOGS: true/false - force JSON output (default: auto based on ENVIRONMENT)
    ENVIRONMENT: development, staging, production (default: development)
"""
import os
import sys
import json
from datetime import datetime, timezone

from loguru import logger

from .context import get_context


def _json_sink(message) -> None:
    """
    Custom sink that writes ECS-compatible JSON to stdout.
    Used internally - call logger.add() with this as sink.
    """
    record = message.record
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
    
    sys.stdout.write(json.dumps(log_dict, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _make_json_serializer():
    """Create a JSON serializer function for file output."""
    def serialize(record):
        log_dict = {
            "@timestamp": datetime.now(timezone.utc).isoformat(),
            "log.level": record["level"].name,
            "message": record["message"],
            "service.name": record["extra"].get("service_name", "app"),
            "service.environment": os.getenv("ENVIRONMENT", "development"),
            "log.origin.file": record["file"].name,
            "log.origin.line": record["line"],
            "log.origin.function": record["function"],
            **get_context(),
        }
        
        if record["exception"]:
            log_dict["error.type"] = record["exception"].type.__name__ if record["exception"].type else None
            log_dict["error.message"] = str(record["exception"].value) if record["exception"].value else None
            log_dict["error.stack_trace"] = "".join(record["exception"].traceback) if record["exception"].traceback else None
        
        for key, value in record["extra"].items():
            if key not in ("service_name",):
                try:
                    json.dumps(value)
                    log_dict[key] = value
                except (TypeError, ValueError):
                    log_dict[key] = str(value)
        
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
    
    # Build retention value based on type
    # "files" means keep N backup files, others are time-based
    if ret_type == "files":
        retention = ret_count  # Integer for file count
    elif ret_type in ("days", "hours", "weeks"):
        retention = f"{ret_count} {ret_type}"
    else:
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
    
    # File handler with rotation
    if output in ("file", "both"):
        json_serializer = _make_json_serializer()
        
        # Ensure log directory exists
        os.makedirs(base_dir, exist_ok=True)
        
        # Custom format function for JSON file output
        def json_format(record):
            record["extra"]["_serialized"] = json_serializer(record)
            return "{extra[_serialized]}\n"
        
        # WARNING: File logging is NOT safe for multiple processes/containers
        # writing to the same file. Use LOG_OUTPUT=stdout in production with
        # multiple workers and let Docker/k8s handle log aggregation.
        # enqueue=True only helps with threads within a single process.
        logger.add(
            f"{base_dir}/app.log",
            format=json_format,
            level=log_level,
            rotation=f"{max_size_mb} MB",
            retention=retention,  # Either "{N} days/hours/weeks" or int for file count
            encoding="utf-8",
            enqueue=True,
        )
    
    return configured_logger
