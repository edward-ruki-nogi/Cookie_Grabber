from __future__ import annotations

import logging
import random
import time
from urllib.parse import urlsplit

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from cookie_grabber.behavior.browser_pages import close_extra_browser_pages
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
from gala_9static_proxy.validator import StopAfter9ProxyDialog

from cookie_grabber.farming.playwright_nav import (
    goto_bounded,
    is_chrome_error_page,
    is_stuck_launcher_url,
    page_on_entry_domain,
)
from cookie_grabber.farming.selectors import find_cookie_accept_candidates
from cookie_grabber.grabber_proxy import FarmingProxyHold

logger = logging.getLogger(__name__)

# Не крутить engagement на стартовой вкладке дольше этого после неудачного goto.
_LAUNCHER_STUCK_ABORT_SEC = 90.0
_FARM_STALL_ABORT_SEC = 90.0
_FARM_HEARTBEAT_SEC = 45.0
_MAX_ENTRY_GOTO_FAILURES = 5
_ENTRY_GOTO_ATTEMPTS = 3
_ENTRY_GOTO_RETRY_DELAY_SEC = 2.0
_POST_GOTO_LOAD_TIMEOUT_MS = 15_000


def _ensure_proxy_before_entry_fallback(
    proxy_hold: FarmingProxyHold | None,
    *,
    task_label: str | None,
    entry_url: str,
    control: RunControl | None = None,
) -> bool:
    """Проверка/активация прокси перед ``goto(entry_url)``. ``True`` — можно переходить."""
    if proxy_hold is None:
        return True
    if control is not None and control.shutdown.is_set():
        return False
    label = task_label or "нагул"
    logger.info(
        "Нагул [%s]: проверка прокси перед переходом на %s",
        label,
        entry_url,
    )
    try:
        ok = proxy_hold.allocator.ensure_proxy_valid(
            proxy_hold.acquisition,
            proxy_hold.account_name,
            control=control,
        )
    except StopAfter9ProxyDialog:
        raise
    except Exception as exc:
        logger.warning(
            "Нагул [%s]: ошибка проверки/активации прокси перед entry_url %s: %s",
            label,
            entry_url,
            exc,
        )
        return False
    if not ok:
        logger.warning(
            "Нагул [%s]: прокси не валиден после активации — переход на entry_url пропущен (%s)",
            label,
            entry_url,
        )
    return ok


def _goto_entry_with_retries(
    page: Page,
    entry_url: str,
    settings: AppSettings,
    control: RunControl | None,
    *,
    referer: str | None,
    task_label: str | None,
    proxy_hold: FarmingProxyHold | None,
) -> bool:
    """Переход на entry_url: проверка прокси и до нескольких повторов при сбое SOCKS/сети."""
    label = (task_label or "").strip() or None
    suffix = f" [{label}]" if label else ""

    for attempt in range(1, _ENTRY_GOTO_ATTEMPTS + 1):
        if control is not None and control.shutdown.is_set():
            return False

        if proxy_hold is not None:
            if not _ensure_proxy_before_entry_fallback(
                proxy_hold,
                task_label=label,
                entry_url=entry_url,
                control=control,
            ):
                if attempt < _ENTRY_GOTO_ATTEMPTS:
                    logger.info(
                        "Нагул%s: прокси не готов перед %s — повтор %s/%s",
                        suffix,
                        entry_url,
                        attempt,
                        _ENTRY_GOTO_ATTEMPTS,
                    )
                    if interruptible_sleep(_ENTRY_GOTO_RETRY_DELAY_SEC, control):
                        return False
                continue

        if goto_bounded(page, entry_url, settings, referer=referer):
            if is_chrome_error_page(page.url):
                logger.warning(
                    "Нагул%s: после goto осталась страница ошибки Chrome (%s) — %s",
                    suffix,
                    page.url,
                    entry_url,
                )
            elif page_on_entry_domain(page, entry_url):
                return True
            else:
                logger.warning(
                    "Нагул%s: goto завершился, но вкладка не на домене %s (URL: %s)",
                    suffix,
                    entry_url,
                    page.url,
                )

        if attempt >= _ENTRY_GOTO_ATTEMPTS:
            break
        logger.info(
            "Нагул%s: не удалось открыть %s (вкладка: %s) — повтор %s/%s",
            suffix,
            entry_url,
            page.url,
            attempt,
            _ENTRY_GOTO_ATTEMPTS,
        )
        if interruptible_sleep(_ENTRY_GOTO_RETRY_DELAY_SEC, control):
            return False

    return False


def _wait_page_settled(page: Page, settings: AppSettings, *, context: str) -> None:
    """Короткое ожидание DOM после goto (networkidle на «тяжёлых» сайтах часто не наступает)."""
    ms = min(_POST_GOTO_LOAD_TIMEOUT_MS, int(settings.navigation_timeout_ms))
    try:
        page.wait_for_load_state("domcontentloaded", timeout=ms)
    except PlaywrightTimeout:
        logger.debug("domcontentloaded timeout (%s) для %s", context, page.url)


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
    proxy_hold: FarmingProxyHold | None = None,
) -> float:
    """Сессия нагула на одном входном URL до исчерпания бюджета минут или остановки."""
    if minutes <= 0:
        return 0.0
    t0 = time.perf_counter()
    try:
        return farm_site_session(
            page,
            url,
            settings,
            control,
            minutes,
            referer=referer,
            task_label=task_label,
            proxy_hold=proxy_hold,
        )
    except StopAfter9ProxyDialog:
        raise
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
    proxy_hold: FarmingProxyHold | None = None,
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
    close_extra_browser_pages(page, task_label=label)

    if not _goto_entry_with_retries(
        page,
        entry_url,
        settings,
        control,
        referer=referer,
        task_label=label,
        proxy_hold=proxy_hold,
    ):
        logger.warning(
            "Нагул%s: не удалось открыть entry_url %s (URL вкладки: %s) — задание прервано",
            f" [{label}]" if label else "",
            entry_url,
            page.url,
        )
        return max(0.0, time.perf_counter() - t0)

    if control is not None and control.shutdown.is_set():
        return max(0.0, time.perf_counter() - t0)

    if not page_on_entry_domain(page, entry_url):
        logger.warning(
            "Нагул%s: вкладка не на целевом сайте %s (URL: %s) — задание прервано",
            f" [{label}]" if label else "",
            entry_url,
            page.url,
        )
        return max(0.0, time.perf_counter() - t0)

    if label:
        logger.info(
            "Нагул [%s]: открыт entry_url, фактический URL: %s",
            label,
            page.url,
        )
    else:
        logger.info("Нагул: открыт entry_url, фактический URL: %s", page.url)

    _wait_page_settled(page, settings, context="entry")

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
    entry_goto_failures = 0
    launcher_stuck_since: float | None = (
        time.monotonic() if is_stuck_launcher_url(page.url) else None
    )
    last_heartbeat = time.monotonic()
    stall_url = normalize_visit_url(page.url)
    stall_since = time.monotonic()
    while time.monotonic() < deadline:
        now_mono = time.monotonic()
        if now_mono - last_heartbeat >= _FARM_HEARTBEAT_SEC:
            if label:
                logger.info(
                    "Нагул [%s]: heartbeat — URL=%s, осталось %.0f с, переходов=%s",
                    label,
                    page.url,
                    max(0.0, deadline - now_mono),
                    nav,
                )
            else:
                logger.info(
                    "Нагул: heartbeat — URL=%s, осталось %.0f с, переходов=%s",
                    page.url,
                    max(0.0, deadline - now_mono),
                    nav,
                )
            last_heartbeat = now_mono

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

        loop_nav = nav
        loop_url = normalize_visit_url(page.url)

        if is_stuck_launcher_url(page.url):
            if launcher_stuck_since is None:
                launcher_stuck_since = time.monotonic()
            elif time.monotonic() - launcher_stuck_since >= _LAUNCHER_STUCK_ABORT_SEC:
                logger.warning(
                    "Нагул%s: вкладка остаётся на стартовой/пустой странице (%s) > %.0f с — выход",
                    f" [{label}]" if label else "",
                    page.url,
                    _LAUNCHER_STUCK_ABORT_SEC,
                )
                break
        else:
            launcher_stuck_since = None

        picked = pick_internal_navigation_locator(page, session)
        if picked is None and time.monotonic() - stall_since >= _FARM_STALL_ABORT_SEC:
            logger.warning(
                "Нагул%s: нет прогресса на %s > %.0f с (подбор ссылок) — выход",
                f" [{label}]" if label else "",
                stall_url or page.url,
                _FARM_STALL_ABORT_SEC,
            )
            break
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
            if not _goto_entry_with_retries(
                page,
                entry_url,
                settings,
                control,
                referer=referer,
                task_label=label,
                proxy_hold=proxy_hold,
            ):
                entry_goto_failures += 1
                if entry_goto_failures >= _MAX_ENTRY_GOTO_FAILURES:
                    logger.warning(
                        "Нагул%s: %s подряд неудачных переходов на entry_url — выход",
                        f" [{label}]" if label else "",
                        entry_goto_failures,
                    )
                    break
                continue
            entry_goto_failures = 0
            if control is not None and control.shutdown.is_set():
                break
            _wait_page_settled(page, settings, context="re-entry")
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

        cur_after = normalize_visit_url(page.url)
        if nav > loop_nav or cur_after != loop_url:
            stall_url = cur_after
            stall_since = time.monotonic()
        elif time.monotonic() - stall_since >= _FARM_STALL_ABORT_SEC:
            logger.warning(
                "Нагул%s: нет прогресса на %s > %.0f с — выход из задания",
                f" [{label}]" if label else "",
                stall_url or page.url,
                _FARM_STALL_ABORT_SEC,
            )
            break

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
    close_extra_browser_pages(page)

    if not goto_bounded(page, url, settings, referer=referer):
        return max(0.0, time.perf_counter() - t0)
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
