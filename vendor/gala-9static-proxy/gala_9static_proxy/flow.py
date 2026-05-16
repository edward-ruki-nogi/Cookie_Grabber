"""
Политика прокси: П.1 (флаги, тип источника), режимы вызова.

Порядок П.1 перед работой с API «9 static»:
1) bad_end.flag, safe_stop.flag
2) тип источника (управляемый сценарий — только proxy_source == «9 static»)
3) трёхзначный режим (аргумент вызова validate_and_activate_proxy / change_ip)
"""
import os
from enum import IntEnum
from typing import Literal


class ProxyRunMode(IntEnum):
    """Режим вызова модуля прокси (аргумент каждого вызова; по умолчанию STANDARD)."""

    STANDARD = 0  # проверка; при невалиде — активация API
    CHANGE_PROXY = 1  # без предварительной проверки — сразу API (гео и т.п.)
    CHECK_ONLY = 2  # только check_proxy, без активации


def proxy_p1_check_stop_flags(cwd: str) -> Literal["ok", "bad_end", "safe_stop"]:
    """Шаг 1 П.1: экстренные флаги."""
    if os.path.isfile(os.path.join(cwd, "bad_end.flag")):
        return "bad_end"
    if os.path.isfile(os.path.join(cwd, "safe_stop.flag")):
        return "safe_stop"
    return "ok"


def proxy_is_9static_managed(proxy_source: str) -> bool:
    """Шаг 2 П.1: для этого источника действуют бан, API 9proxy, пул портов."""
    return (proxy_source or "").strip() == "9 static"
