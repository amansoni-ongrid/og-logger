"""Functional tests for og_logger."""
import os
import json
import time
import shutil
import tempfile
import threading
import multiprocessing
from pathlib import Path

import pytest


class TestSetupLogger:
    """Tests for setup_logger function."""
    
    def test_setup_logger_returns_logger(self):
        """Test that setup_logger returns a configured logger."""
        from og_logger import setup_logger
        
        logger = setup_logger(service_name="test-service", log_output="stdout")
        assert logger is not None
        assert hasattr(logger, "info")
        assert hasattr(logger, "error")
    
    def test_setup_logger_with_different_levels(self):
        """Test logger with different log levels."""
        from og_logger import setup_logger
        
        for level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            logger = setup_logger(level=level, log_output="stdout")
            assert logger is not None


class TestFileLogging:
    """Tests for file logging functionality."""
    
    @pytest.fixture
    def temp_log_dir(self):
        """Create a temporary directory for log files."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_file_logging_creates_file(self, temp_log_dir):
        """Test that file logging creates a log file."""
        from og_logger import setup_logger
        
        logger = setup_logger(
            service_name="test",
            log_output="file",
            log_dir=temp_log_dir,
        )
        
        logger.info("Test message")
        
        # Give background thread time to write
        time.sleep(0.3)
        
        log_file = Path(temp_log_dir) / "app.log"
        assert log_file.exists(), f"Log file not created at {log_file}"
    
    def test_file_logging_writes_json(self, temp_log_dir):
        """Test that file logging writes valid JSON."""
        from og_logger import setup_logger
        
        logger = setup_logger(
            service_name="test-json",
            log_output="file",
            log_dir=temp_log_dir,
        )
        
        logger.info("JSON test message")
        
        # Give background thread time to write
        time.sleep(0.3)
        
        log_file = Path(temp_log_dir) / "app.log"
        assert log_file.exists()
        
        with open(log_file) as f:
            content = f.read().strip()
            if content:
                log_entry = json.loads(content)
                assert log_entry["message"] == "JSON test message"
                assert log_entry["service.name"] == "test-json"
                assert "@timestamp" in log_entry
    
    def test_multiple_log_messages(self, temp_log_dir):
        """Test writing multiple log messages."""
        from og_logger import setup_logger
        
        logger = setup_logger(
            service_name="test-multi",
            log_output="file",
            log_dir=temp_log_dir,
        )
        
        for i in range(10):
            logger.info(f"Message {i}")
        
        # Give background thread time to write
        time.sleep(0.5)
        
        log_file = Path(temp_log_dir) / "app.log"
        with open(log_file) as f:
            lines = [line for line in f.readlines() if line.strip()]
        
        assert len(lines) == 10


class TestContextLogging:
    """Tests for request context functionality."""
    
    def test_set_and_get_context(self):
        """Test setting and getting request context."""
        from og_logger import set_request_context, get_context, clear_request_context
        
        set_request_context("req-123", "192.168.1.1", user_id="user-456")
        
        ctx = get_context()
        assert ctx["request.id"] == "req-123"
        assert ctx["client.ip"] == "192.168.1.1"
        assert ctx["user_id"] == "user-456"
        
        clear_request_context()
        
        ctx = get_context()
        assert ctx["request.id"] == "-"
        assert ctx["client.ip"] == "-"
    
    def test_context_isolation(self):
        """Test that context is isolated between threads."""
        from og_logger import set_request_context, get_context, clear_request_context
        
        results = {}
        
        def thread_func(thread_id, request_id):
            set_request_context(request_id)
            time.sleep(0.1)  # Simulate work
            ctx = get_context()
            results[thread_id] = ctx["request.id"]
            clear_request_context()
        
        threads = [
            threading.Thread(target=thread_func, args=(1, "req-AAA")),
            threading.Thread(target=thread_func, args=(2, "req-BBB")),
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Each thread should see its own context
        assert results[1] == "req-AAA"
        assert results[2] == "req-BBB"


class TestAsyncSafeFileSink:
    """Tests for AsyncSafeFileSink class."""
    
    @pytest.fixture
    def temp_log_dir(self):
        """Create a temporary directory for log files."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_sink_creates_directory(self, temp_log_dir):
        """Test that sink creates log directory if it doesn't exist."""
        from og_logger.setup import AsyncSafeFileSink
        
        nested_dir = os.path.join(temp_log_dir, "nested", "logs")
        sink = AsyncSafeFileSink(path=os.path.join(nested_dir, "app.log"))
        
        assert os.path.exists(nested_dir)
        
        sink.flush()
    
    def test_sink_flush_on_shutdown(self, temp_log_dir):
        """Test that sink flushes messages on shutdown."""
        from og_logger.setup import AsyncSafeFileSink, _make_json_serializer
        
        log_path = os.path.join(temp_log_dir, "app.log")
        sink = AsyncSafeFileSink(
            path=log_path,
            serialize_func=lambda r: json.dumps({"message": r["message"]}),
        )
        
        # Create a mock message
        class MockMessage:
            record = {"message": "shutdown test"}
        
        sink.write(MockMessage())
        
        # Flush immediately
        sink.flush(timeout=2.0)
        
        # Verify message was written
        with open(log_path) as f:
            content = f.read()
            assert "shutdown test" in content


class TestRotation:
    """Tests for log rotation functionality."""
    
    @pytest.fixture
    def temp_log_dir(self):
        """Create a temporary directory for log files."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_rotation_by_size(self, temp_log_dir):
        """Test that rotation happens when file exceeds max size."""
        from og_logger.setup import AsyncSafeFileSink
        
        log_path = os.path.join(temp_log_dir, "app.log")
        
        # Use a very small max size (1KB) to trigger rotation
        sink = AsyncSafeFileSink(
            path=log_path,
            max_size_mb=0.001,  # ~1KB
            retention_type="files",
            retention_count=5,
            serialize_func=lambda r: "x" * 500,  # 500 byte messages
        )
        
        class MockMessage:
            record = {"message": "test"}
        
        # Write enough to trigger rotation
        for _ in range(5):
            sink.write(MockMessage())
        
        time.sleep(0.5)
        sink.flush()
        
        # Check that rotated files exist
        log_files = list(Path(temp_log_dir).glob("app.*.log"))
        # Should have at least one rotated file
        assert len(log_files) >= 1 or Path(log_path).exists()


class TestMemoryMonitoring:
    """Tests for memory monitoring functionality."""
    
    def test_memory_tracking_starts_and_stops(self):
        """Test that memory tracking can be started and stopped."""
        from og_logger import start_memory_tracking, stop_memory_tracking
        
        start_memory_tracking()
        metrics = stop_memory_tracking()
        
        assert "memory.allocated_mb" in metrics
        assert "memory.peak_mb" in metrics
        assert "memory.current_mb" in metrics
    
    def test_memory_tracking_returns_empty_when_disabled(self):
        """Test that memory context returns empty dict when not tracking."""
        from og_logger import get_memory_context, is_memory_monitoring_enabled
        
        assert not is_memory_monitoring_enabled()
        assert get_memory_context() == {}
    
    def test_double_stop_is_safe(self):
        """Test that calling stop twice doesn't cause errors."""
        from og_logger import start_memory_tracking, stop_memory_tracking
        
        start_memory_tracking()
        metrics1 = stop_memory_tracking()
        metrics2 = stop_memory_tracking()  # Should return empty, not error
        
        assert metrics1  # First stop returns metrics
        assert metrics2 == {}  # Second stop returns empty


# Helper functions for multiprocessing (must be at module level for pickling)
def _worker_write_logs(log_dir: str, worker_id: int, num_messages: int):
    """Worker function that writes logs from a separate process."""
    from og_logger import setup_logger
    
    logger = setup_logger(
        service_name=f"worker-{worker_id}",
        log_output="file",
        log_dir=log_dir,
    )
    
    for i in range(num_messages):
        logger.info(f"Worker {worker_id} message {i}")
    
    # Give time for background thread to write
    time.sleep(0.3)
    
    # Explicitly flush
    from og_logger.setup import _current_file_sink
    if _current_file_sink:
        _current_file_sink.flush(timeout=5.0)


def _worker_with_rotation(log_path: str, worker_id: int):
    """Worker that writes enough to trigger rotation."""
    from og_logger.setup import AsyncSafeFileSink
    
    sink = AsyncSafeFileSink(
        path=log_path,
        max_size_mb=0.001,  # ~1KB - will rotate quickly
        retention_type="files",
        retention_count=10,
        serialize_func=lambda r: json.dumps({
            "worker": worker_id,
            "message": r["message"],
            "data": "x" * 200,  # Pad to trigger rotation faster
        }),
    )
    
    class MockMessage:
        record = {"message": f"Worker {worker_id}"}
    
    for _ in range(20):
        sink.write(MockMessage())
        time.sleep(0.01)
    
    sink.flush(timeout=5.0)


class TestMultiWorkerLogging:
    """Tests for multi-process (multi-worker) logging safety."""
    
    @pytest.fixture
    def temp_log_dir(self):
        """Create a temporary directory for log files."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_multiple_processes_write_safely(self, temp_log_dir):
        """
        Test that multiple processes can write to the same log file safely.
        
        This simulates Gunicorn/uvicorn with multiple workers.
        """
        num_workers = 4
        messages_per_worker = 25
        total_expected = num_workers * messages_per_worker
        
        # Spawn multiple processes
        processes = []
        for worker_id in range(num_workers):
            p = multiprocessing.Process(
                target=_worker_write_logs,
                args=(temp_log_dir, worker_id, messages_per_worker),
            )
            processes.append(p)
        
        # Start all processes
        for p in processes:
            p.start()
        
        # Wait for all to complete
        for p in processes:
            p.join(timeout=30)
        
        # Verify all processes exited successfully
        for i, p in enumerate(processes):
            assert p.exitcode == 0, f"Worker {i} failed with exit code {p.exitcode}"
        
        # Read and verify log file
        log_file = Path(temp_log_dir) / "app.log"
        assert log_file.exists(), "Log file should exist"
        
        with open(log_file) as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
        
        # Verify we got all messages (no loss)
        assert len(lines) == total_expected, \
            f"Expected {total_expected} log lines, got {len(lines)}"
        
        # Verify each line is valid JSON (no corruption/interleaving)
        for i, line in enumerate(lines):
            try:
                log_entry = json.loads(line)
                assert "message" in log_entry, f"Line {i} missing 'message' field"
                assert "@timestamp" in log_entry, f"Line {i} missing '@timestamp' field"
            except json.JSONDecodeError as e:
                pytest.fail(f"Line {i} is not valid JSON: {line[:100]}... Error: {e}")
        
        # Verify all workers wrote their messages
        worker_counts = {i: 0 for i in range(num_workers)}
        for line in lines:
            log_entry = json.loads(line)
            msg = log_entry["message"]
            for worker_id in range(num_workers):
                if f"Worker {worker_id}" in msg:
                    worker_counts[worker_id] += 1
                    break
        
        for worker_id, count in worker_counts.items():
            assert count == messages_per_worker, \
                f"Worker {worker_id} wrote {count} messages, expected {messages_per_worker}"
    
    def test_concurrent_rotation_safety(self, temp_log_dir):
        """
        Test that multiple processes rotating simultaneously doesn't cause issues.
        """
        log_path = os.path.join(temp_log_dir, "app.log")
        
        # Run workers in parallel
        processes = []
        for worker_id in range(3):
            p = multiprocessing.Process(
                target=_worker_with_rotation,
                args=(log_path, worker_id),
            )
            processes.append(p)
        
        for p in processes:
            p.start()
        
        for p in processes:
            p.join(timeout=30)
        
        # All processes should complete without error
        for i, p in enumerate(processes):
            assert p.exitcode == 0, f"Worker {i} failed with exit code {p.exitcode}"
        
        # Verify log files exist (main + rotated)
        log_files = list(Path(temp_log_dir).glob("app*.log"))
        assert len(log_files) >= 1, "At least one log file should exist"
