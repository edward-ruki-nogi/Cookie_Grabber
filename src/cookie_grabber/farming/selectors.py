from __future__ import annotations

import re

from playwright.sync_api import Locator, Page

_COOKIE_RE = re.compile(
    r"(accept|agree|ok|allow|consent|got\s*it|understood|cookie)",
    re.I,
)
_BAD_HREF = re.compile(r"(logout|sign\s*out|exit\s*account)", re.I)
_READ_MORE = re.compile(r"(read\s*more|learn\s*more|details|continue\s*reading)", re.I)


def find_cookie_accept_candidates(page: Page) -> list[Locator]:
    candidates: list[Locator] = []
    for role in ("button", "link"):
        loc = page.get_by_role(role)
        try:
            n = loc.count()
        except Exception:
            n = 0
        for i in range(min(n, 80)):
            el = loc.nth(i)
            try:
                txt = (el.inner_text(timeout=500) or "").strip()
            except Exception:
                txt = ""
            if txt and _COOKIE_RE.search(txt):
                candidates.append(el)
    loc = page.locator("[data-cookie], [data-cookie-consent], [class*='cookie'] button")
    try:
        for i in range(min(loc.count(), 40)):
            candidates.append(loc.nth(i))
    except Exception:
        pass
    return candidates


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
        if _READ_MORE.search(txt) or (len(txt) > 0 and len(txt) < 80):
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
