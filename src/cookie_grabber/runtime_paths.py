"""Пути приложения: dev (cwd) и frozen exe (каталог .exe)."""

from __future__ import annotations

import sys
from pathlib import Path


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def gui_client_argv(host: str, port: int) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--gui-client", host, str(port)]
    return [sys.executable, "-m", "cookie_grabber.gui_spawn", host, str(port)]


def menu_client_argv(host: str, port: int) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--menu-client", host, str(port)]
    return [sys.executable, "-m", "cookie_grabber.main", "--menu-client", host, str(port)]
