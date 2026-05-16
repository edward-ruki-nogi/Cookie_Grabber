"""Проверка прокси, активация 9proxy API, диалоги."""
import logging
import os
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
import urllib.parse
from typing import Any, Optional, Tuple

from gala_9static_proxy.flow import ProxyRunMode, proxy_is_9static_managed, proxy_p1_check_stop_flags

_logger = logging.getLogger(__name__)


class StopAfter9ProxyDialog(Exception):
    def __init__(self, error_kind: str) -> None:
        super().__init__(error_kind)
        self.error_kind = error_kind


try:
    import requests
except ImportError:
    requests = None  # type: ignore

_PROXY_URL = "http://httpbin.org/ip"
_CHECK_TIMEOUT = 10


def _do_proxy_request(proxy_url: str) -> bool:
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        resp = requests.get(_PROXY_URL, proxies=proxies, timeout=_CHECK_TIMEOUT)
        return resp.status_code == 200
    except Exception:
        return False


def check_proxy(
    proxy_host: str,
    proxy_port: str,
    proxy_type: str = "socks5",
) -> bool:
    if not proxy_host or not proxy_port:
        return False
    if requests is None:
        _logger.warning("requests не установлен, проверка прокси пропущена")
        return True

    if proxy_type == "socks5":
        if _do_proxy_request(f"socks5h://{proxy_host}:{proxy_port}"):
            return True
        if _do_proxy_request(f"socks5://{proxy_host}:{proxy_port}"):
            return True
        return False

    proxy_url = f"{proxy_type}://{proxy_host}:{proxy_port}"
    return _do_proxy_request(proxy_url)


def activate_proxy_9static(
    proxy_ip: str,
    port: str,
    proxy_isp: str,
    acc_name: str = "",
) -> Tuple[bool, str]:
    if not proxy_ip or not port:
        return False, ""
    if requests is None:
        _logger.warning("requests не установлен, активация прокси невозможна")
        return False, ""

    params = "t=2&num=1&port=" + urllib.parse.quote(str(port)) + "&country=GB"
    if proxy_isp and proxy_isp.strip().lower() != "любой":
        isp_val = proxy_isp.strip()
        params += "&isp=" + urllib.parse.quote(isp_val)

    url = f"http://{proxy_ip}:10101/api/proxy?{params}"
    try:
        resp = requests.get(url, timeout=15)
        response_text = resp.text
        if '"message":"Success"' in response_text or '"message": "Success"' in response_text:
            if acc_name:
                _logger.info(f"{acc_name}: {proxy_ip}:{port} - смена прокси")
            else:
                _logger.info(f"Прокси {proxy_ip}:{port} активирован через API")
            return True, response_text
        return False, response_text
    except Exception as e:
        _logger.error(f"Ошибка активации прокси {proxy_ip}:{port}: {e}")
        return False, str(e)


def show_9proxy_issue_dialog(
    acc_name: str,
    host: str,
    port: str,
    api_response: str,
    validation_timeout_state: Optional[Any] = None,
    proxy_read_timed_out_ban_sec: int = 30 * 60,
) -> None:
    """
    proxy_read_timed_out_ban_sec == 0: не показывать окно, если в ответе API фигурирует Read timed out
    (согласовано с отключением бана/ожидания в ValidationTimeoutState).
    """
    if proxy_read_timed_out_ban_sec <= 0 and api_response and "Read timed out" in api_response:
        _logger.warning(
            f"{acc_name}: ответ API с Read timed out — диалог «9proxy — проблема» пропущен "
            f"(proxy_read_timed_out_ban_sec=0)"
        )
        return
    root = tk.Tk()
    root.title("9proxy — проблема")
    root.resizable(True, True)
    root.minsize(400, 200)

    frame = ttk.Frame(root, padding=15)
    frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(frame, text=f"{acc_name}: проблема с 9proxy", font=("", 10, "bold")).pack(
        anchor=tk.W
    )
    ttk.Label(frame, text="Ответ API:").pack(anchor=tk.W, pady=(10, 2))
    text = ScrolledText(frame, height=8, width=50, wrap=tk.WORD)
    text.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
    text.insert(tk.END, api_response)
    text.config(state=tk.DISABLED)

    def on_continue():
        root.quit()
        root.destroy()

    ttk.Button(frame, text="Продолжить", command=on_continue).pack(anchor=tk.E)

    root.mainloop()

    if validation_timeout_state is not None and validation_timeout_state.is_banned():
        try:
            cwd = os.getcwd()
            bad_p = os.path.join(cwd, "bad_end.flag")
            safe_p = os.path.join(cwd, "safe_stop.flag")
            if os.path.isfile(bad_p):
                _logger.warning(
                    f"{acc_name}: bad_end.flag после диалога 9proxy "
                    f"(блокировка активации прокси: 2 подряд Read timed out)"
                )
                raise StopAfter9ProxyDialog("Stop")
            if os.path.isfile(safe_p):
                _logger.warning(
                    f"{acc_name}: safe_stop.flag после диалога 9proxy "
                    f"(блокировка активации прокси: 2 подряд Read timed out)"
                )
                raise StopAfter9ProxyDialog("Safe stop")
        except StopAfter9ProxyDialog:
            raise
        except Exception as ex:
            _logger.debug(f"{acc_name}: проверка флагов после диалога 9proxy: {ex}")


def _format_ban_remaining(seconds: float) -> str:
    if seconds <= 0:
        return "0 сек"
    m = int(seconds // 60)
    s = int(round(seconds % 60))
    if m > 0:
        return f"{m} мин {s} сек"
    return f"{s} сек"


def show_proxy_ban_thaw_dialog(
    acc_name: str,
    validation_timeout_state: Any,
    cwd: str,
) -> str:
    bad_p = os.path.join(cwd, "bad_end.flag")
    safe_p = os.path.join(cwd, "safe_stop.flag")
    result = ["running"]

    root = tk.Tk()
    root.title("Блокировка активации прокси")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=15)
    frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(
        frame,
        text=f"{acc_name}: активация прокси заблокирована (2 подряд Read timed out)",
        font=("", 10, "bold"),
        wraplength=420,
    ).pack(anchor=tk.W)
    msg_var = tk.StringVar(value="")
    ttk.Label(frame, textvariable=msg_var, wraplength=420).pack(anchor=tk.W, pady=(10, 10))

    def _finish(code: str) -> None:
        if result[0] != "running":
            return
        result[0] = code
        try:
            root.quit()
        except Exception:
            pass

    def refresh_label() -> None:
        if result[0] != "running":
            return
        try:
            if os.path.isfile(bad_p):
                _finish("bad_end")
                return
            if os.path.isfile(safe_p):
                _finish("safe_stop")
                return
            rem = validation_timeout_state.remaining_ban_seconds()
            if rem <= 0:
                _finish("expired")
                return
            msg_var.set(
                f"Осталось ждать: {_format_ban_remaining(rem)}\n"
                "Или нажмите «Разморозить», чтобы снять блокировку для всех процессов."
            )
        except Exception:
            pass
        try:
            root.after(500, refresh_label)
        except Exception:
            pass

    def on_thaw():
        try:
            validation_timeout_state.clear_ban_early()
        except Exception as ex:
            _logger.warning(f"{acc_name}: clear_ban_early: {ex}")
        _finish("thaw")

    ttk.Button(frame, text="Разморозить", command=on_thaw).pack(anchor=tk.E)
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    refresh_label()
    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass
    return result[0] if result[0] != "running" else "expired"


def show_invalid_proxy_after_switch_dialog(
    acc_name: str,
    host: str,
    port: str,
    api_response: str,
) -> None:
    root = tk.Tk()
    root.title("Прокси невалид после смены")
    root.resizable(True, True)
    root.minsize(400, 200)

    frame = ttk.Frame(root, padding=15)
    frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(frame, text=f"{acc_name}: прокси невалид после смены", font=("", 10, "bold")).pack(
        anchor=tk.W
    )
    ttk.Label(frame, text="Ответ API:").pack(anchor=tk.W, pady=(10, 2))
    text = ScrolledText(frame, height=8, width=50, wrap=tk.WORD)
    text.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
    text.insert(tk.END, api_response or "")
    text.config(state=tk.DISABLED)

    def on_repeat():
        root.quit()
        root.destroy()

    ttk.Button(frame, text="Повторить", command=on_repeat).pack(anchor=tk.E)
    root.protocol("WM_DELETE_WINDOW", on_repeat)
    root.mainloop()


def _proxy_p1_raise_if_stop(cwd: str) -> None:
    p1 = proxy_p1_check_stop_flags(cwd)
    if p1 == "bad_end":
        raise StopAfter9ProxyDialog("Stop")
    if p1 == "safe_stop":
        raise StopAfter9ProxyDialog("Safe stop")


def change_ip(
    proxy_host: str,
    proxy_port: str,
    proxy_source: str,
    proxy_isp: str = "",
    acc_name: str = "",
    cwd: Optional[str] = None,
    validation_timeout_state: Optional[Any] = None,
    increment_z35_on_activation: Optional[list] = None,
    today_list_cache: Optional[Any] = None,
    use_today_list: bool = False,
) -> Tuple[bool, str]:
    cwd = cwd or os.getcwd()
    if not proxy_is_9static_managed(proxy_source):
        return True, ""

    while True:
        _proxy_p1_raise_if_stop(cwd)
        if validation_timeout_state is not None and validation_timeout_state.is_banned():
            w = validation_timeout_state.wait_while_banned(cwd, acc_name)
            if w == "bad_end":
                raise StopAfter9ProxyDialog("Stop")
            if w == "safe_stop":
                raise StopAfter9ProxyDialog("Safe stop")
            continue

        _logger.info(f"{acc_name}: смена IP через API (9 static) {proxy_host}:{proxy_port}")
        success = False
        response_text: Optional[str] = ""
        skip_z35_today = use_today_list and today_list_cache is not None
        z35_from_standard_in_today = False
        if skip_z35_today:
            from gala_9static_proxy.today_list_activation import activate_via_today_list_or_standard

            try:
                success, response_text, z35_from_standard_in_today = (
                    activate_via_today_list_or_standard(
                        proxy_host,
                        proxy_port,
                        proxy_isp or "",
                        acc_name,
                        today_list_cache,
                        validation_timeout_state,
                        cwd,
                    )
                )
            except StopAfter9ProxyDialog:
                raise
            if success:
                if z35_from_standard_in_today and increment_z35_on_activation is not None:
                    increment_z35_on_activation[0] = True
                return True, response_text or ""
            if response_text is None:
                continue
        else:
            success, response_text = activate_proxy_9static(
                proxy_host, proxy_port, proxy_isp or "", acc_name
            )
            if success:
                if increment_z35_on_activation is not None:
                    increment_z35_on_activation[0] = True
                return True, response_text

        if response_text and "Read timed out" in response_text:
            if increment_z35_on_activation is not None and (
                not skip_z35_today or z35_from_standard_in_today
            ):
                increment_z35_on_activation[0] = True
            if validation_timeout_state is not None:
                if validation_timeout_state.record_timeout():
                    r = show_proxy_ban_thaw_dialog(acc_name, validation_timeout_state, cwd)
                    if r == "bad_end":
                        raise StopAfter9ProxyDialog("Stop")
                    if r == "safe_stop":
                        raise StopAfter9ProxyDialog("Safe stop")
                    continue
        return False, response_text or ""


def validate_and_activate_proxy(
    proxy_host: str,
    proxy_port: str,
    proxy_source: str,
    proxy_isp: str,
    acc_name: str = "",
    increment_z35_on_activation: Optional[list] = None,
    validation_timeout_state: Optional[Any] = None,
    run_mode: ProxyRunMode = ProxyRunMode.STANDARD,
    cwd: Optional[str] = None,
    first_probe_out: Optional[list] = None,
    today_list_cache: Optional[Any] = None,
    use_today_list: bool = False,
) -> Tuple[bool, Optional[str]]:
    cwd = cwd or os.getcwd()
    protocol = "socks5"

    while True:
        _proxy_p1_raise_if_stop(cwd)

        if not proxy_is_9static_managed(proxy_source):
            if run_mode == ProxyRunMode.CHECK_ONLY:
                ok = check_proxy(proxy_host, proxy_port, protocol)
                if first_probe_out is not None:
                    first_probe_out[0] = ok
                return ok, None
            ok = check_proxy(proxy_host, proxy_port, protocol)
            if first_probe_out is not None:
                first_probe_out[0] = ok
            return (True, None) if ok else (False, None)

        if validation_timeout_state is not None and validation_timeout_state.is_banned():
            w = validation_timeout_state.wait_while_banned(cwd, acc_name)
            if w == "bad_end":
                raise StopAfter9ProxyDialog("Stop")
            if w == "safe_stop":
                raise StopAfter9ProxyDialog("Safe stop")
            continue

        if run_mode == ProxyRunMode.CHECK_ONLY:
            cok = check_proxy(proxy_host, proxy_port, protocol)
            if first_probe_out is not None:
                first_probe_out[0] = cok
            if cok and validation_timeout_state is not None:
                validation_timeout_state.record_success()
            return cok, None

        if run_mode == ProxyRunMode.CHANGE_PROXY:
            if first_probe_out is not None:
                first_probe_out[0] = None
        else:
            cok = check_proxy(proxy_host, proxy_port, protocol)
            if first_probe_out is not None:
                first_probe_out[0] = cok
            if cok:
                if validation_timeout_state is not None:
                    validation_timeout_state.record_success()
                return True, None

        _logger.info(f"Прокси {proxy_host}:{proxy_port} невалиден или режим смены — пробуем активацию...")
        success = False
        response_text: Optional[str] = ""
        skip_z35_today = use_today_list and today_list_cache is not None
        z35_from_standard_in_today = False
        if skip_z35_today:
            from gala_9static_proxy.today_list_activation import activate_via_today_list_or_standard

            try:
                success, response_text, z35_from_standard_in_today = (
                    activate_via_today_list_or_standard(
                        proxy_host,
                        proxy_port,
                        proxy_isp or "",
                        acc_name,
                        today_list_cache,
                        validation_timeout_state,
                        cwd,
                    )
                )
            except StopAfter9ProxyDialog:
                raise
            if success:
                if z35_from_standard_in_today and increment_z35_on_activation is not None:
                    increment_z35_on_activation[0] = True
                result_valid = check_proxy(proxy_host, proxy_port, protocol)
                if result_valid and validation_timeout_state is not None:
                    validation_timeout_state.record_success()
                if not result_valid:
                    _logger.warning(
                        f"{acc_name}: после today list check_proxy снова невалид — без диалога (today list)"
                    )
                return result_valid, None
            if response_text is None:
                continue
        else:
            success, response_text = activate_proxy_9static(
                proxy_host, proxy_port, proxy_isp or "", acc_name
            )
            if success:
                if increment_z35_on_activation is not None:
                    increment_z35_on_activation[0] = True
                result_valid = check_proxy(proxy_host, proxy_port, protocol)
                if result_valid and validation_timeout_state is not None:
                    validation_timeout_state.record_success()
                if not result_valid:
                    show_invalid_proxy_after_switch_dialog(
                        acc_name, proxy_host, proxy_port, response_text
                    )
                return result_valid, None

        if response_text and "Read timed out" in response_text:
            if increment_z35_on_activation is not None and (
                not skip_z35_today or z35_from_standard_in_today
            ):
                increment_z35_on_activation[0] = True
            if validation_timeout_state is not None:
                if validation_timeout_state.record_timeout():
                    r = show_proxy_ban_thaw_dialog(acc_name, validation_timeout_state, cwd)
                    if r == "bad_end":
                        raise StopAfter9ProxyDialog("Stop")
                    if r == "safe_stop":
                        raise StopAfter9ProxyDialog("Safe stop")
                    continue
        return False, response_text
