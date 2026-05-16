"""
Глобальное состояние бана активации прокси (2× Read timed out). Через multiprocessing Manager.
"""
import logging
import multiprocessing
import os
import time
from typing import Any, Optional

_logger = logging.getLogger(__name__)


class ValidationTimeoutState:
    """Длительность бана задаётся в __init__ (из config); классовая константа — только дефолт для совместимости."""
    BAN_DURATION_SEC = 30 * 60  # 30 минут (если не передали ban_duration_sec)

    def __init__(self, release_event: Any = None, ban_duration_sec: Optional[float] = None) -> None:
        self._count = multiprocessing.Value("i", 0)
        self._ban_until = multiprocessing.Value("d", 0.0)
        self._lock = multiprocessing.Lock()
        self._release_event = release_event
        self._ban_duration_sec = float(self.BAN_DURATION_SEC if ban_duration_sec is None else ban_duration_sec)

    def ban_duration_sec(self) -> float:
        return self._ban_duration_sec

    def record_timeout(self) -> bool:
        with self._lock:
            self._count.value += 1
            if self._count.value >= 2:
                if self._ban_duration_sec <= 0:
                    self._count.value = 0
                    return False
                self._ban_until.value = time.time() + self._ban_duration_sec
                return True
        return False

    def record_success(self) -> None:
        with self._lock:
            self._count.value = 0

    def is_banned(self) -> bool:
        with self._lock:
            if self._ban_until.value <= 0:
                return False
            if time.time() >= self._ban_until.value:
                self._ban_until.value = 0.0
                self._count.value = 0
                return False
            return True

    def remaining_ban_seconds(self) -> float:
        with self._lock:
            if self._ban_until.value <= 0:
                return 0.0
            remaining = self._ban_until.value - time.time()
            if remaining <= 0:
                self._ban_until.value = 0.0
                self._count.value = 0
                return 0.0
            return remaining

    def clear_ban_early(self) -> None:
        with self._lock:
            self._ban_until.value = 0.0
            self._count.value = 0
        if self._release_event is not None:
            try:
                self._release_event.set()
            except Exception:
                pass

    def wait_while_banned(self, cwd: str, acc_name: str = "") -> str:
        bad_p = os.path.join(cwd, "bad_end.flag")
        safe_p = os.path.join(cwd, "safe_stop.flag")
        while self.is_banned():
            if os.path.isfile(bad_p):
                _logger.warning(f"{acc_name}: bad_end при ожидании снятия бана прокси")
                return "bad_end"
            if os.path.isfile(safe_p):
                _logger.warning(f"{acc_name}: safe_stop при ожидании снятия бана прокси")
                return "safe_stop"
            remaining = self.remaining_ban_seconds()
            if remaining <= 0:
                return "unbanned"
            chunk = min(0.5, max(0.05, remaining))
            if self._release_event is not None:
                try:
                    if self._release_event.wait(timeout=chunk):
                        try:
                            self._release_event.clear()
                        except Exception:
                            pass
                except Exception:
                    time.sleep(chunk)
            else:
                time.sleep(chunk)
        return "unbanned"

    def wait_if_banned(self) -> None:
        while True:
            remaining = self.remaining_ban_seconds()
            if remaining <= 0:
                return
            _logger.warning(
                f"Валидация прокси заблокирована на {remaining:.0f} сек "
                f"(2 подряд Read timed out). Ожидание..."
            )
            time.sleep(min(remaining, 60))
