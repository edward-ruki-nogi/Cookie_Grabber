from __future__ import annotations

import logging
import random
import time
from urllib.parse import urlsplit

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from cookie_grabber.behavior.cdp_mouse import cdp_click
from cookie_grabber.behavior.timing import random_action_delay
from cookie_grabber.config.settings import AppSettings
from cookie_grabber.control.runtime_control import RunControl, interruptible_sleep
from cookie_grabber.farming.engagement import (
    run_engagement_during_pause,
    simulate_reading_along_text,
)
from cookie_grabber.farming.navigation import (
    FarmingSessionState,
    normalize_visit_url,
    pick_internal_navigation_locator,
    registered_domain,
)
from cookie_grabber.farming.selectors import find_cookie_accept_candidates

logger = logging.getLogger(__name__)


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


def _cookie_banner_phase(page: Page, settings: AppSettings, control: RunControl | None) -> None:
    """Опрос кандидатов принятия cookie до таймаута или первого успешного клика."""
    timeout = max(0.0, float(settings.farming.cookie_banner_timeout_sec))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if control is not None:
            if control.shutdown.is_set():
                return
            while control.pause.is_set() and not control.shutdown.is_set():
                time.sleep(0.2)
            if control.shutdown.is_set():
                return
        for loc in find_cookie_accept_candidates(page):
            if control is not None and control.shutdown.is_set():
                return
            if _try_click_locator(page, loc, settings, control):
                random_action_delay(
                    settings.action_delay_sec_min * 0.4,
                    settings.action_delay_sec_max * 0.5,
                    control,
                )
                return
        if interruptible_sleep(0.35, control):
            return


def farm_single_url_for_minutes(
    page: Page,
    url: str,
    settings: AppSettings,
    control: RunControl | None,
    minutes: int,
    *,
    referer: str | None = None,
    task_label: str | None = None,
) -> float:
    """Сессия нагула на одном входном URL до исчерпания бюджета минут или остановки."""
    if minutes <= 0:
        return 0.0
    t0 = time.perf_counter()
    try:
        return farm_site_session(
            page, url, settings, control, minutes, referer=referer, task_label=task_label
        )
    except Exception as exc:
        logger.exception("farm_site_session %s: %s", url, exc)
        return max(0.0, time.perf_counter() - t0)


def farm_site_session(
    page: Page,
    entry_url: str,
    settings: AppSettings,
    control: RunControl | None,
    minutes: int,
    *,
    referer: str | None = None,
    task_label: str | None = None,
) -> float:
    """Один заход: goto → cookie (таймаут) → цикл внутренних переходов с engagement до дедлайна."""
    t0 = time.perf_counter()
    deadline = time.monotonic() + float(int(minutes)) * 60.0
    label = (task_label or "").strip() or None

    if control is not None and control.shutdown.is_set():
        return max(0.0, time.perf_counter() - t0)

    if label:
        logger.info(
            "Нагул [%s]: старт задания — entry_url=%s, бюджет=%s мин%s",
            label,
            entry_url,
            minutes,
            f", referer={referer}" if referer else "",
        )
    else:
        logger.info(
            "Нагул: старт задания — entry_url=%s, бюджет=%s мин%s",
            entry_url,
            minutes,
            f", referer={referer}" if referer else "",
        )

    try:
        page.bring_to_front()
    except Exception as exc:
        logger.debug("bring_to_front: %s", exc)

    goto_kwargs: dict = {
        "wait_until": "domcontentloaded",
        "timeout": settings.navigation_timeout_ms,
    }
    if referer:
        goto_kwargs["referer"] = referer
    page.goto(entry_url, **goto_kwargs)

    if control is not None and control.shutdown.is_set():
        return max(0.0, time.perf_counter() - t0)

    try:
        page.wait_for_load_state("networkidle", timeout=settings.network_idle_timeout_ms)
    except PlaywrightTimeout:
        logger.debug("networkidle timeout for %s", entry_url)

    random_action_delay(settings.action_delay_sec_min, settings.action_delay_sec_max, control)
    simulate_reading_along_text(page, settings, control)

    if control is not None and control.shutdown.is_set():
        return max(0.0, time.perf_counter() - t0)

    start_rd = registered_domain(page.url)
    start_netloc = urlsplit(page.url).netloc.lower()
    session = FarmingSessionState(
        start_registered_domain=start_rd,
        start_netloc=start_netloc,
        revisit_probability=float(settings.farming.revisit_url_probability),
    )
    session.note_visit(normalize_visit_url(page.url))

    _cookie_banner_phase(page, settings, control)

    nav = 0
    while time.monotonic() < deadline:
        if control is not None:
            if control.shutdown.is_set():
                break
            if control.safe_stop.is_set():
                break
            while control.pause.is_set() and not control.shutdown.is_set():
                time.sleep(0.2)
            if control.shutdown.is_set():
                break

        try:
            page.bring_to_front()
        except Exception:
            pass

        farm = settings.farming
        time_left = deadline - time.monotonic()
        if time_left <= 0.05:
            break
        pause_sec = random.uniform(farm.between_nav_delay_sec_min, farm.between_nav_delay_sec_max)
        pause_sec = min(pause_sec, max(0.2, time_left))
        engage_sec = min(pause_sec, float(farm.engagement_budget_sec_max))
        run_engagement_during_pause(page, settings, control, engage_sec)
        rest = pause_sec - engage_sec
        if rest > 0 and interruptible_sleep(rest, control):
            break

        if control is not None and (control.shutdown.is_set() or control.safe_stop.is_set()):
            break

        picked = pick_internal_navigation_locator(page, session)
        if picked is None:
            tl = max(0.0, deadline - time.monotonic())
            run_engagement_during_pause(
                page,
                settings,
                control,
                min(4.0, float(farm.engagement_budget_sec_max), max(0.1, tl)),
            )
            if control is not None and (control.shutdown.is_set() or control.safe_stop.is_set()):
                break
            tl2 = deadline - time.monotonic()
            if tl2 <= 0.15:
                break
            if label:
                logger.info(
                    "Нагул [%s]: нет подходящих внутренних ссылок (текущая страница: %s) — "
                    "переход на entry_url=%s",
                    label,
                    page.url,
                    entry_url,
                )
            else:
                logger.info(
                    "Нагул: нет подходящих внутренних ссылок (текущая страница: %s) — "
                    "переход на entry_url=%s",
                    page.url,
                    entry_url,
                )
            try:
                page.goto(
                    entry_url,
                    wait_until="domcontentloaded",
                    timeout=settings.navigation_timeout_ms,
                )
            except Exception as exc:
                if label:
                    logger.warning(
                        "Нагул [%s]: не удалось открыть entry_url %s: %s",
                        label,
                        entry_url,
                        exc,
                    )
                else:
                    logger.warning("Нагул: не удалось открыть entry_url %s: %s", entry_url, exc)
                continue
            if control is not None and control.shutdown.is_set():
                break
            try:
                page.wait_for_load_state("networkidle", timeout=settings.network_idle_timeout_ms)
            except PlaywrightTimeout:
                logger.debug("networkidle timeout после возврата на %s", entry_url)
            if label:
                logger.info(
                    "Нагул [%s]: открыт entry_url, фактический URL: %s",
                    label,
                    page.url,
                )
            else:
                logger.info("Нагул: открыт entry_url, фактический URL: %s", page.url)
            random_action_delay(
                settings.action_delay_sec_min * 0.25,
                settings.action_delay_sec_max * 0.45,
                control,
            )
            session.note_visit(normalize_visit_url(page.url))
            continue

        loc, _ = picked
        if not _try_click_locator(page, loc, settings, control):
            continue

        try:
            page.wait_for_load_state("domcontentloaded", timeout=settings.navigation_timeout_ms)
        except PlaywrightTimeout:
            pass
        random_action_delay(
            settings.action_delay_sec_min * 0.35,
            settings.action_delay_sec_max * 0.55,
            control,
        )
        session.note_visit(normalize_visit_url(page.url))
        nav += 1
        if nav % 5 == 0:
            try:
                page.bring_to_front()
            except Exception:
                pass

    return max(0.0, time.perf_counter() - t0)


def farm_single_url(
    page: Page,
    url: str,
    settings: AppSettings,
    control: RunControl | None = None,
    *,
    referer: str | None = None,
) -> float:
    """Один переход на URL: загрузка, короткое «чтение», фаза cookie (таймаут), без обхода по сайту."""
    t0 = time.perf_counter()
    if control is not None and control.shutdown.is_set():
        return max(0.0, time.perf_counter() - t0)

    try:
        page.bring_to_front()
    except Exception as exc:
        logger.debug("bring_to_front: %s", exc)

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
    simulate_reading_along_text(page, settings, control)

    if control is not None and control.shutdown.is_set():
        return max(0.0, time.perf_counter() - t0)

    _cookie_banner_phase(page, settings, control)
    random_action_delay(settings.action_delay_sec_min, settings.action_delay_sec_max, control)

    return max(0.0, time.perf_counter() - t0)
