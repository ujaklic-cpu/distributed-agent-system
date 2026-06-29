from __future__ import annotations

import json
from typing import Any

from redis import Redis

from orchestrator.config import settings


class RedisTaskQueue:
    def __init__(self, url: str = settings.queue_url) -> None:
        self.redis = Redis.from_url(url, decode_responses=True)
        self.key = "tasks.new"

    def enqueue(self, task_id: str, priority: int = 5) -> None:
        # Redis lists are enough for the MVP. Priority is persisted in DB and can be
        # replaced by sorted sets or Redis Streams in Phase 2.
        payload = json.dumps({"task_id": task_id, "priority": priority})
        self.redis.lpush(self.key, payload)

    def depth(self) -> int:
        return int(self.redis.llen(self.key))

    def get_next(self, timeout: int = 5) -> dict[str, Any] | None:
        item = self.redis.brpop(self.key, timeout=timeout)
        if not item:
            return None
        _, payload = item
        return json.loads(payload)
