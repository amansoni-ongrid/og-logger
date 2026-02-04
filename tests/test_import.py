"""Basic import tests to verify package installation."""


def test_import_main():
    """Test that main module imports successfully."""
    import og_logger

    assert hasattr(og_logger, "setup_logger")
    assert hasattr(og_logger, "get_logger")


def test_import_context():
    """Test that context functions are available."""
    from og_logger import set_request_context, clear_request_context, get_context

    assert callable(set_request_context)
    assert callable(clear_request_context)
    assert callable(get_context)


def test_import_middleware():
    """Test that middleware is importable."""
    from og_logger import RequestLoggingMiddleware

    assert RequestLoggingMiddleware is not None


def test_import_memory():
    """Test that memory monitoring functions are available."""
    from og_logger import (
        start_memory_tracking,
        stop_memory_tracking,
        get_memory_context,
        is_memory_monitoring_enabled,
    )

    assert callable(start_memory_tracking)
    assert callable(stop_memory_tracking)
    assert callable(get_memory_context)
    assert callable(is_memory_monitoring_enabled)


def test_setup_logger():
    """Test basic logger setup."""
    from og_logger import setup_logger

    logger = setup_logger(service_name="test-service")
    assert logger is not None
