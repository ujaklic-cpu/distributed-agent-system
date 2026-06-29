from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol, cast


class RedisClientProtocol(Protocol):
    def incr(self, name: str) -> int: ...

    def expire(self, name: str, time: int) -> bool: ...

    def ttl(self, name: str) -> int: ...


try:
    redis_module: Any | None = importlib.import_module("redis")
except Exception:  # pragma: no cover - optional in some unit environments
    redis_module = None


@dataclass
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int


@dataclass
class InMemoryRateLimiter:
    _buckets: dict[str, tuple[int, float]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        now = time.time()
        with self._lock:
            count, reset_at = self._buckets.get(key, (0, now + window_seconds))
            if now >= reset_at:
                count, reset_at = 0, now + window_seconds
            count += 1
            self._buckets[key] = (count, reset_at)
            remaining = max(0, limit - count)
            return RateLimitDecision(count <= limit, limit, remaining, max(1, int(reset_at - now)))


class RedisRateLimiter:
    def __init__(self, redis_url: str, *, prefix: str = "rl") -> None:
        if redis_module is None:
            raise RuntimeError("redis package is not installed")
        self.client = cast(RedisClientProtocol, redis_module.Redis.from_url(redis_url, decode_responses=True))
        self.prefix = prefix

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        now_window = int(time.time() // window_seconds)
        redis_key = f"{self.prefix}:{key}:{now_window}"
        count = int(self.client.incr(redis_key))
        if count == 1:
            self.client.expire(redis_key, window_seconds + 2)
        ttl = self.client.ttl(redis_key)
        reset_after = ttl if ttl > 0 else window_seconds
        return RateLimitDecision(count <= limit, limit, max(0, limit - count), int(reset_after))
