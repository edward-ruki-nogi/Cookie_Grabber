"""
Активация 9proxy через today list: API today_list, forward, fallback api/proxy.
"""
from __future__ import annotations

import json
import logging
import random
import time
import urllib.parse
from typing import Any, Callable, List, Optional, Tuple, TYPE_CHECKING

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from gala_9static_proxy.today_list_cache import TodayListCache


try:
    import requests
except ImportError:
    requests = None  # type: ignore

_human_delay_override: Optional[Callable[[float, float], None]] = None


def _default_human_delay(min_sec: float, max_sec: float) -> None:
    time.sleep(random.uniform(min_sec, max_sec))


def set_human_delay(fn: Optional[Callable[[float, float], None]]) -> None:
    """Подмена пауз (например, на utils.human_delays.human_delay). None = встроенный random sleep."""
    global _human_delay_override
    _human_delay_override = fn


def human_delay(min_sec: float = 0.5, max_sec: float = 1.5) -> None:
    fn = _human_delay_override or _default_human_delay
    fn(min_sec, max_sec)


def _requests_available() -> bool:
    return requests is not None


def fetch_today_list(proxy_host: str, limit: int, acc_name: str) -> Tuple[bool, List[dict], str]:
    """GET today_list. Возвращает (ok, data_list, raw_or_error)."""
    if not _requests_available():
        return False, [], "requests not installed"
    url = f"http://{proxy_host}:10101/api/today_list?t=2&limit={int(limit)}&today"
    try:
        resp = requests.get(url, timeout=30)
        text = resp.text
        data = json.loads(text)
        if data.get("error") is True:
            return False, [], text
        arr = data.get("data")
        if not isinstance(arr, list):
            return False, [], text
        return True, arr, text
    except Exception as e:
        _logger.warning(f"{acc_name}: today_list API ошибка: {e}")
        return False, [], str(e)


def forward_today_api(
    proxy_host: str,
    proxy_port: str,
    proxy_id: str,
    acc_name: str,
) -> Tuple[bool, str]:
    if not _requests_available():
        return False, "requests not installed"
    enc_id = urllib.parse.quote(str(proxy_id), safe="")
    enc_port = urllib.parse.quote(str(proxy_port), safe="")
    url = f"http://{proxy_host}:10101/api/forward?t=2&today&id={enc_id}&port={enc_port}"
    try:
        resp = requests.get(url, timeout=30)
        response_text = resp.text
        if '"message":"Success"' in response_text or '"message": "Success"' in response_text:
            _logger.info(f"{acc_name}: today forward id={proxy_id} {proxy_host}:{proxy_port}")
            return True, response_text
        return False, response_text
    except Exception as e:
        _logger.warning(f"{acc_name}: forward API {proxy_id}: {e}")
        return False, str(e)


def port_free_9proxy(proxy_host: str, proxy_port: str, acc_name: str) -> None:
    """Освобождение порта на панели 9proxy (после исчерпания попыток логина при today list)."""
    if not proxy_host or not proxy_port or not _requests_available():
        return
    try:
        enc = urllib.parse.quote(str(proxy_port), safe="")
        url = f"http://{proxy_host}:10101/api/port_free?t=2&ports={enc}"
        requests.get(url, timeout=15)
        _logger.warning(f"{acc_name}: port_free запрошен для порта {proxy_port}")
    except Exception as e:
        _logger.warning(f"{acc_name}: port_free ошибка: {e}")


def _run_ban_after_exhausted_read_timeouts(
    acc_name: str,
    validation_timeout_state: Any,
    cwd: str,
) -> None:
    from gala_9static_proxy.validator import StopAfter9ProxyDialog, show_proxy_ban_thaw_dialog

    for _ in range(2):
        if validation_timeout_state is None:
            break
        if validation_timeout_state.record_timeout():
            r = show_proxy_ban_thaw_dialog(acc_name, validation_timeout_state, cwd)
            if r == "bad_end":
                raise StopAfter9ProxyDialog("Stop")
            if r == "safe_stop":
                raise StopAfter9ProxyDialog("Safe stop")
            break


def _ensure_cache_and_pick_id(
    proxy_host: str,
    acc_name: str,
    today_list_cache: "TodayListCache",
) -> Optional[str]:
    if today_list_cache.is_http_stale():
        ok, items, _ = fetch_today_list(proxy_host, 1000, acc_name)
        if ok:
            today_list_cache.apply_fetch(items, reset_window=True)
        else:
            today_list_cache.apply_fetch([], reset_window=True)

    pid = today_list_cache.try_claim_random_eligible_id()
    if pid is not None:
        return pid

    if today_list_cache.should_fetch_2000():
        ok, items, _ = fetch_today_list(proxy_host, 2000, acc_name)
        today_list_cache.mark_2000_fetch_done()
        if ok:
            today_list_cache.apply_fetch(items, reset_window=False)
        pid = today_list_cache.try_claim_random_eligible_id()
    return pid


def _pick_more_id(
    proxy_host: str,
    acc_name: str,
    today_list_cache: "TodayListCache",
) -> Optional[str]:
    p = today_list_cache.try_claim_random_eligible_id()
    if p:
        return p
    return _ensure_cache_and_pick_id(proxy_host, acc_name, today_list_cache)


def activate_via_today_list_or_standard(
    proxy_host: str,
    proxy_port: str,
    proxy_isp: str,
    acc_name: str,
    today_list_cache: "TodayListCache",
    validation_timeout_state: Any,
    cwd: str,
) -> Tuple[bool, Optional[str], bool]:
    """
    Третий элемент — учитывать Z35 (покупка через api/proxy): True только если последний
    исход связан с activate_proxy_9static, не с forward today list.

    (True, None, z35) — успех, SOCKS валиден.
    (False, msg, z35) — ошибка API; z35=True если ответ от api/proxy.
    (False, None, False) — после бана/диалога; внешний validate_and_activate_proxy делает continue.
    """
    from gala_9static_proxy.validator import StopAfter9ProxyDialog, activate_proxy_9static, check_proxy

    protocol = "socks5"

    def standard() -> Tuple[bool, Optional[str], bool]:
        ok, text = activate_proxy_9static(proxy_host, proxy_port, proxy_isp or "", acc_name)
        return ok, text if not ok else None, True

    first_id = _ensure_cache_and_pick_id(proxy_host, acc_name, today_list_cache)
    if not first_id:
        return standard()

    for phase in range(2):
        current_id = first_id if phase == 0 else _pick_more_id(proxy_host, acc_name, today_list_cache)
        if not current_id:
            return standard()

        for _ in range(10):
            ok, text = forward_today_api(proxy_host, proxy_port, current_id, acc_name)
            if ok:
                if check_proxy(proxy_host, proxy_port, protocol):
                    if validation_timeout_state is not None:
                        validation_timeout_state.record_success()
                    return True, None, False
                _logger.warning(
                    f"{acc_name}: today forward успех, но SOCKS невалид — другой id из списка"
                )
                current_id = _pick_more_id(proxy_host, acc_name, today_list_cache)
                if not current_id:
                    return standard()
                continue

            if text and "Read timed out" in text:
                _logger.warning(
                    f"{acc_name}: forward Read timed out (today), пауза и повтор id={current_id}"
                )
                human_delay(4.0, 6.0)
                continue
            return False, text or "", False

    try:
        _run_ban_after_exhausted_read_timeouts(acc_name, validation_timeout_state, cwd)
    except StopAfter9ProxyDialog:
        raise
    return False, None, False
