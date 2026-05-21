"""Файлы остановки для gala_9static_proxy (``proxy_p1_check_stop_flags``)."""

from __future__ import annotations

from pathlib import Path

from cookie_grabber.runtime_paths import application_root

SAFE_STOP_FLAG = "safe_stop.flag"
BAD_END_FLAG = "bad_end.flag"


def stop_flags_dir() -> Path:
    return application_root()


def clear_stop_flags() -> None:
    for name in (SAFE_STOP_FLAG, BAD_END_FLAG):
        try:
            (stop_flags_dir() / name).unlink(missing_ok=True)
        except OSError:
            pass


def write_safe_stop_flag() -> None:
    (stop_flags_dir() / SAFE_STOP_FLAG).write_text("", encoding="utf-8")


def write_bad_end_flag() -> None:
    (stop_flags_dir() / BAD_END_FLAG).write_text("", encoding="utf-8")
