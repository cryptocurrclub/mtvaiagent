"""Minimal JSON disk cache shared by price_api.py and staking_api.py."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class DiskCache:
    """JSON disk cache keyed by a string, with a TTL per entry."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return self.cache_dir / f"{safe}.json"

    def get(self, key: str, ttl_seconds: int) -> Optional[dict]:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - payload.get("_cached_at", 0) > ttl_seconds:
            return None
        return payload

    def get_stale(self, key: str) -> Optional[dict]:
        """Return cached data regardless of TTL - used as a last-resort fallback."""
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, key: str, data: dict) -> None:
        data = dict(data)
        data["_cached_at"] = time.time()
        try:
            self._path(key).write_text(json.dumps(data))
        except OSError as exc:
            logger.warning("Failed to write cache for %s: %s", key, exc)
