"""Печать групп AdsPower (group_id, group_name) через Local API. Запуск из корня: python scripts/list_adspower_groups.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cookie_grabber.ads.ads_power_api import AdsPowerApi
from cookie_grabber.config.settings import load_settings


def main() -> None:
    s = load_settings(ROOT)
    api = AdsPowerApi(s)
    try:
        rows = api.list_groups()
    finally:
        api.close()
    if not rows:
        print("(нет групп или пустой ответ)")
        return
    for r in rows:
        print(f"{r['group_id']}\t{r['group_name']}")


if __name__ == "__main__":
    main()
