from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from cookie_grabber.config.settings import AppSettings, ImportantSite


class SiteGroup(str, Enum):
    IMPORTANT = "important"
    MASS = "mass"


@dataclass(frozen=True)
class PlannedVisit:
    group: SiteGroup
    site_id: str | None
    url: str


def _load_mass_urls(settings: AppSettings) -> list[str]:
    path = settings.resolve(settings.mass_sites_file)
    if not path.is_file():
        return []
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _enabled_important(settings: AppSettings) -> list[ImportantSite]:
    return [s for s in settings.important_sites if s.enabled]


def _next_group(
    time_important: float,
    time_mass: float,
    share_important: float,
    share_mass: float,
) -> SiteGroup:
    total = time_important + time_mass
    if total < 1e-6:
        return SiteGroup.IMPORTANT if share_important >= share_mass else SiteGroup.MASS
    target_ratio = share_important / max(1e-9, (share_important + share_mass))
    current_ratio = time_important / total
    return SiteGroup.IMPORTANT if current_ratio < target_ratio else SiteGroup.MASS


@dataclass
class SessionPlan:
    important_queue: list[PlannedVisit] = field(default_factory=list)
    mass_queue: list[PlannedVisit] = field(default_factory=list)
    share_important: float = 0.5
    share_mass: float = 0.5

    def pick_next(self, wall_important: float, wall_mass: float) -> PlannedVisit | None:
        if not self.important_queue and not self.mass_queue:
            return None
        if not self.important_queue:
            return self.mass_queue.pop(0)
        if not self.mass_queue:
            return self.important_queue.pop(0)
        grp = _next_group(wall_important, wall_mass, self.share_important, self.share_mass)
        if grp == SiteGroup.IMPORTANT:
            return self.important_queue.pop(0)
        return self.mass_queue.pop(0)


def build_session_plan(settings: AppSettings) -> SessionPlan:
    important = _enabled_important(settings)
    mass = list(_load_mass_urls(settings))
    random.shuffle(mass)

    imp_queue = [PlannedVisit(SiteGroup.IMPORTANT, s.id, s.url) for s in important]
    mass_queue = [PlannedVisit(SiteGroup.MASS, None, u) for u in mass]
    b = settings.session_time_budget
    return SessionPlan(
        important_queue=imp_queue,
        mass_queue=mass_queue,
        share_important=b.important_share,
        share_mass=b.mass_share,
    )
