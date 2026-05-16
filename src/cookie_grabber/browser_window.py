"""Геометрия окна ADS-браузера: первый/повторный запуск и формат ячейки таблицы."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

# Рабочая область 1920×1080 с учётом панели задач Windows.
# Окно не шире 1920 и не выше 1040 (ни при первом, ни при повторном запуске).
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
TASKBAR_HEIGHT = 40
WORK_HEIGHT = SCREEN_HEIGHT - TASKBAR_HEIGHT
MAX_WINDOW_WIDTH = SCREEN_WIDTH
MAX_WINDOW_HEIGHT = WORK_HEIGHT

MIN_WIDTH = 1100
MIN_HEIGHT = 700
HALF_SCREEN_PIXELS = SCREEN_WIDTH * SCREEN_HEIGHT // 2

X_MIN = -10
X_MAX = 50
Y_MIN = 0
Y_MAX = 100

POSITION_JITTER = 10
FULLSCREEN_CHANCE = 0.2

_LAYOUT_CELL_RE = re.compile(
    r"^\s*(-?\d+)\s*:\s*(-?\d+)\s*,\s*(\d+)\s*:\s*(\d+)\s*$",
)


@dataclass(frozen=True)
class WindowLayout:
    x: int
    y: int
    width: int
    height: int


def format_layout_cell(layout: WindowLayout) -> str:
    return f"{layout.x}:{layout.y}, {layout.width}:{layout.height}"


def parse_layout_cell(raw: str) -> WindowLayout | None:
    s = (raw or "").strip()
    if not s:
        return None
    m = _LAYOUT_CELL_RE.match(s)
    if not m:
        return None
    return WindowLayout(
        x=int(m.group(1)),
        y=int(m.group(2)),
        width=int(m.group(3)),
        height=int(m.group(4)),
    )


def launch_args_for_layout(layout: WindowLayout) -> list[str]:
    return [
        f"--window-position={layout.x},{layout.y}",
        f"--window-size={layout.width},{layout.height}",
    ]


def _fits_screen(x: int, y: int, width: int, height: int) -> bool:
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return False
    if width > MAX_WINDOW_WIDTH or height > MAX_WINDOW_HEIGHT:
        return False
    if width * height < HALF_SCREEN_PIXELS:
        return False
    if not (X_MIN <= x <= X_MAX and Y_MIN <= y <= Y_MAX):
        return False
    if x + width > SCREEN_WIDTH:
        return False
    if y + height > WORK_HEIGHT:
        return False
    return True


def _clamp_position(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """Подогнать x,y под фиксированные width/height и ограничения экрана."""
    x = max(X_MIN, min(x, X_MAX))
    y = max(Y_MIN, min(y, Y_MAX))
    if x + width > SCREEN_WIDTH:
        x = SCREEN_WIDTH - width
    if y + height > WORK_HEIGHT:
        y = WORK_HEIGHT - height
    x = max(X_MIN, min(x, X_MAX))
    y = max(Y_MIN, min(y, Y_MAX))
    return x, y


def _fullscreen_layout() -> WindowLayout:
    x = random.randint(X_MIN, 0)
    # Не шире MAX_WINDOW_WIDTH (при x<0 иначе получалось 1930).
    width = min(MAX_WINDOW_WIDTH, SCREEN_WIDTH - x)
    height = MAX_WINDOW_HEIGHT
    y = 0
    return WindowLayout(x=x, y=y, width=width, height=height)


def layout_for_first_launch(profile_screen_w: int, profile_screen_h: int) -> WindowLayout:
    """Случайная геометрия первого запуска; сохраняется в ячейку таблицы."""
    if (
        profile_screen_w <= SCREEN_WIDTH
        and profile_screen_h <= SCREEN_HEIGHT
        and random.random() < FULLSCREEN_CHANCE
    ):
        return _fullscreen_layout()

    for _ in range(800):
        x = random.randint(X_MIN, X_MAX)
        y = random.randint(Y_MIN, Y_MAX)
        max_w = min(MAX_WINDOW_WIDTH, SCREEN_WIDTH - x)
        max_h = min(MAX_WINDOW_HEIGHT, WORK_HEIGHT - y)
        if max_w < MIN_WIDTH or max_h < MIN_HEIGHT:
            continue
        width = random.randint(MIN_WIDTH, max_w)
        height = random.randint(MIN_HEIGHT, max_h)
        if width * height < HALF_SCREEN_PIXELS:
            continue
        layout = WindowLayout(x=x, y=y, width=width, height=height)
        if _fits_screen(layout.x, layout.y, layout.width, layout.height):
            return layout

    return WindowLayout(x=10, y=50, width=1400, height=900)


def layout_for_repeat_launch(saved: WindowLayout) -> WindowLayout:
    """Повторный запуск: те же width/height (не больше 1920×1040), x/y ±10."""
    w = min(saved.width, MAX_WINDOW_WIDTH)
    h = min(saved.height, MAX_WINDOW_HEIGHT)
    x = saved.x + random.randint(-POSITION_JITTER, POSITION_JITTER)
    y = saved.y + random.randint(-POSITION_JITTER, POSITION_JITTER)
    x, y = _clamp_position(x, y, w, h)
    layout = WindowLayout(x=x, y=y, width=w, height=h)
    if not _fits_screen(layout.x, layout.y, layout.width, layout.height):
        x, y = _clamp_position(saved.x, saved.y, w, h)
        layout = WindowLayout(x=x, y=y, width=w, height=h)
    return layout


def resolve_launch_layout(
    cell_raw: str,
    *,
    profile_screen_w: int,
    profile_screen_h: int,
) -> tuple[WindowLayout, WindowLayout | None]:
    """Вернуть (layout для старта, layout для записи в таблицу или None если не менять)."""
    saved = parse_layout_cell(cell_raw)
    if saved is not None:
        return layout_for_repeat_launch(saved), None
    first = layout_for_first_launch(profile_screen_w, profile_screen_h)
    return first, first
