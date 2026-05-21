from __future__ import annotations

import logging
import random
import time

from playwright.sync_api import Page

from cookie_grabber.behavior.bezier import bezier_curve_points
from cookie_grabber.behavior.cdp_mouse import cdp_click, cdp_mouse_wheel, human_mouse_move, move_mouse_path
from cookie_grabber.behavior.timing import random_action_delay
from cookie_grabber.config.settings import AppSettings
from cookie_grabber.control.runtime_control import RunControl
from cookie_grabber.farming.selectors import is_ignored_interaction_label

logger = logging.getLogger(__name__)

_LOCATOR_OP_MS = 2500


def _shutdown(control: RunControl | None) -> bool:
    return control is not None and control.shutdown.is_set()


def simulate_reading_along_text(
    page: Page,
    settings: AppSettings,
    control: RunControl | None,
) -> None:
    try:
        if _shutdown(control):
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
            if _shutdown(control):
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


def human_scroll_burst(page: Page, control: RunControl | None, max_sec: float = 6.0) -> None:
    t0 = time.monotonic()
    steps = random.randint(5, 18)
    down = True
    for _ in range(steps):
        if _shutdown(control) or time.monotonic() - t0 > max_sec:
            return
        dy = random.uniform(40, 140) * (1 if down else -0.35)
        cdp_mouse_wheel(page, dy)
        if random.random() < 0.12:
            down = not down
        if control is not None:
            from cookie_grabber.control.runtime_control import interruptible_sleep

            if interruptible_sleep(random.uniform(0.04, 0.14), control):
                return
        else:
            time.sleep(random.uniform(0.04, 0.14))


def random_bezier_wander(page: Page, control: RunControl | None) -> None:
    if _shutdown(control):
        return
    vp = page.viewport_size or {"width": 1280, "height": 720}
    x = random.uniform(vp["width"] * 0.08, vp["width"] * 0.92)
    y = random.uniform(vp["height"] * 0.08, vp["height"] * 0.92)
    try:
        human_mouse_move(page, x, y)
    except Exception as exc:
        logger.debug("wander skip: %s", exc)


def _hover_and_maybe_safe_click(
    page: Page,
    settings: AppSettings,
    control: RunControl | None,
) -> None:
    if _shutdown(control):
        return
    loc = page.locator(
        "button:visible, input:visible, [role='checkbox']:visible, [role='switch']:visible"
    )
    try:
        n = min(loc.count(), 45)
    except Exception:
        n = 0
    if n <= 0:
        return
    el = loc.nth(random.randint(0, n - 1))
    try:
        if not el.is_visible(timeout=800):
            return
        label = (el.inner_text(timeout=500) or "").strip()
        if is_ignored_interaction_label(label):
            return
    except Exception:
        return
    try:
        safe = el.evaluate(
            """(el) => {
                const tag = el.tagName.toLowerCase();
                const type = (el.getAttribute('type') || '').toLowerCase();
                if (type === 'submit' || type === 'password' || type === 'file' || type === 'image') return 0;
                const href = (el.getAttribute('formaction') || '').toLowerCase();
                if (href.includes('logout') || href.includes('delete') || href.includes('join')) return 0;
                const label = ((el.innerText || el.textContent || '') + '').toLowerCase();
                if (/\\bjoin\\b/.test(label)) return 0;
                if (tag === 'button') return (type === 'button' || type === '' || type === 'reset') ? 2 : 1;
                if (tag === 'input') return ['checkbox','radio','range','button'].includes(type) ? 2 : 1;
                if (el.getAttribute('role') === 'switch') return 2;
                return 1;
            }""",
            timeout=_LOCATOR_OP_MS,
        )
    except Exception:
        return
    try:
        el.hover(timeout=_LOCATOR_OP_MS)
    except Exception:
        return
    random_action_delay(
        settings.action_delay_sec_min * 0.25,
        settings.action_delay_sec_max * 0.35,
        control,
    )
    if _shutdown(control):
        return
    if safe == 2 and random.random() < 0.14:
        try:
            box = el.bounding_box()
            if box:
                x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
                y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
                cdp_click(page, x, y)
                random_action_delay(
                    settings.action_delay_sec_min * 0.2,
                    settings.action_delay_sec_max * 0.3,
                    control,
                )
        except Exception as exc:
            logger.debug("safe click skip: %s", exc)


def run_engagement_during_pause(
    page: Page,
    settings: AppSettings,
    control: RunControl | None,
    duration_sec: float,
) -> None:
    """Заполняет паузу «живыми» действиями до ``duration_sec`` wall-clock (с учётом shutdown)."""
    if duration_sec <= 0:
        return
    t_end = time.perf_counter() + duration_sec
    actions = (
        lambda: human_scroll_burst(page, control, max_sec=min(8.0, duration_sec)),
        lambda: simulate_reading_along_text(page, settings, control),
        lambda: random_bezier_wander(page, control),
        lambda: _hover_and_maybe_safe_click(page, settings, control),
    )
    while time.perf_counter() < t_end:
        if _shutdown(control):
            return
        if control is not None and control.safe_stop.is_set():
            return
        if control is not None:
            while control.pause.is_set() and not control.shutdown.is_set():
                time.sleep(0.2)
            if control.shutdown.is_set():
                return
        random.choice(actions)()
        if random_action_delay(
            settings.action_delay_sec_min * 0.15,
            settings.action_delay_sec_max * 0.35,
            control,
        ):
            return
