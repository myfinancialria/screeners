"""
Thread-safe rate limiter so the concurrent full-market scan stays within
FYERS API limits. A single shared limiter is used across all worker threads.
"""
import threading
import time


class RateLimiter:
    """Allow at most `rps` calls per second (min-interval token gate)."""

    def __init__(self, rps: float = 8.0):
        self._min_interval = 1.0 / rps
        self._lock = threading.Lock()
        self._next_at = 0.0

    def configure(self, rps: float):
        with self._lock:
            self._min_interval = 1.0 / rps

    def wait(self):
        with self._lock:
            now = time.monotonic()
            wait_for = self._next_at - now
            if wait_for > 0:
                time.sleep(wait_for)
                now = time.monotonic()
            self._next_at = max(now, self._next_at) + self._min_interval


# shared instance (default 8 req/s; scan_all may reconfigure)
limiter = RateLimiter(rps=8.0)
