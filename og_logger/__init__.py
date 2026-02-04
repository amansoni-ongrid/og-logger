"""
Structured JSON logging with request context for observability.

This package provides logging using loguru with:
- ECS-compatible JSON output for production
- Colored console output for development
- Automatic rotation by size (15MB) and date
- Request context injection
- Configurable request logging middleware
- Optional memory consumption monitoring per request

Usage:
    from og_logger import setup_logger, set_request_context, clear_request_context
    
    # Setup logger (call once at app startup)
    logger = setup_logger(service_name="my-service")
    
    # Basic logging
    logger.info("User created", user_id=123)
    
    # With extra fields
    logger.bind(user_id=123).info("User created")
    
    # In middleware - set context for all logs in the request
    set_request_context(request_id="abc123", client_ip="192.168.1.1")
    logger.info("Processing")  # Includes request_id and client_ip
    clear_request_context()

Middleware Usage (FastAPI/Starlette):
    from og_logger import RequestLoggingMiddleware
    
    app.add_middleware(
        RequestLoggingMiddleware,
        context_fields=["process_id", "folder_id", "user_id"],
        include_query_params=True,
        include_payload=True,
        payload_max_chars=100,
        enable_memory_monitor=True,  # Track memory per request
    )

Memory Monitoring:
    When enable_memory_monitor=True, each log includes:
    - memory.allocated_mb: Memory allocated since request started
    - memory.peak_mb: Peak memory usage during the request
    - memory.current_mb: Current memory snapshot
    
    Note: Memory monitoring uses tracemalloc and adds ~5-10% overhead.
"""

__version__ = "0.1.2"

from .context import set_request_context, clear_request_context, get_context
from .setup import setup_logger
from .instances import get_logger, logger
from .request_logger import RequestLoggingMiddleware
from .memory import (
    start_memory_tracking,
    stop_memory_tracking,
    get_memory_context,
    is_memory_monitoring_enabled,
)

__all__ = [
    # Version
    "__version__",
    # Context management
    "set_request_context",
    "clear_request_context",
    "get_context",
    # Setup
    "setup_logger",
    # Logger instance (lazy-initialized)
    "get_logger",
    "logger",
    # Middleware
    "RequestLoggingMiddleware",
    # Memory monitoring
    "start_memory_tracking",
    "stop_memory_tracking",
    "get_memory_context",
    "is_memory_monitoring_enabled",
]
