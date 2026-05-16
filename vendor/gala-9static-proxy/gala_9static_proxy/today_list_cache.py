"""
Общий кэш today list для всех воркеров (multiprocessing.Manager).
HTTP выполняется в воркерах; здесь только состояние под threading.Lock.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any, Dict, List, Optional


class TodayListCache:
    """Кэш ответа API today_list: TTL 5 мин, множество занятых id."""

    TTL_SEC = 300.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fetch_ts = 0.0
        self._records: List[Dict[str, Any]] = []
        self._claimed: set = set()
        self._did_2000_fetch = False

    def is_http_stale(self) -> bool:
        with self._lock:
            return self._fetch_ts == 0.0 or (time.time() - self._fetch_ts) >= self.TTL_SEC

    def apply_fetch(self, records: List[Dict[str, Any]], *, reset_window: bool) -> None:
        with self._lock:
            self._records = [dict(x) for x in records]
            self._fetch_ts = time.time()
            if reset_window:
                self._claimed.clear()
                self._did_2000_fetch = False

    def mark_2000_fetch_done(self) -> None:
        with self._lock:
            self._did_2000_fetch = True

    def should_fetch_2000(self) -> bool:
        with self._lock:
            return not self._did_2000_fetch

    def try_claim_random_eligible_id(self) -> Optional[str]:
        with self._lock:
            eligible: List[str] = []
            for r in self._records:
                try:
                    pid = r.get("id")
                    if not pid or pid in self._claimed:
                        continue
                    if r.get("binding") is not None:
                        continue
                    if str(r.get("country_code") or "").upper() != "GB":
                        continue
                    if r.get("is_online") is not True:
                        continue
                    eligible.append(str(pid))
                except Exception:
                    continue
            if not eligible:
                return None
            choice = random.choice(eligible)
            self._claimed.add(choice)
            return choice
