# og-logger

Structured JSON logging with async-safe request context for FastAPI/Starlette applications.

## Features

- **ECS-compatible JSON output** for production (works with ELK, Loki, CloudWatch)
- **Colored console output** for development
- **Async-safe request context** using `contextvars` - automatically includes `request_id`, `client_ip`, and custom fields in all logs
- **Configurable middleware** for FastAPI/Starlette with request/response logging
- **Automatic log rotation** by size and time
- **Zero configuration required** - sensible defaults with environment variable overrides

## Installation

```bash
pip install og-logger

# With middleware support (for FastAPI/Starlette)
pip install og-logger[middleware]
```

## Quick Start

### Basic Usage

```python
from og_logger import setup_logger

# Setup once at app startup
logger = setup_logger(service_name="my-api")

# Use anywhere
logger.info("User created", user_id=123)
logger.bind(order_id="abc").info("Order processed")
```

### With Request Context

```python
from og_logger import setup_logger, set_request_context, clear_request_context

logger = setup_logger()

# In your request handler or middleware
set_request_context(request_id="req-123", client_ip="192.168.1.1", user_id="usr-456")

logger.info("Processing order")  # Automatically includes request_id, client_ip, user_id

clear_request_context()  # Call in finally block
```

### FastAPI Middleware (Recommended)

```python
from fastapi import FastAPI
from og_logger import RequestLoggingMiddleware, setup_logger

app = FastAPI()
logger = setup_logger(service_name="my-api")

app.add_middleware(
    RequestLoggingMiddleware,
    context_fields=["user_id", "order_id"],  # Extract from query/body
    include_query_params=True,
    include_payload=True,
    payload_max_chars=100,
)

@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    logger.info("Fetching order")  # Includes request_id, client_ip, order_id
    return {"order_id": order_id}
```

## Configuration

### Via Arguments

```python
logger = setup_logger(
    service_name="my-api",      # Included in JSON logs as "service.name"
    level="DEBUG",              # DEBUG, INFO, WARNING, ERROR
    json_output=True,           # Force JSON (default: auto based on ENVIRONMENT)
    log_output="both",          # stdout, file, or both
    log_dir="logs",             # Directory for log files
    max_mb=15,                  # Max file size before rotation
    retention_count=7,          # How many to keep
    retention_type="days",      # days, hours, weeks, or files
)
```

### Via Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Minimum log level |
| `LOG_OUTPUT` | `stdout` (prod) / `both` (dev) | Where to send logs |
| `LOG_DIR` | `logs` | Directory for log files |
| `LOG_MAX_MB` | `15` | Max file size in MB |
| `LOG_RETENTION_COUNT` | `7` | Retention count |
| `LOG_RETENTION_TYPE` | `days` | Retention type |
| `JSON_LOGS` | auto | Force JSON output |
| `ENVIRONMENT` | `development` | Environment name |
| `SERVICE_NAME` | `app` | Service identifier |

## Output Formats

### Development (Colored Console)

```
2026-02-03 10:30:00 | INFO     | [req:abc123 | user:456] User logged in
2026-02-03 10:30:01 | ERROR    | [req:abc123 | user:456] Database error
```

### Production (JSON / ECS)

```json
{
  "@timestamp": "2026-02-03T10:30:00.000Z",
  "log.level": "INFO",
  "message": "User logged in",
  "service.name": "my-api",
  "service.environment": "production",
  "request.id": "abc123",
  "client.ip": "192.168.1.1",
  "user_id": "456"
}
```

## Multi-Worker / Container Deployments

**Important**: File logging is NOT safe when multiple processes write to the same file.

In production with multiple workers/containers:
1. Set `LOG_OUTPUT=stdout`
2. Let Docker/Kubernetes handle log aggregation
3. Forward to ELK, Loki, CloudWatch, etc.

```python
# Production config
logger = setup_logger(log_output="stdout", json_output=True)
```

## API Reference

### `setup_logger(**kwargs)`
Configure and return the loguru logger instance.

### `get_logger(**kwargs)`
Get or create a configured logger (lazy initialization).

### `set_request_context(request_id, client_ip=None, **extra)`
Set request context for all subsequent logs in the current async task.

### `clear_request_context()`
Clear request context (call in finally block).

### `get_context()`
Get current context as a dictionary.

### `RequestLoggingMiddleware`
Starlette/FastAPI middleware for automatic request/response logging.

## License

MIT
