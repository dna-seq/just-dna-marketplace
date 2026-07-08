"""
In-memory token-bucket rate limiting (SPEC §7). Per-caller (API key if present, else client IP)
× category. MVP: process-local, no external store — good enough for a single-instance deployment;
swap for Redis buckets when horizontally scaled.
"""

import threading
import time


class RateLimiter:
    """Token buckets keyed by (identity, category). `limits[category] = (capacity, refill_per_sec)`."""

    def __init__(self, limits: dict[str, tuple[float, float]], enabled: bool = True) -> None:
        self.limits = limits
        self.enabled = enabled
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, identity: str, category: str) -> bool:
        if not self.enabled or category not in self.limits:
            return True
        capacity, refill = self.limits[category]
        now = time.monotonic()
        with self._lock:
            tokens, updated = self._buckets.get((identity, category), (capacity, now))
            tokens = min(capacity, tokens + (now - updated) * refill)
            if tokens < 1.0:
                self._buckets[(identity, category)] = (tokens, now)
                return False
            self._buckets[(identity, category)] = (tokens - 1.0, now)
            return True


def default_limiter(settings) -> RateLimiter:
    """Build a limiter from settings: publish 10/h, download 1000/h, search 60/min, social 30/min."""
    return RateLimiter(
        limits={
            "publish": (settings.rate_publish_per_hour, settings.rate_publish_per_hour / 3600.0),
            "download": (settings.rate_download_per_hour, settings.rate_download_per_hour / 3600.0),
            "search": (settings.rate_search_per_min, settings.rate_search_per_min / 60.0),
            "social": (settings.rate_social_per_min, settings.rate_social_per_min / 60.0),
        },
        enabled=settings.rate_limit_enabled,
    )
