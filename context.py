"""
Request Context Management

Context variables are like thread-local storage but work correctly with async code.
When you set a context variable in a request handler, it's automatically available
to all code running within that request (including nested function calls, database
operations, etc.) without passing it explicitly.

Why not just pass request_id as a parameter everywhere?
- Would require changing function signatures across the entire codebase
- Doesn't work well with third-party libraries
- Context variables are the Python standard for this pattern

Default value '-' indicates "no request context" (e.g., during startup/shutdown)

Usage:
    from og_logger import set_request_context, clear_request_context, get_context
    
    set_request_context("req-123", "192.168.1.1", user_id="usr-456")
    # ... all logs now include request context ...
    clear_request_context()
"""
import contextvars

# Async-safe context variables scoped per request
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar('request_id', default='-')
client_ip_ctx: contextvars.ContextVar[str] = contextvars.ContextVar('client_ip', default='-')
extra_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar('extra_ctx', default={})


def set_request_context(request_id: str, client_ip: str = None, **extra) -> None:
    """
    Set request context that will be included in ALL logs during this request.
    
    Call this at the start of request processing (usually in middleware).
    The context is automatically scoped to the current async task, so concurrent
    requests don't interfere with each other.
    
    Args:
        request_id: Unique identifier for this request (for tracing)
        client_ip: Client's IP address
        **extra: Any additional fields to include (e.g., process_id, folder_id, user_id)
    
    Example:
        set_request_context("abc123", "192.168.1.1", process_id="proc_456")
        logger.info("Processing started")  # Automatically includes all context
    """
    request_id_ctx.set(request_id)
    if client_ip:
        client_ip_ctx.set(client_ip)
    if extra:
        # Filter out None values to keep logs clean
        extra_ctx.set({k: v for k, v in extra.items() if v is not None})


def clear_request_context() -> None:
    """
    Clear request context after request completes.
    
    Call this in a finally block to ensure cleanup even if request fails.
    This prevents context from one request leaking into another.
    """
    request_id_ctx.set('-')
    client_ip_ctx.set('-')
    extra_ctx.set({})


def get_context() -> dict:
    """
    Get current request context as a dictionary.
    
    Used internally by formatters to include context in every log message.
    Returns all context variables merged into a single dict.
    """
    ctx = {"request.id": request_id_ctx.get(), "client.ip": client_ip_ctx.get()}
    ctx.update(extra_ctx.get())  # Add any extra fields (process_id, folder_id, etc.)
    return ctx
