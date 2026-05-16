from typing import Any, Dict, Optional


def build_proxy_config_9static(
    proxy_ip: str,
    port_prefix: int,
    port_suffix: str,
) -> Dict[str, Any]:
    """
    Строит user_proxy_config для AdsPower при типе «9 static».
    Протокол: socks5.
    Порт: AAA + YY, где AAA = port_prefix (100–999), YY = port_suffix («01»–«99»).
    """
    p = int(port_prefix)
    if not (100 <= p <= 999):
        raise ValueError(f"port_prefix must be 100–999 inclusive, got {p}")
    if len(port_suffix) != 2 or not str(port_suffix).isdigit():
        raise ValueError(f"port_suffix must be two digits, got {port_suffix!r}")
    port = f"{p:03d}{port_suffix}"
    return {
        "proxy_soft": "other",
        "proxy_type": "socks5",
        "proxy_host": proxy_ip,
        "proxy_port": port,
        "proxy_user": "",
        "proxy_password": "",
    }


def parse_proxy(proxy_string: str) -> Optional[Dict]:
    """
    Парсит строку прокси формата:
    http://user:pass@ip:port
    socks5://ip:port
    """
    if not proxy_string:
        return None

    parts = proxy_string.split("://")
    if len(parts) != 2:
        return None

    proxy_type = parts[0]
    rest = parts[1]

    if "@" in rest:
        auth, host_port = rest.split("@")
        user_pass = auth.split(":")
        if len(user_pass) == 2:
            user, pwd = user_pass
        else:
            user, pwd = user_pass[0], ""
        host_port_parts = host_port.split(":")
        host = host_port_parts[0]
        port = host_port_parts[1] if len(host_port_parts) > 1 else ""
    else:
        user, pwd = "", ""
        host_port_parts = rest.split(":")
        host = host_port_parts[0]
        port = host_port_parts[1] if len(host_port_parts) > 1 else ""

    return {
        "proxy_type": proxy_type,
        "proxy_host": host,
        "proxy_port": port,
        "proxy_user": user,
        "proxy_pass": pwd,
    }
