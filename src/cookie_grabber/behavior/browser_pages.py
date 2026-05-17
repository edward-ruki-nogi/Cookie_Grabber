from __future__ import annotations

import logging

from playwright.sync_api import Page

logger = logging.getLogger(__name__)


def close_extra_browser_pages(keep: Page, *, task_label: str | None = None) -> int:
    """Закрывает все вкладки/окна Playwright в браузере, кроме рабочей ``keep``."""
    try:
        browser = keep.context.browser
    except Exception:
        return 0
    if browser is None:
        return 0

    closed = 0
    for ctx in list(browser.contexts):
        for pg in list(ctx.pages):
            if pg is keep:
                continue
            try:
                if pg.is_closed():
                    continue
                url = pg.url
                pg.close()
                closed += 1
                logger.debug("Закрыта лишняя вкладка/окно: %s", url)
            except Exception as exc:
                logger.debug("close extra page skip: %s", exc)

    if closed:
        try:
            keep.bring_to_front()
        except Exception as exc:
            logger.debug("bring_to_front after close extras: %s", exc)
        prefix = f"Нагул [{task_label}]" if task_label else "Нагул"
        logger.info("%s: закрыто лишних вкладок/окон: %s", prefix, closed)
    return closed
