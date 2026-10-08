"""
In-memory sliding-window rate limiter for heavy endpoints.

Usage:
    from backend.api.rate_limit import rate_limit

    @router.post("/run")
    def run_backtest(request: Request, ...):
        rate_limit(request, max_requests=5, window_seconds=60)
        ...

Raises HTTPException(429) if the limit is exceeded.
No external dependencies — uses a simple dict + deque.
"""
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

# Bypass rate limiting during tests (TESTING=1 set by pytest or testclient)
_TESTING = os.environ.get("TESTING", "").lower() in ("1", "true", "yes")

# endpoint → { client_ip: deque_of_timestamps }
_buckets: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
_call_count = 0
_CLEANUP_INTERVAL = 100


def _sweep_empty_buckets() -> None:
    """Remove empty deques so _buckets doesn't grow unbounded with unique IPs."""
    for bucket_key in list(_buckets):
        ip_map = _buckets[bucket_key]
        for ip in [k for k, v in ip_map.items() if not v]:
            del ip_map[ip]
        if not ip_map:
            del _buckets[bucket_key]


def rate_limit(
    request: Request,
    max_requests: int = 5,
    window_seconds: int = 60,
    key: str | None = None,
) -> None:
    """Check rate limit; raise 429 if exceeded.

    Args:
        request: FastAPI Request object (for client IP).
        max_requests: Max allowed requests in the window.
        window_seconds: Window size in seconds.
        key: Optional override for the bucket key (defaults to route path).
    """
    # Skip rate limiting during tests (env var or Starlette TestClient)
    client_host = request.client.host if request.client else "unknown"
    if _TESTING or client_host == "testclient":
        return

    global _call_count
    _call_count += 1
    if _call_count % _CLEANUP_INTERVAL == 0:
        _sweep_empty_buckets()

    bucket_key = key or request.url.path
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    timestamps = _buckets[bucket_key][client_ip]

    # Evict expired entries
    while timestamps and timestamps[0] <= now - window_seconds:
        timestamps.popleft()

    if len(timestamps) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — max {max_requests} requests per {window_seconds}s. Try again shortly.",
        )

    timestamps.append(now)
