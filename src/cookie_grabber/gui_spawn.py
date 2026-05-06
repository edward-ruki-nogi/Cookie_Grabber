"""Лёгкий вход для процесса GUI без импорта `main` (оркестратор, Sheets, Ads, Playwright)."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) < 3:
        sys.stderr.write("Использование: python -m cookie_grabber.gui_spawn HOST PORT\n")
        raise SystemExit(2)
    host = sys.argv[1]
    port = int(sys.argv[2])
    from cookie_grabber.ui.app_window import run_gui_client

    run_gui_client(host, port)


if __name__ == "__main__":
    main()
