from __future__ import annotations

import logging
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from cookie_grabber.config.settings import AppSettings

logger = logging.getLogger(__name__)


def is_chrome_error_page(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith("chrome-error://") or "chromewebdata" in u


def is_proxy_network_goto_error(message: str) -> bool:
    """Ошибки навигации, при которых имеет смысл перепроверить/активировать прокси."""
    m = message or ""
    return any(
        token in m
        for token in (
            "ERR_SOCKS_CONNECTION_FAILED",
            "ERR_PROXY_CONNECTION_FAILED",
            "ERR_TUNNEL_CONNECTION_FAILED",
            "ERR_CONNECTION_REFUSED",
            "ERR_CONNECTION_RESET",
            "ERR_CONNECTION_TIMED_OUT",
            "ERR_NAME_NOT_RESOLVED",
            "ERR_INTERNET_DISCONNECTED",
            "ERR_NETWORK_CHANGED",
        )
    )


def is_stuck_launcher_url(url: str) -> bool:
    """Стартовая/пустая вкладка ADS или внутренние страницы Chromium — нагул ещё не начался."""
    if is_chrome_error_page(url):
        return True
    u = (url or "").strip().lower()
    if not u or u == "about:blank":
        return True
    if u.startswith("chrome://") or u.startswith("edge://") or u.startswith("devtools://"):
        return True
    if "start.adspower.net" in u:
        return True
    return False


def apply_page_timeouts(page: Page, settings: AppSettings) -> None:
    """Единые таймауты для CDP-сессии (после connect_over_cdp дефолты часто «бесконечные»)."""
    ms = max(1000, int(settings.navigation_timeout_ms))
    page.set_default_timeout(ms)
    page.set_default_navigation_timeout(ms)


def goto_bounded(
    page: Page,
    url: str,
    settings: AppSettings,
    *,
    referer: str | None = None,
    wait_until: str = "domcontentloaded",
) -> bool:
    """``page.goto`` в потоке воркера (Playwright sync API не переносится между потоками)."""
    nav_ms = max(1000, int(settings.navigation_timeout_ms))
    kwargs: dict[str, Any] = {"wait_until": wait_until, "timeout": nav_ms}
    if referer:
        kwargs["referer"] = referer
    try:
        page.goto(url, **kwargs)
        return True
    except PlaywrightTimeout:
        logger.warning("goto: Playwright timeout — %s, текущий URL: %s", url, page.url)
        return False
    except PlaywrightError as exc:
        msg = str(exc)
        # Прерванная навигация (редирект/новая вкладка): часто страница уже на целевом домене.
        if "ERR_ABORTED" in msg and not is_stuck_launcher_url(page.url):
            logger.debug("goto: ERR_ABORTED для %s, фактический URL: %s", url, page.url)
            return True
        if is_proxy_network_goto_error(msg):
            logger.warning(
                "goto: сеть/прокси — %s: %s (текущий URL: %s)",
                url,
                exc,
                page.url,
            )
        else:
            logger.warning("goto: ошибка — %s: %s (текущий URL: %s)", url, exc, page.url)
        return False
    except Exception as exc:
        logger.warning("goto: ошибка — %s: %s (текущий URL: %s)", url, exc, page.url)
        return False
