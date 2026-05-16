"""
Сессия «9 static» до validate: ожидание снятия бана (без удержания порта), acquire пула, сборка конфига.
"""
import os
from typing import Any, Dict, Tuple

from gala_9static_proxy.parser import build_proxy_config_9static
from gala_9static_proxy.validator import StopAfter9ProxyDialog


def wait_unban_if_needed(
    validation_timeout_state: Any,
    cwd: str,
    acc_name: str,
) -> None:
    """
    Если активен бан по Read timed out — блокировать до снятия.
    bad_end / safe_stop — StopAfter9ProxyDialog.
    """
    if validation_timeout_state is None or not validation_timeout_state.is_banned():
        return
    w = validation_timeout_state.wait_while_banned(cwd, acc_name)
    if w == "bad_end":
        raise StopAfter9ProxyDialog("Stop")
    if w == "safe_stop":
        raise StopAfter9ProxyDialog("Safe stop")


def acquire_9static_port_and_config(
    proxy_pool: Any,
    shared_port_suffixes: dict,
    row_key: int,
    proxy_ip: str,
    port_prefix: int,
) -> Tuple[str, Dict[str, Any], str, str]:
    """
    Забирает суффикс из пула, пишет в shared_port_suffixes, строит proxy_config.
    Возвращает (port_suffix, proxy_config, proxy_host, proxy_port).
    """
    port_suffix = proxy_pool.acquire()
    shared_port_suffixes[row_key] = port_suffix
    cfg = build_proxy_config_9static(proxy_ip, port_prefix, port_suffix)
    return port_suffix, cfg, cfg["proxy_host"], cfg["proxy_port"]


def worker_proxy_cwd() -> str:
    return os.getcwd()
