from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class RunControl:
    """Управление остановкой воркеров.

    Поле ``shutdown`` — это ``threading.Event`` (не путать с ``ThreadPoolExecutor.shutdown()``).
    """

    safe_stop: threading.Event = field(default_factory=threading.Event)
    shutdown: threading.Event = field(default_factory=threading.Event)
    pause: threading.Event = field(default_factory=threading.Event)

    def prepare_new_run(self) -> None:
        self.safe_stop.clear()
        self.shutdown.clear()
        self.pause.clear()


def interruptible_sleep(seconds: float, control: RunControl | None, slice_sec: float = 0.15) -> bool:
    """Спит до ``seconds`` сек или до ``control.shutdown``.
    Возвращает ``True``, если выход из-за shutdown."""
    if seconds <= 0:
        return False
    if control is None:
        time.sleep(seconds)
        return False
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if control.shutdown.is_set():
            return True
        slice_left = deadline - time.monotonic()
        if slice_left <= 0:
            break
        time.sleep(min(slice_sec, slice_left))
    return False
