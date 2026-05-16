"""Нормализация значений одной ячейки после чтения из Sheets."""

from __future__ import annotations

from typing import Any


def normalize_sheet_cell_scalar(value: Any) -> str:
    """NBSP/ZWSP/BOM + strip — для надёжного сравнения статусов с ``status_values.*`` из YAML."""
    s = str(value).replace("\ufeff", "").replace("\u200b", "")
    return s.replace("\u00a0", " ").strip()


def labels_equal(cell: Any, *, ref: Any) -> bool:
    """Сравнение подписанных в таблице и в конфиге строк (нормализация + casefold)."""
    a = normalize_sheet_cell_scalar(cell).casefold()
    b = normalize_sheet_cell_scalar(ref).casefold()
    return a == b
