"""Воркер аккаунта: цикл по строкам через A2 + сценарий нагула.

Реализует алгоритм:
1. acquire_next_row_index → N
2. ветвление по статусу F{N}
3. при «Ожидает» (status_values.empty) — сначала прокси, затем ADS create_profile с ``user_proxy_config`` и именем «{имя} - {порт}», запись G{N}
4. прокси (ProxyAllocator), update_profile_proxy (прокси + то же имя), start_browser
5. PP / WH / Kwiff (по флагам important_sites[*].enabled) — сессия нагула ``farm_site_session`` на бюджет
   ``farming.important_sites_minutes_per_site`` мин.; массовка — ``farming.mass_sites_minutes_per_site`` и
   ``farming.mass_sites_count`` (0 = все строки файла)
6. commit_session_row: накопительно M/N/O/K/E, +D, B, C, F=Создан
7. stop_browser, allocator.release

"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from playwright.sync_api import Playwright
from playwright_cookie_blocker import block_cookie_dialogs

from gala_9static_proxy.validator import StopAfter9ProxyDialog

from cookie_grabber.ads.ads_power_api import AdsPowerApi
from cookie_grabber.browser_window import (
    format_layout_cell,
    launch_args_for_layout,
    resolve_launch_layout,
)
from cookie_grabber.behavior.cdp_mouse import install_synthetic_mouse_overlay
from cookie_grabber.config.settings import AppSettings, ImportantSite
from cookie_grabber.control.runtime_control import RunControl, interruptible_sleep
from cookie_grabber.log_bus import set_worker_panel_label
from cookie_grabber.farming.flow import farm_single_url_for_minutes
from cookie_grabber.farming.playwright_nav import apply_page_timeouts
from cookie_grabber.grabber_proxy import FarmingProxyHold, ProxyAllocator, format_ads_profile_name
from cookie_grabber.playwright_thread import thread_playwright_session
from cookie_grabber.sheets.cell_text import labels_equal, normalize_sheet_cell_scalar
from cookie_grabber.sheets.google_sheets_api import GoogleSheetsApi

logger = logging.getLogger(__name__)


def _profile_quota_should_release_reservation(result: str) -> bool:
    """Строка выдана, но реальную работу с профилем не начинали — вернуть слот лимита."""
    if result == "reset_counter":
        return True
    if result == "skip_unknown_status":
        return True
    return result.startswith("skip_")


def _wait_if_paused(control: RunControl) -> bool:
    while control.pause.is_set() and not control.shutdown.is_set():
        if control.safe_stop.is_set():
            return True
        time.sleep(0.2)
    return control.shutdown.is_set() or control.safe_stop.is_set()


def _skip_new_farming_tasks(control: RunControl) -> bool:
    """Не начинать следующее задание нагула (shutdown, safe_stop или выход из паузы с флагом)."""
    if control.shutdown.is_set() or control.safe_stop.is_set():
        return True
    return _wait_if_paused(control)


def _close_all_browser_tabs(browser: Any) -> int:
    """Закрывает все вкладки во всех контекстах Playwright перед отключением от браузера."""
    closed = 0
    for ctx in list(browser.contexts):
        for pg in list(ctx.pages):
            try:
                if not pg.is_closed():
                    pg.close()
                    closed += 1
            except Exception as exc:
                logger.debug("не удалось закрыть вкладку: %s", exc)
    return closed


def _restore_row_status(sheets: GoogleSheetsApi, row: int, initial_status: str) -> None:
    """Вернуть статус строки к значению на момент начала обработки аккаунта."""
    if not initial_status:
        return
    try:
        sheets.write_status(row, initial_status)
    except Exception:
        logger.exception("не удалось восстановить статус %r для row=%s", initial_status, row)


def _rollback_account_on_failure(
    sheets: GoogleSheetsApi,
    ads: AdsPowerApi,
    row: int,
    *,
    initial_status: str,
    started_from_empty: bool,
    profile_id: str | None,
    browser_was_started: bool,
) -> None:
    """Откат после ошибки: для «Ожидает» — stop/delete ADS и очистка G, затем исходный статус."""
    pid = (profile_id or "").strip()
    if browser_was_started and pid:
        try:
            ads.stop_browser(pid)
        except Exception as exc:
            logger.warning("ADS stop при откате row=%s: %s", row, exc)
    if started_from_empty and pid:
        try:
            ads.delete_profile(pid)
            logger.info("ADS profile удалён (откат «Ожидает»): row=%s id=%s", row, pid)
        except Exception as exc:
            logger.warning("ADS delete_profile row=%s id=%s: %s", row, pid, exc)
        try:
            sheets.write_profile_id(row, "")
        except Exception:
            logger.exception("не удалось очистить ads_profile_id для row=%s", row)
    _restore_row_status(sheets, row, initial_status)


def _important_by_id(settings: AppSettings, site_id: str) -> ImportantSite | None:
    for s in settings.important_sites:
        if s.id == site_id:
            return s
    return None


def _load_mass_urls(settings: AppSettings) -> list[str]:
    path = settings.resolve(settings.mass_sites_file)
    if not path.is_file():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def _farm_important(
    page: Any,
    settings: AppSettings,
    control: RunControl,
    site_id: str,
    proxy_hold: FarmingProxyHold | None = None,
) -> float:
    """Запуск важного сайта, возвращает прожитое wall-clock время (сек)."""
    site = _important_by_id(settings, site_id)
    if site is None or not site.enabled:
        return 0.0
    if _skip_new_farming_tasks(control):
        return 0.0
    referer = site.referer or "https://www.google.com/"
    minutes = settings.farming.important_sites_minutes_per_site
    if minutes <= 0:
        return 0.0
    logger.info(
        "Задание: важный сайт %s — %s (%s мин)",
        site_id,
        site.url,
        minutes,
    )
    try:
        return farm_single_url_for_minutes(
            page,
            site.url,
            settings,
            control,
            minutes,
            referer=referer,
            task_label=f"важный:{site_id}",
            proxy_hold=proxy_hold,
        )
    except StopAfter9ProxyDialog:
        raise
    except Exception as exc:
        logger.exception("farm важный %s (%s) ошибка: %s", site_id, site.url, exc)
        return 0.0


def _farm_mass(
    page: Any,
    settings: AppSettings,
    control: RunControl,
    proxy_hold: FarmingProxyHold | None = None,
) -> float:
    minutes = settings.farming.mass_sites_minutes_per_site
    if minutes <= 0:
        return 0.0
    if _skip_new_farming_tasks(control):
        return 0.0
    urls = _load_mass_urls(settings)
    random.shuffle(urls)
    cap = settings.farming.mass_sites_count
    if cap > 0:
        urls = urls[:cap]
    if not urls:
        return 0.0
    t0 = time.perf_counter()
    for url in urls:
        if control.shutdown.is_set() or control.safe_stop.is_set():
            break
        if _wait_if_paused(control):
            break
        logger.info("Задание: массовка — %s (%s мин)", url, minutes)
        try:
            farm_single_url_for_minutes(
                page,
                url,
                settings,
                control,
                minutes,
                task_label="массовка",
                proxy_hold=proxy_hold,
            )
        except StopAfter9ProxyDialog:
            raise
        except Exception as exc:
            logger.exception("farm массовка %s ошибка: %s", url, exc)
    return max(0.0, time.perf_counter() - t0)


def _ensure_profile(
    settings: AppSettings,
    sheets: GoogleSheetsApi,
    ads: AdsPowerApi,
    row: int,
    account_name: str,
    *,
    create_if_missing: bool,
    known_profile_id: str | None = None,
    user_proxy_config: dict[str, Any] | None = None,
    profile_name: str | None = None,
) -> str | None:
    pid = (known_profile_id or "").strip()
    if not pid:
        pid = sheets.read_ads_profile_id(row).strip()
    if pid:
        return pid
    if not create_if_missing:
        return None
    group_id = settings.ads_power.signup_group_id.strip()
    if not group_id:
        logger.error("ads_power.signup_group_id не задан — не могу создать профиль для строки %s", row)
        return None
    try:
        new_pid = ads.create_profile(
            group_id,
            name=profile_name or account_name or None,
            user_proxy_config=user_proxy_config,
        )
    except Exception as exc:
        logger.exception("ADS create_profile failed for row %s (%s): %s", row, account_name, exc)
        return None
    sheets.write_profile_id(row, new_pid)
    logger.info(
        "ADS profile создан: row=%s name=%s id=%s",
        row,
        profile_name or account_name,
        new_pid,
    )
    return new_pid


def _process_one_row(
    settings: AppSettings,
    sheets: GoogleSheetsApi,
    ads: AdsPowerApi,
    allocator: ProxyAllocator,
    control: RunControl,
    row: int,
    *,
    pw: Playwright,
) -> str:
    """Шаги 2..8 для одного N. Возвращает короткий код результата для лога."""
    sv = settings.status_values
    status_cell, account_cell, pid_cell = sheets.read_row_identity(row)
    status_raw = normalize_sheet_cell_scalar(status_cell)
    initial_status = status_raw
    started_from_empty = labels_equal(status_cell, ref=sv.empty)
    account_name = normalize_sheet_cell_scalar(account_cell)
    pid_snapshot = normalize_sheet_cell_scalar(pid_cell)

    # п.1: пустой статус F → конец таблицы (как при пустом имени): сброс A2 на first_data_row, повтор с п.1
    if status_raw == "":
        sheets.reset_counter_to_first()
        return "reset_counter"

    need_create_profile = False

    if labels_equal(status_cell, ref=sv.created):
        sheets.write_status(row, sv.warming)
    elif labels_equal(status_cell, ref=sv.empty):
        sheets.write_status(row, sv.warming)
        need_create_profile = True
    elif labels_equal(status_cell, ref=sv.warming) or labels_equal(status_cell, ref=sv.deleted):
        return f"skip_{normalize_sheet_cell_scalar(status_cell)}"
    else:
        logger.warning(
            "Аккаунт %s (row=%s): статус %r не совпадает с created/empty/warming/deleted из settings "
            "(ожидаемые строки задаются status_values.* в config/settings.yaml)",
            account_name,
            row,
            status_raw,
        )
        return "skip_unknown_status"

    # п.2: id профиля (новый профиль создаётся внутри hold_proxy — с user_proxy_config для ADS)
    active_profile_id: str | None = pid_snapshot.strip() or None
    if not active_profile_id and not need_create_profile:
        logger.error("Нет ADS profile_id в G%s для %s — пропуск", row, account_name)
        _rollback_account_on_failure(
            sheets,
            ads,
            row,
            initial_status=initial_status,
            started_from_empty=started_from_empty,
            profile_id=active_profile_id,
            browser_was_started=False,
        )
        return "no_profile_id"

    hold_key = (account_name or active_profile_id or f"row_{row}").strip() or f"row_{row}"
    set_worker_panel_label(account=account_name or hold_key)

    # прокси (суффикс порта всегда возвращается в пул при выходе из with, см. hold_proxy)
    delta_pp = delta_wh = delta_kwiff = delta_mass = 0.0
    last_seconds = 0.0
    browser_started = False
    try:
        with allocator.hold_proxy(hold_key) as acq:
            if acq is None:
                logger.error("Не удалось получить прокси для %s (row=%s)", account_name, row)
                _rollback_account_on_failure(
                    sheets,
                    ads,
                    row,
                    initial_status=initial_status,
                    started_from_empty=started_from_empty,
                    profile_id=active_profile_id,
                    browser_was_started=False,
                )
                return "no_proxy"

            ads_profile_name = format_ads_profile_name(account_name, acq.proxy.port)
            set_worker_panel_label(account=account_name or hold_key, proxy_port=acq.proxy.port)
            proxy_hold = FarmingProxyHold(
                allocator=allocator,
                acquisition=acq,
                account_name=hold_key,
            )

            if need_create_profile and not active_profile_id:
                created = _ensure_profile(
                    settings,
                    sheets,
                    ads,
                    row,
                    account_name,
                    create_if_missing=True,
                    known_profile_id=pid_snapshot.strip() or None,
                    user_proxy_config=acq.proxy.to_ads_user_proxy_config(),
                    profile_name=ads_profile_name,
                )
                if not created:
                    _rollback_account_on_failure(
                        sheets,
                        ads,
                        row,
                        initial_status=initial_status,
                        started_from_empty=started_from_empty,
                        profile_id=active_profile_id,
                        browser_was_started=False,
                    )
                    return "no_profile"
                active_profile_id = created

            try:
                ads.update_profile_proxy(
                    active_profile_id,
                    acq.proxy.to_ads_user_proxy_payload(),
                    name=ads_profile_name,
                )
            except Exception as exc:
                logger.warning("update_profile_proxy failed (continue): %s", exc)

            profile_sw, profile_sh = ads.get_profile_screen_resolution(active_profile_id)
            layout_cell = sheets.read_window_layout_cell(row)
            launch_layout, layout_to_save = resolve_launch_layout(
                layout_cell,
                profile_screen_w=profile_sw,
                profile_screen_h=profile_sh,
            )
            if layout_to_save is not None:
                sheets.write_window_layout_cell(row, format_layout_cell(layout_to_save))
                logger.info(
                    "Окно профиля (первый запуск): row=%s %s",
                    row,
                    format_layout_cell(layout_to_save),
                )
            else:
                logger.debug(
                    "Окно профиля (повтор): row=%s → %s",
                    row,
                    format_layout_cell(launch_layout),
                )

            started = ads.start_browser(
                active_profile_id,
                launch_args=launch_args_for_layout(launch_layout),
            )
            browser_started = True
            t_session_start = time.perf_counter()

            try:
                browser = pw.chromium.connect_over_cdp(started.ws_puppeteer)
                try:
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    try:
                        # Правила «I Still Don't Care About Cookies»: сеть/CSS/autoclick по доменам.
                        block_cookie_dialogs(context)
                    except Exception as exc:
                        logger.warning(
                            "block_cookie_dialogs (ai-dont-care-about-cookies) не применён: %s",
                            exc,
                        )
                    page = context.pages[0] if context.pages else context.new_page()
                    apply_page_timeouts(page, settings)

                    if settings.farming.show_synthetic_mouse:
                        try:
                            install_synthetic_mouse_overlay(page)
                        except Exception as exc:
                            logger.warning("show_synthetic_mouse overlay: %s", exc)

                    # п.3: PP
                    if not _skip_new_farming_tasks(control):
                        delta_pp = _farm_important(
                            page, settings, control, "PP", proxy_hold=proxy_hold
                        )
                    # п.4: WH
                    if not _skip_new_farming_tasks(control):
                        delta_wh = _farm_important(
                            page, settings, control, "WH", proxy_hold=proxy_hold
                        )
                    # п.5: Kwiff
                    if not _skip_new_farming_tasks(control):
                        delta_kwiff = _farm_important(
                            page, settings, control, "Kwiff", proxy_hold=proxy_hold
                        )
                    # п.6: массовка
                    if not _skip_new_farming_tasks(control):
                        delta_mass = _farm_mass(page, settings, control, proxy_hold=proxy_hold)
                finally:
                    try:
                        n_tabs = _close_all_browser_tabs(browser)
                        if n_tabs:
                            logger.info(
                                "Сессия %s: закрыто вкладок перед остановкой браузера: %s",
                                account_name or active_profile_id,
                                n_tabs,
                            )
                    except Exception as exc:
                        logger.debug("close_all_browser_tabs: %s", exc)
                    try:
                        browser.close()
                    except Exception:
                        pass
            finally:
                last_seconds = max(0.0, time.perf_counter() - t_session_start)
                try:
                    ads.stop_browser(active_profile_id)
                except Exception as exc:
                    logger.warning("ADS stop failed: %s", exc)

            # п.7 + п.8: накопительный коммит и статус «Создан»
            sheets.commit_session_row(
                row,
                last_seconds=last_seconds,
                important_deltas={"PP": delta_pp, "WH": delta_wh, "Kwiff": delta_kwiff},
                mass_delta=delta_mass,
                status_after=sv.created,
            )
            return "ok"
    except StopAfter9ProxyDialog as ex:
        kind = getattr(ex, "error_kind", str(ex))
        logger.warning("9proxy UI/stop: %s", kind)
        if kind == "Stop":
            control.shutdown.set()
        elif kind == "Safe stop":
            control.safe_stop.set()
        return "stop_dialog"
    except Exception as exc:
        logger.exception("Сессия для %s (row=%s) упала: %s", account_name, row, exc)
        _rollback_account_on_failure(
            sheets,
            ads,
            row,
            initial_status=initial_status,
            started_from_empty=started_from_empty,
            profile_id=active_profile_id,
            browser_was_started=browser_started,
        )
        return "session_error"


def run_account_loop(
    settings: AppSettings,
    sheets: GoogleSheetsApi,
    ads: AdsPowerApi,
    allocator: ProxyAllocator,
    control: RunControl,
) -> None:
    """Основной цикл воркера. Берёт N через A2 и обрабатывает по алгоритму.

    Останавливается на ``control.shutdown``; ``safe_stop`` — между итерациями.
    """
    with thread_playwright_session() as pw:
        while not control.shutdown.is_set():
            if control.safe_stop.is_set():
                logger.info("safe_stop: воркер выходит из цикла")
                return
            if _wait_if_paused(control):
                return

            limit = control.accounts_per_run_limit
            reserved = False
            if limit > 0:
                with control.profile_quota_lock:
                    if control.profiles_started_this_run >= limit:
                        logger.info(
                            "Лимит Выполнений (%s): воркер завершается без новых профилей.",
                            limit,
                        )
                        return
                    control.profiles_started_this_run += 1
                    reserved = True

            try:
                n = sheets.acquire_next_row_index()
            except Exception as exc:
                if reserved:
                    with control.profile_quota_lock:
                        control.profiles_started_this_run -= 1
                logger.exception("acquire_next_row_index failed: %s", exc)
                if interruptible_sleep(2.0, control):
                    return
                continue

            result = _process_one_row(settings, sheets, ads, allocator, control, n, pw=pw)
            if reserved and _profile_quota_should_release_reservation(result):
                with control.profile_quota_lock:
                    control.profiles_started_this_run -= 1

            if result == "reset_counter":
                # n был выдан — теперь A2 сброшен в first_data_row, продолжаем сразу
                continue
            if result == "stop_dialog":
                return
            # короткий рандомизированный inter-row sleep, чтобы не долбить API
            interruptible_sleep(random.uniform(0.5, 1.5), control)
