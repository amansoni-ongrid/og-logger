# og-logger

Structured JSON logging with async-safe request context for FastAPI/Starlette applications.

## Features

- **ECS-compatible JSON output** for production (works with ELK, Loki, CloudWatch)
- **Colored console output** for development
- **Async-safe request context** using `contextvars` - automatically includes `request_id`, `client_ip`, and custom fields in all logs
- **Configurable middleware** for FastAPI/Starlette with request/response logging
- **Memory monitoring** - optional per-request memory consumption tracking with peak usage
- **Automatic log rotation** by size and time
- **Zero configuration required** - sensible defaults with environment variable overrides

## Installation

This is a private package. Make sure you have access to the repository.

### Using pip

```bash
# Install latest from main branch
pip install git+https://github.com/amansoni-ongrid/og-logger.git

# Install specific version (recommended)
pip install git+https://github.com/amansoni-ongrid/og-logger.git@v0.1.0

# With middleware support
pip install "og-logger[middleware] @ git+https://github.com/amansoni-ongrid/og-logger.git@v0.1.0"
```

### Using uv

```bash
# Install latest from main branch
uv pip install git+https://github.com/amansoni-ongrid/og-logger.git

# Install specific version (recommended)
uv pip install git+https://github.com/amansoni-ongrid/og-logger.git@v0.1.0

# With middleware support
uv pip install "og-logger[middleware] @ git+https://github.com/amansoni-ongrid/og-logger.git@v0.1.0"
```

### In pyproject.toml (for uv sync / pip install)

Add to your project's `pyproject.toml`:

```toml
[project]
dependencies = [
    "og-logger @ git+https://github.com/amansoni-ongrid/og-logger.git@v0.1.0",
]

# Or with middleware extra:
# "og-logger[middleware] @ git+https://github.com/amansoni-ongrid/og-logger.git@v0.1.0",
```

Then run:

```bash
# With uv
uv sync

# With pip
pip install -e .
```

### In requirements.txt

```
og-logger @ git+https://github.com/amansoni-ongrid/og-logger.git@v0.1.0
```

### Using SSH (if you have SSH keys configured)

```bash
pip install git+ssh://git@github.com/amansoni-ongrid/og-logger.git@v0.1.0
uv pip install git+ssh://git@github.com/amansoni-ongrid/og-logger.git@v0.1.0
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
    enable_memory_monitor=True,  # Track memory per request
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

## Memory Monitoring

Track memory consumption per API request by enabling the `enable_memory_monitor` flag:

```python
app.add_middleware(
    RequestLoggingMiddleware,
    enable_memory_monitor=True,
)
```

When enabled, every log during a request includes:

| Field | Description |
|-------|-------------|
| `memory.allocated_mb` | Memory allocated since request started |
| `memory.peak_mb` | Peak memory usage during the request |
| `memory.current_mb` | Current memory snapshot |

### Example Output (JSON)

```json
{
  "@timestamp": "2026-02-03T10:30:00.000Z",
  "log.level": "INFO",
  "message": "⬅️  200 in 45ms",
  "request.id": "abc123",
  "http.status_code": 200,
  "duration_ms": 45.23,
  "memory.allocated_mb": 2.451,
  "memory.peak_mb": 3.892,
  "memory.current_mb": 15.234
}
```

### Manual Memory Tracking

For custom use cases outside the middleware:

```python
from og_logger import start_memory_tracking, stop_memory_tracking, get_memory_context

# Start tracking
start_memory_tracking()

# ... your code ...
logger.info("Processing", **get_memory_context())  # Include current memory in log

# Stop and get final metrics
metrics = stop_memory_tracking()
# {'memory.allocated_mb': 1.234, 'memory.peak_mb': 2.345, 'memory.current_mb': 10.567}
```

### Performance Note

Memory monitoring uses Python's `tracemalloc` module which adds approximately 5-10% overhead. Consider enabling it only when needed (debugging, profiling, specific endpoints) rather than globally in high-throughput production environments.

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

### `start_memory_tracking()`
Start memory tracking for the current request/context.

### `stop_memory_tracking()`
Stop tracking and return final memory metrics as a dict.

### `get_memory_context()`
Get current memory metrics for inclusion in logs.

### `is_memory_monitoring_enabled()`
Check if memory monitoring is active for the current context.

## License

MIT
