from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cookie_grabber.control.runtime_control import RunControl


def random_action_delay(
    min_sec: float, max_sec: float, control: RunControl | None = None
) -> bool:
    """Случайная пауза. Возвращает ``True``, если завершено досрочно из‑за ``control.shutdown``."""
    dur = random.uniform(min_sec, max_sec)
    if dur <= 0:
        return False
    if control is None:
        time.sleep(dur)
        return False
    from cookie_grabber.control.runtime_control import interruptible_sleep

    return interruptible_sleep(dur, control)
