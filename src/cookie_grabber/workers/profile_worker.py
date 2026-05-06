"""Воркер аккаунта: цикл по строкам через A2 + сценарий нагула.

Реализует алгоритм:
1. acquire_next_row_index → N
2. ветвление по статусу F{N}
3. при «Пустой» — ADS create_profile в группе signup_group_id, запись G{N}
4. прокси (ProxyAllocator), update_profile_proxy, start_browser
5. PP / WH / Kwiff (по флагам important_sites[*].enabled), массовка
6. commit_session_row: накопительно M/N/O/K/E, +D, B, C, F=Создан
7. stop_browser, allocator.release

"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from playwright.sync_api import sync_playwright

from gala_9static_proxy.validator import StopAfter9ProxyDialog

from cookie_grabber.ads.ads_power_api import AdsPowerApi
from cookie_grabber.config.settings import AppSettings, ImportantSite
from cookie_grabber.control.runtime_control import RunControl, interruptible_sleep
from cookie_grabber.farming.flow import farm_single_url
from cookie_grabber.grabber_proxy import ProxyAllocator
from cookie_grabber.sheets.google_sheets_api import GoogleSheetsApi

logger = logging.getLogger(__name__)


def _wait_if_paused(control: RunControl) -> bool:
    while control.pause.is_set() and not control.shutdown.is_set():
        time.sleep(0.2)
    return control.shutdown.is_set()


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
) -> float:
    """Запуск важного сайта, возвращает прожитое wall-clock время (сек)."""
    site = _important_by_id(settings, site_id)
    if site is None or not site.enabled:
        return 0.0
    if control.shutdown.is_set() or _wait_if_paused(control):
        return 0.0
    referer = site.referer or "https://www.google.com/"
    t0 = time.perf_counter()
    try:
        farm_single_url(page, site.url, settings, control, referer=referer)
    except Exception as exc:
        logger.exception("farm важный %s (%s) ошибка: %s", site_id, site.url, exc)
    return max(0.0, time.perf_counter() - t0)


def _farm_mass(
    page: Any,
    settings: AppSettings,
    control: RunControl,
) -> float:
    urls = _load_mass_urls(settings)
    random.shuffle(urls)
    if not urls:
        return 0.0
    t0 = time.perf_counter()
    for url in urls:
        if control.shutdown.is_set() or control.safe_stop.is_set():
            break
        if _wait_if_paused(control):
            break
        try:
            farm_single_url(page, url, settings, control)
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
        new_pid = ads.create_profile(group_id, name=account_name or None)
    except Exception as exc:
        logger.exception("ADS create_profile failed for row %s (%s): %s", row, account_name, exc)
        return None
    sheets.write_profile_id(row, new_pid)
    logger.info("ADS profile создан: row=%s name=%s id=%s", row, account_name, new_pid)
    return new_pid


def _process_one_row(
    settings: AppSettings,
    sheets: GoogleSheetsApi,
    ads: AdsPowerApi,
    allocator: ProxyAllocator,
    control: RunControl,
    row: int,
) -> str:
    """Шаги 2..8 для одного N. Возвращает короткий код результата для лога."""
    sv = settings.status_values
    status_raw, account_name, pid_snapshot = sheets.read_row_identity(row)
    status_raw = status_raw.strip()
    account_name = account_name.strip()
    pid_snapshot = pid_snapshot.strip()

    # п.1: пустая F → запросить сброс A2 на first_data_row (overflow по таблице)
    if status_raw == "":
        if not account_name:
            sheets.reset_counter_to_first()
            return "reset_counter"

    ensured_pid: str | None = None

    if status_raw == sv.created:
        sheets.write_status(row, sv.warming)
    elif status_raw == sv.empty:
        sheets.write_status(row, sv.warming)
        ensured_pid = _ensure_profile(
            settings,
            sheets,
            ads,
            row,
            account_name,
            create_if_missing=True,
            known_profile_id=pid_snapshot,
        )
        if not ensured_pid:
            sheets.write_status(row, sv.error)
            return "no_profile"
    elif status_raw in (sv.warming, sv.deleted):
        return f"skip_{status_raw}"
    else:
        if status_raw == "":
            return "skip_empty_or_overflow"
        logger.warning("Аккаунт %s (row=%s): нераспознанный статус %r — пропуск", account_name, row, status_raw)
        return "skip_unknown_status"

    # п.2: id профиля (после «Пустой» — из _ensure_profile; иначе из первого чтения)
    profile_id = (ensured_pid or pid_snapshot).strip()
    if not profile_id:
        logger.error("Нет ADS profile_id в G%s для %s — пропуск", row, account_name)
        sheets.write_status(row, sv.error)
        return "no_profile_id"

    # прокси (суффикс порта всегда возвращается в пул при выходе из with, см. hold_proxy)
    delta_pp = delta_wh = delta_kwiff = delta_mass = 0.0
    last_seconds = 0.0
    try:
        with allocator.hold_proxy(account_name or profile_id) as acq:
            if acq is None:
                logger.error("Не удалось получить прокси для %s (row=%s)", account_name, row)
                sheets.write_status(row, sv.error)
                return "no_proxy"

            try:
                ads.update_profile_proxy(profile_id, acq.proxy.to_ads_user_proxy_payload())
            except Exception as exc:
                logger.warning("update_profile_proxy failed (continue): %s", exc)

            started = ads.start_browser(profile_id)
            t_session_start = time.perf_counter()

            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.connect_over_cdp(started.ws_puppeteer)
                    try:
                        context = browser.contexts[0] if browser.contexts else browser.new_context()
                        page = context.pages[0] if context.pages else context.new_page()

                        # п.3: PP
                        if not control.shutdown.is_set() and not _wait_if_paused(control):
                            delta_pp = _farm_important(page, settings, control, "PP")
                        # п.4: WH
                        if not control.shutdown.is_set() and not _wait_if_paused(control):
                            delta_wh = _farm_important(page, settings, control, "WH")
                        # п.5: Kwiff
                        if not control.shutdown.is_set() and not _wait_if_paused(control):
                            delta_kwiff = _farm_important(page, settings, control, "Kwiff")
                        # п.6: массовка
                        if not control.shutdown.is_set() and not _wait_if_paused(control):
                            delta_mass = _farm_mass(page, settings, control)
                    finally:
                        try:
                            browser.close()
                        except Exception:
                            pass
            finally:
                last_seconds = max(0.0, time.perf_counter() - t_session_start)
                try:
                    ads.stop_browser(profile_id)
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
        try:
            sheets.write_status(row, sv.error)
        except Exception:
            logger.exception("write status error на ошибочной сессии")
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
    while not control.shutdown.is_set():
        if control.safe_stop.is_set():
            logger.info("safe_stop: воркер выходит из цикла")
            return
        if _wait_if_paused(control):
            return
        try:
            n = sheets.acquire_next_row_index()
        except Exception as exc:
            logger.exception("acquire_next_row_index failed: %s", exc)
            if interruptible_sleep(2.0, control):
                return
            continue
        result = _process_one_row(settings, sheets, ads, allocator, control, n)
        if result == "reset_counter":
            # n был выдан — теперь A2 сброшен в first_data_row, продолжаем сразу
            continue
        if result == "stop_dialog":
            return
        # короткий рандомизированный inter-row sleep, чтобы не долбить API
        interruptible_sleep(random.uniform(0.5, 1.5), control)
