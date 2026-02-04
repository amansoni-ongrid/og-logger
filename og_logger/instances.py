"""
Logger instance management with lazy initialization.

Usage:
    from og_logger import get_logger, logger
    
    # Option 1: Get pre-configured logger (lazy-initialized on first access)
    logger.info("Hello")
    
    # Option 2: Explicit setup with custom config
    my_logger = get_logger(service_name="my-service", level="DEBUG")
"""
from typing import Optional
from loguru import logger as _loguru_logger

_configured_logger = None


def get_logger(
    service_name: str = None,
    level: str = None,
    json_output: bool = None,
    log_output: str = None,
    log_dir: str = None,
    max_mb: int = None,
    retention_count: int = None,
    retention_type: str = None,
    force_reconfigure: bool = False,
):
    """
    Get or create a configured logger instance.
    
    On first call, configures the logger with provided settings.
    Subsequent calls return the same instance unless force_reconfigure=True.
    
    Args:
        service_name: Service identifier for logs (default: "app")
        level: Log level - DEBUG, INFO, WARNING, ERROR (default: "INFO")
        json_output: Force JSON output (default: auto based on ENVIRONMENT)
        log_output: Where to log - "stdout", "file", "both" (default: auto)
        log_dir: Directory for log files (default: "logs")
        max_mb: Max file size before rotation (default: 15)
        retention_count: How many units to retain (default: 7)
        retention_type: "days", "hours", "weeks", or "files" (default: "days")
        force_reconfigure: If True, reconfigure even if already set up
    
    Returns:
        Configured loguru logger instance
    """
    global _configured_logger
    
    if _configured_logger is None or force_reconfigure:
        from .setup import setup_logger
        _configured_logger = setup_logger(
            service_name=service_name,
            level=level,
            json_output=json_output,
            log_output=log_output,
            log_dir=log_dir,
            max_mb=max_mb,
            retention_count=retention_count,
            retention_type=retention_type,
        )
    
    return _configured_logger


class _LazyLogger:
    """
    Proxy that initializes the logger on first use.
    
    This allows importing `logger` without triggering setup until
    an actual log call is made.
    """
    
    def __getattr__(self, name):
        return getattr(get_logger(), name)


# Lazy-initialized logger for convenience
# Won't configure until first log call
logger = _LazyLogger()
