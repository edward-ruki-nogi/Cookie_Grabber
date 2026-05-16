from __future__ import annotations

import re

from playwright.sync_api import Locator, Page

_ACCEPT_ALL = re.compile(r"accept\s+all", re.I)
_ALLOW_ALL = re.compile(r"allow\s+all", re.I)
_ACCEPT_WORD = re.compile(r"\baccept\b", re.I)
_ALLOW_WORD = re.compile(r"\ballow\b", re.I)
_BAD_HREF = re.compile(r"(logout|sign\s*out|exit\s*account)", re.I)
_READ_MORE = re.compile(r"(read\s*more|learn\s*more|details|continue\s*reading)", re.I)
_IGNORE_BUTTON_LABEL = re.compile(r"\bjoin\b", re.I)


def is_ignored_interaction_label(text: str) -> bool:
    """Подписи кнопок/ссылок, по которым не кликаем (навигация, engagement, cookie)."""
    t = (text or "").strip()
    return bool(t and _IGNORE_BUTTON_LABEL.search(t))

# Контейнеры типичных CMP / cookie-баннеров (кнопки ищем внутри descendant).
_COOKIE_BANNER_ROOT_SELECTOR = (
    "[data-cookie], [data-cookie-consent], "
    "[id*='cookie'], [class*='cookie'], [id*='Cookie'], [class*='Cookie'], "
    "[id*='onetrust'], [id*='cookiebot'], [class*='cmp-banner'], "
    "[class*='consent-banner'], [class*='privacy-banner'], [class*='fc-consent']"
)


def _cookie_accept_tier(text: str) -> int | None:
    """0 = accept all … 3 = allow; ``None`` — не кандидат (в т.ч. Read more)."""
    t = (text or "").strip()
    if not t or _READ_MORE.search(t) or is_ignored_interaction_label(t):
        return None
    if _ACCEPT_ALL.search(t):
        return 0
    if _ALLOW_ALL.search(t):
        return 1
    if _ACCEPT_WORD.search(t):
        return 2
    if _ALLOW_WORD.search(t):
        return 3
    return None


def _collect_role_buttons_into_tiers(root: Locator, tiers: list[list[Locator]]) -> None:
    for role in ("button", "link"):
        items = root.get_by_role(role)
        try:
            n = min(items.count(), 50)
        except Exception:
            n = 0
        for i in range(n):
            el = items.nth(i)
            try:
                txt = (el.inner_text(timeout=500) or "").strip()
            except Exception:
                txt = ""
            tier = _cookie_accept_tier(txt)
            if tier is not None:
                tiers[tier].append(el)


def find_cookie_accept_candidates(page: Page) -> list[Locator]:
    tiers: list[list[Locator]] = [[], [], [], []]
    roots = page.locator(_COOKIE_BANNER_ROOT_SELECTOR)
    try:
        n_roots = min(roots.count(), 20)
    except Exception:
        n_roots = 0
    for i in range(n_roots):
        _collect_role_buttons_into_tiers(roots.nth(i), tiers)

    ordered = tiers[0] + tiers[1] + tiers[2] + tiers[3]
    if ordered:
        return ordered

    # Баннер без узнаваемой обёртки: те же фразы по странице, тот же приоритет.
    for role in ("button", "link"):
        loc = page.get_by_role(role)
        try:
            n = min(loc.count(), 100)
        except Exception:
            n = 0
        for i in range(n):
            el = loc.nth(i)
            try:
                txt = (el.inner_text(timeout=500) or "").strip()
            except Exception:
                txt = ""
            tier = _cookie_accept_tier(txt)
            if tier is not None:
                tiers[tier].append(el)

    return tiers[0] + tiers[1] + tiers[2] + tiers[3]


def find_inner_navigation_candidates(page: Page) -> list[Locator]:
    out: list[Locator] = []
    links = page.locator("a[href]")
    try:
        total = links.count()
    except Exception:
        total = 0
    for i in range(min(total, 120)):
        a = links.nth(i)
        try:
            href = a.get_attribute("href") or ""
            txt = (a.inner_text(timeout=400) or "").strip()
        except Exception:
            continue
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        if _BAD_HREF.search(href) or _BAD_HREF.search(txt):
            continue
        if is_ignored_interaction_label(txt):
            continue
        if len(txt) > 0 and len(txt) < 80:
            out.append(a)
    for role in ("button",):
        loc = page.get_by_role(role)
        try:
            n = loc.count()
        except Exception:
            n = 0
        for i in range(min(n, 60)):
            el = loc.nth(i)
            try:
                txt = (el.inner_text(timeout=400) or "").strip()
            except Exception:
                continue
            if _READ_MORE.search(txt):
                out.append(el)
    return out
