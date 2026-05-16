from __future__ import annotations

import random
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

import tldextract
from playwright.sync_api import Locator, Page

from cookie_grabber.farming.selectors import is_ignored_interaction_label

_PATH_SEGMENT_DENY = frozenset(
    {
        "login",
        "signin",
        "logout",
        "cart",
        "checkout",
        "profile",
        "account",
        "support",
        "docs",
        "api",
        "admin",
        "upload",
        "delete",
        "edit",
        "settings",
        "password",
        "subscribe",
        "unsubscribe",
        "join",
    }
)

_FILE_SUFFIX_DENY = (
    ".pdf",
    ".zip",
    ".doc",
    ".xls",
    ".docx",
    ".xlsx",
)

_extract = tldextract.TLDExtract()


def registered_domain(url: str) -> str:
    """Registered domain (eTLD+1) в нижнем регистре; пустая строка для IP/invalid."""
    e = _extract((url or "").strip())
    if not e.domain:
        return ""
    if e.suffix:
        return f"{e.domain}.{e.suffix}".lower()
    return (e.domain or "").lower()


def normalize_visit_url(url: str) -> str:
    """Без фрагмента; host lower; path без завершающего слэша (кроме корня)."""
    p = urlsplit((url or "").strip())
    if not p.scheme or not p.netloc:
        return (url or "").strip()
    netloc = p.netloc.lower()
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((p.scheme.lower(), netloc, path, p.query, ""))


def _path_has_denied_segment(path: str) -> bool:
    parts = [x.lower() for x in path.split("/") if x]
    return any(seg in _PATH_SEGMENT_DENY for seg in parts)


def _href_is_skippable_scheme(href: str) -> bool:
    h = (href or "").strip()
    low = h.lower()
    if not h or h.startswith("#"):
        return True
    if low.startswith("javascript:"):
        return True
    if low.startswith("mailto:") or low.startswith("tel:"):
        return True
    return False


def _href_has_denied_file_suffix(absolute_url: str) -> bool:
    path = urlsplit(absolute_url).path.lower()
    return any(path.endswith(sfx) for sfx in _FILE_SUFFIX_DENY)


def link_zone_weight_score(page: Page, locator: Locator) -> float:
    """Выше вес — предпочтительнее для навигации (основной контент)."""
    try:
        tier = locator.evaluate(
            """(el) => {
                let n = el;
                const badRe = /nav|header|footer|menu|sidebar/i;
                const goodRe = /content|^main$|^article$/i;
                for (; n && n.nodeType === 1; n = n.parentElement) {
                    const tag = (n.tagName || '').toLowerCase();
                    const cls = (typeof n.className === 'string' ? n.className : '') || '';
                    const id = (n.id || '');
                    const role = (n.getAttribute && n.getAttribute('role')) || '';
                    if (tag === 'nav' || tag === 'header' || tag === 'footer' || role === 'navigation')
                        return 1;
                    if (badRe.test(cls) || badRe.test(id)) return 1;
                    if (tag === 'main' || tag === 'article' || goodRe.test(cls) || goodRe.test(id))
                        return 3;
                }
                return 2;
            }"""
        )
    except Exception:
        return 1.0
    if tier == 3:
        return 3.0
    if tier == 2:
        return 1.5
    return 0.6


@dataclass
class FarmingSessionState:
    """Состояние одной сессии нагула по одному входному сайту."""

    start_registered_domain: str
    start_netloc: str = ""
    visited_normalized: set[str] = field(default_factory=set)
    revisit_probability: float = 0.07

    def note_visit(self, normalized_url: str) -> None:
        self.visited_normalized.add(normalized_url)

    def allow_revisit(self) -> bool:
        return random.random() < self.revisit_probability


def same_registered_site(session_rd: str, absolute_url: str) -> bool:
    if not session_rd:
        # fallback: только тот же host (например IP)
        return False
    return registered_domain(absolute_url) == session_rd


def pick_internal_navigation_locator(
    page: Page,
    session: FarmingSessionState,
    *,
    max_links: int = 220,
) -> tuple[Locator, str] | None:
    """Возвращает (locator, normalized_absolute_url) или None."""
    links = page.locator("a[href]")
    try:
        total = min(links.count(), max_links)
    except Exception:
        total = 0
    if total <= 0:
        return None

    base_url = page.url
    session_rd = session.start_registered_domain
    pool: list[tuple[Locator, float, str]] = []

    for i in range(total):
        a = links.nth(i)
        try:
            href = (a.get_attribute("href") or "").strip()
            link_txt = (a.inner_text(timeout=400) or "").strip()
        except Exception:
            continue
        if is_ignored_interaction_label(link_txt):
            continue
        if _href_is_skippable_scheme(href):
            continue
        try:
            absolute = urljoin(base_url, href)
        except Exception:
            continue
        sp = urlsplit(absolute)
        if sp.scheme not in ("http", "https"):
            continue
        if not same_registered_site(session_rd, absolute):
            if session_rd:
                continue
            sn = (session.start_netloc or "").lower()
            if not sn:
                continue
            nh = sp.netloc.lower()
            if nh != sn and not nh.endswith("." + sn):
                continue
        if _path_has_denied_segment(sp.path):
            continue
        if _href_has_denied_file_suffix(absolute):
            continue

        norm = normalize_visit_url(absolute)
        if norm in session.visited_normalized and not session.allow_revisit():
            continue

        w = link_zone_weight_score(page, a)
        pool.append((a, float(w), norm))

    if not pool:
        return None

    locs, weights, norms = zip(*pool, strict=True)
    choice = random.choices(range(len(locs)), weights=list(weights), k=1)[0]
    return locs[choice], norms[choice]
