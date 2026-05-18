"""Общая палитра и шрифты GUI Cookie Graber (меню, окно логов)."""

from __future__ import annotations

# Тёмный slate, бирюзовый акцент (Segoe UI — нативно на Windows).
UI_FONT = ("Segoe UI", 12)
UI_FONT_SM = ("Segoe UI", 11)
UI_FONT_HEAD = ("Segoe UI Semibold", 13)
UI_FONT_BTN = ("Segoe UI Semibold", 12)
UI_FONT_COUNTER = ("Segoe UI", 13)
UI_FONT_LOG = ("Consolas", 10)
UI_FONT_PANEL = ("Segoe UI Semibold", 10)
UI_FONT_PANEL_HOST = ("Segoe UI Semibold", 11)
UI_CORNER = 8
UI_CORNER_SM = 6

C = {
    "bg": "#0d1117",
    "surface": "#161b22",
    "surface2": "#21262d",
    "border": "#30363d",
    "text": "#e6edf3",
    "muted": "#8b949e",
    "accent": "#2a9d8f",
    "accent_hover": "#3dbda8",
    "start": "#238636",
    "start_hover": "#2ea043",
    "stop": "#8b2e2e",
    "stop_hover": "#a83a3a",
    "btn": "#21262d",
    "btn_hover": "#30363d",
    "entry": "#0d1117",
}

LOG_LEVEL = {
    "error": "#f85149",
    "warning": "#d29922",
    "info": "#58a6ff",
    "debug": "#8b949e",
}
