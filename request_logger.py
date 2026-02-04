"""
Request Logging Middleware

Configurable middleware for logging HTTP requests/responses with context extraction.
Designed to be used as a library - all configuration is passed via constructor.

Usage:
    from og_logger import RequestLoggingMiddleware
    
    app.add_middleware(
        RequestLoggingMiddleware,
        context_fields=["process_id", "folder_id", "user_id"],
        include_query_params=True,
        include_payload=True,
        payload_max_chars=100,
        enable_memory_monitor=True,  # Track memory consumption per request
    )
"""
import time
import uuid
import json

from typing import List, Optional
from starlette.requests import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .context import set_request_context, clear_request_context
from .instances import logger
from .memory import start_memory_tracking, stop_memory_tracking


# =============================================================================
# Helper Functions
# =============================================================================

def _get_client_ip(request: Request) -> str:
    """Extract client IP from request (checks X-Forwarded-For for proxies)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _extract_context_fields(sources: List[dict], field_names: List[str]) -> dict:
    """
    Extract specified fields from multiple sources (query params, payload).
    
    Args:
        sources: List of dicts to search (e.g., [query_params, payload])
        field_names: List of field names to look for
    
    Returns:
        Dict of found fields with their values
    """
    context = {}
    for field in field_names:
        for source in sources:
            if source and field in source:
                context[field] = str(source[field])
                break  # Found it, move to next field
    return context


# =============================================================================
# Middleware
# =============================================================================

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for logging requests/responses with context extraction.
    
    Args:
        app: The ASGI application
        context_fields: List of field names to extract from query params/payload
                        and add to log context for tracing (e.g., ["process_id", "user_id"])
        include_query_params: Whether to log query params in request logs
        include_payload: Whether to log request payload in request logs
        payload_max_chars: Maximum characters to log from payload (truncates with "...")
        enable_memory_monitor: Whether to track memory consumption per request.
                                When enabled, each log includes memory.allocated_mb,
                                memory.peak_mb, and memory.current_mb fields.
                                Note: Adds ~5-10% overhead due to tracemalloc.
    
    Example:
        app.add_middleware(
            RequestLoggingMiddleware,
            context_fields=["process_id", "folder_id"],
            include_query_params=True,
            include_payload=True,
            payload_max_chars=100,
            enable_memory_monitor=True,
        )
    """
    
    def __init__(
        self,
        app,
        context_fields: Optional[List[str]] = None,
        include_query_params: bool = True,
        include_payload: bool = True,
        payload_max_chars: int = 100,
        enable_memory_monitor: bool = False,
    ):
        super().__init__(app)
        self.context_fields = context_fields or []
        self.include_query_params = include_query_params
        self.include_payload = include_payload
        self.payload_max_chars = payload_max_chars
        self.enable_memory_monitor = enable_memory_monitor

    async def dispatch(self, request: Request, call_next):
        request_id = uuid.uuid4().hex[:8]
        client_ip = _get_client_ip(request)
        method = request.method
        path = request.url.path

        # Start memory tracking if enabled
        if self.enable_memory_monitor:
            start_memory_tracking()

        # Parse query params
        query_params = dict(request.query_params) if request.query_params else {}

        # Parse JSON body for POST/PUT/PATCH
        payload = None
        body_bytes = b""
        if method in ("POST", "PUT", "PATCH"):
            try:
                body_bytes = await request.body()
                if body_bytes:
                    payload = json.loads(body_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            if body_bytes:
                async def receive():
                    return {"type": "http.request", "body": body_bytes}
                request = Request(request.scope, receive)

        # Extract configured context fields for tracing
        sources = [query_params, payload]
        extra_context = _extract_context_fields(sources, self.context_fields)

        # Set context for all logs in this request
        set_request_context(request_id, client_ip, **extra_context)

        # Build log extras
        extras = {"event_type": "request_start", "http.method": method, "http.path": path}
        if self.include_query_params and query_params:
            extras["http.query_params"] = query_params
        if self.include_payload and payload:
            payload_str = json.dumps(payload, ensure_ascii=False)
            extras["http.payload"] = payload_str[:self.payload_max_chars] + ("..." if len(payload_str) > self.payload_max_chars else "")

        try:
            # Log incoming request
            logger.bind(**extras).info(f"➡️  {method} {path}")

            start_time = time.time()
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000

            # Build response log extras
            resp_extras = {
                "event_type": "request_end",
                "http.method": method,
                "http.path": path,
                "http.status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            }
            
            # Add final memory metrics to response log
            if self.enable_memory_monitor:
                memory_metrics = stop_memory_tracking()
                resp_extras.update(memory_metrics)
            
            msg = f"⬅️  {response.status_code} in {duration_ms:.0f}ms"
            
            if response.status_code >= 500:
                logger.bind(**resp_extras).error(msg)
            elif response.status_code >= 400:
                logger.bind(**resp_extras).warning(msg)
            else:
                logger.bind(**resp_extras).info(msg)

            response.headers["x-request-id"] = request_id
            return response
        finally:
            # Ensure memory tracking is stopped even on error
            if self.enable_memory_monitor:
                stop_memory_tracking()
            clear_request_context()

