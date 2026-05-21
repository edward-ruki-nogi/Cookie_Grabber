"""Playwright sync API в пуле воркеров: старт драйвера под lock, один экземпляр на поток.

``with sync_playwright()`` из нескольких ``ThreadPoolExecutor``-потоков одновременно
ломает инициализацию (``_playwright`` / Connection closed while reading from the driver).
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import Playwright, sync_playwright

logger = logging.getLogger(__name__)

# Сериализация start/stop node-драйвера Playwright между потоками.
_DRIVER_LOCK = threading.Lock()
_thread_state = threading.local()


@contextmanager
def thread_playwright_session() -> Iterator[Playwright]:
    """Один Playwright на поток воркера; ``start``/``stop`` под глобальным lock."""
    if getattr(_thread_state, "depth", 0) > 0:
        raise RuntimeError("thread_playwright_session: вложенный вызов в одном потоке")
    _thread_state.depth = 1
    pw: Playwright | None = None
    with _DRIVER_LOCK:
        manager = sync_playwright()
        try:
            pw = manager.start()
        except Exception:
            logger.exception(
                "Playwright: не удалось запустить драйвер (поток %s)",
                threading.current_thread().name,
            )
            raise
        _thread_state._manager = manager
        _thread_state._playwright = pw
        logger.info("Playwright: драйвер готов (%s)", threading.current_thread().name)
    try:
        yield pw
    finally:
        with _DRIVER_LOCK:
            try:
                if pw is not None:
                    pw.stop()
            except Exception as exc:
                logger.warning("Playwright stop: %s", exc)
            finally:
                _thread_state._playwright = None
                _thread_state._manager = None
                _thread_state.depth = 0
                logger.debug(
                    "Playwright: драйвер остановлен (поток %s)",
                    threading.current_thread().name,
                )
