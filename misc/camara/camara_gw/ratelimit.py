"""Per-client rate limit (sliding 60 s window) and optional daily quota.

Exceeding the rate -> 429 TOO_MANY_REQUESTS; exceeding the daily quota -> 429 QUOTA_EXCEEDED.
Checked for every authenticated API call (auth/tokens.py). In-memory: resets on restart.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from . import errors
from .config import RateLimit


class Limiter:
    def __init__(self, cfg: RateLimit):
        self.cfg = cfg
        self._win: dict[str, deque] = defaultdict(deque)
        self._day: dict[str, tuple[str, int]] = {}

    def check(self, client_id: str) -> None:
        if not self.cfg.enabled:
            return
        now = time.monotonic()
        win = self._win[client_id]
        while win and now - win[0] > 60:
            win.popleft()
        if len(win) >= self.cfg.per_minute:
            raise errors.CamaraError(429, "TOO_MANY_REQUESTS", "Rate limit reached; retry later.")
        if self.cfg.daily_quota:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            day, n = self._day.get(client_id, (today, 0))
            if day != today:
                n = 0
            if n >= self.cfg.daily_quota:
                raise errors.CamaraError(429, "QUOTA_EXCEEDED", "Daily API quota for this client is exhausted.")
            self._day[client_id] = (today, n + 1)
        win.append(now)
