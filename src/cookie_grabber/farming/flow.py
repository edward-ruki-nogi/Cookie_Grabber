from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from cookie_grabber.behavior.bezier import bezier_curve_points
from cookie_grabber.behavior.cdp_mouse import cdp_click, human_mouse_move, move_mouse_path
from cookie_grabber.behavior.timing import random_action_delay
from cookie_grabber.config.settings import AppSettings
from cookie_grabber.farming.selectors import (
    find_cookie_accept_candidates,
    find_inner_navigation_candidates,
)

if TYPE_CHECKING:
    from cookie_grabber.control.runtime_control import RunControl

logger = logging.getLogger(__name__)


def _simulate_reading_along_text(
    page: Page,
    settings: AppSettings,
    control: RunControl | None,
) -> None:
    try:
        if control is not None and control.shutdown.is_set():
            return
        loc = page.locator("p, article, main")
        n = min(loc.count(), 12)
        if n == 0:
            return
        el = loc.nth(random.randint(0, n - 1))
        try:
            txt = el.inner_text(timeout=800) or ""
        except Exception:
            txt = ""
        if len(txt) < 40:
            return
        box = el.bounding_box()
        if not box:
            return
        lines = random.randint(2, 4)
        for ln in range(lines):
            if control is not None and control.shutdown.is_set():
                return
            y = box["y"] + box["height"] * (0.15 + 0.2 * ln)
            x0 = box["x"] + box["width"] * 0.1
            x1 = box["x"] + box["width"] * 0.85
            pts = bezier_curve_points(x0, y, x1, y, steps=random.randint(12, 22))
            move_mouse_path(page, pts)
            if random_action_delay(
                settings.action_delay_sec_min * 0.3,
                settings.action_delay_sec_max * 0.4,
                control,
            ):
                return
    except Exception as exc:
        logger.debug("reading sim skipped: %s", exc)


def _try_click_locator(
    page: Page,
    loc,
    settings: AppSettings,
    control: RunControl | None,
) -> bool:
    try:
        if control is not None and control.shutdown.is_set():
            return False
        if not loc.is_visible(timeout=1500):
            return False
        box = loc.bounding_box()
        if not box:
            return False
        x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
        y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
        cdp_click(page, x, y)
        random_action_delay(settings.action_delay_sec_min, settings.action_delay_sec_max, control)
        return True
    except Exception as exc:
        logger.debug("click skip: %s", exc)
        return False


def farm_single_url(
    page: Page,
    url: str,
    settings: AppSettings,
    control: RunControl | None = None,
    *,
    referer: str | None = None,
) -> float:
    t0 = time.perf_counter()
    if control is not None and control.shutdown.is_set():
        return max(0.0, time.perf_counter() - t0)

    goto_kwargs: dict = {
        "wait_until": "domcontentloaded",
        "timeout": settings.navigation_timeout_ms,
    }
    if referer:
        goto_kwargs["referer"] = referer
    page.goto(url, **goto_kwargs)
    if control is not None and control.shutdown.is_set():
        return max(0.0, time.perf_counter() - t0)

    try:
        page.wait_for_load_state("networkidle", timeout=settings.network_idle_timeout_ms)
    except PlaywrightTimeout:
        logger.debug("networkidle timeout for %s", url)

    random_action_delay(settings.action_delay_sec_min, settings.action_delay_sec_max, control)
    _simulate_reading_along_text(page, settings, control)

    if control is not None and control.shutdown.is_set():
        return max(0.0, time.perf_counter() - t0)

    for loc in find_cookie_accept_candidates(page):
        if control is not None and control.shutdown.is_set():
            return max(0.0, time.perf_counter() - t0)
        if _try_click_locator(page, loc, settings, control):
            break

    random_action_delay(settings.action_delay_sec_min, settings.action_delay_sec_max, control)

    if control is not None and control.shutdown.is_set():
        return max(0.0, time.perf_counter() - t0)

    cands = find_inner_navigation_candidates(page)
    if cands:
        choice = random.choice(cands)
        _try_click_locator(page, choice, settings, control)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=settings.navigation_timeout_ms)
        except PlaywrightTimeout:
            pass
        random_action_delay(settings.action_delay_sec_min, settings.action_delay_sec_max, control)

    return max(0.0, time.perf_counter() - t0)
