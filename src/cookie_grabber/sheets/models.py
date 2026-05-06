from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProfileRow:
    row_index: int
    profile_id: str
    status: str
    notes: str
    last_update: str
    important_seconds: dict[str, float] = field(default_factory=dict)
    mass_seconds: float = 0.0
