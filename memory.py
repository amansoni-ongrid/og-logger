"""
Memory Monitoring for Request Logging

Provides memory consumption tracking per API request using tracemalloc.
Memory metrics are stored in context variables and automatically included in logs.

Features:
- Track memory allocated during a request
- Track peak memory usage during a request
- Current memory snapshot at each log point
- All metrics in MB for readability

Usage:
    from og_logger import RequestLoggingMiddleware
    
    app.add_middleware(
        RequestLoggingMiddleware,
        enable_memory_monitor=True,  # Enable memory tracking
    )

Note:
    tracemalloc adds some overhead (~5-10%). Consider enabling only when needed
    (debugging, profiling) rather than in high-throughput production environments.
"""
import tracemalloc
import contextvars
from typing import Optional, Dict, Any

# Context variables for memory tracking (async-safe, scoped per request)
_memory_enabled_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar(
    'memory_enabled', default=False
)
_memory_baseline_ctx: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    'memory_baseline', default=None
)
_memory_peak_ctx: contextvars.ContextVar[int] = contextvars.ContextVar(
    'memory_peak', default=0
)


def _bytes_to_mb(bytes_val: int) -> float:
    """Convert bytes to megabytes, rounded to 3 decimal places."""
    return round(bytes_val / (1024 * 1024), 3)


def start_memory_tracking() -> None:
    """
    Start memory tracking for the current request.
    
    Call this at the start of request processing. Takes a baseline snapshot
    so we can calculate memory allocated during the request.
    
    Note: tracemalloc.start() is idempotent - safe to call multiple times.
    """
    if not tracemalloc.is_tracing():
        tracemalloc.start()
    
    # Reset peak to current and take baseline
    tracemalloc.reset_peak()
    current, _ = tracemalloc.get_traced_memory()
    
    _memory_enabled_ctx.set(True)
    _memory_baseline_ctx.set(current)
    _memory_peak_ctx.set(current)


def stop_memory_tracking() -> Dict[str, float]:
    """
    Stop memory tracking and return final metrics.
    
    Returns:
        Dict with final memory metrics:
        - memory.allocated_mb: Total memory allocated during request
        - memory.peak_mb: Peak memory usage during request
        - memory.current_mb: Current memory at end of request
    """
    if not _memory_enabled_ctx.get():
        return {}
    
    current, peak = tracemalloc.get_traced_memory()
    baseline = _memory_baseline_ctx.get() or 0
    
    metrics = {
        "memory.allocated_mb": _bytes_to_mb(current - baseline),
        "memory.peak_mb": _bytes_to_mb(peak - baseline),
        "memory.current_mb": _bytes_to_mb(current),
    }
    
    # Clear context
    _memory_enabled_ctx.set(False)
    _memory_baseline_ctx.set(None)
    _memory_peak_ctx.set(0)
    
    return metrics


def get_memory_context() -> Dict[str, Any]:
    """
    Get current memory metrics for inclusion in logs.
    
    Called by formatters to include memory info in every log message
    when memory monitoring is enabled.
    
    Returns:
        Dict with current memory metrics, or empty dict if monitoring disabled.
    """
    if not _memory_enabled_ctx.get():
        return {}
    
    try:
        current, peak = tracemalloc.get_traced_memory()
        baseline = _memory_baseline_ctx.get() or 0
        
        return {
            "memory.allocated_mb": _bytes_to_mb(current - baseline),
            "memory.peak_mb": _bytes_to_mb(peak - baseline),
            "memory.current_mb": _bytes_to_mb(current),
        }
    except Exception:
        # tracemalloc not running or other error
        return {}


def is_memory_monitoring_enabled() -> bool:
    """Check if memory monitoring is currently enabled for this request."""
    return _memory_enabled_ctx.get()
