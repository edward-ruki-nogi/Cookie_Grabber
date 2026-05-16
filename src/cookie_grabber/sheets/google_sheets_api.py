from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Any

from zoneinfo import ZoneInfo

from cookie_grabber.config.settings import AppSettings, GoogleSheetsConfig
from cookie_grabber.sheets.atomic import (
    batch_update_raw,
    execute_sheets_op_with_quota_retry,
    sheets_service_from_service_account,
)
from cookie_grabber.sheets.cell_text import normalize_sheet_cell_scalar
from cookie_grabber.sheets.models import ProfileRow

logger = logging.getLogger(__name__)


def _escape_sheet(name: str) -> str:
    return name.replace("'", "''")


def _a1(worksheet: str, column: str, row: int) -> str:
    return f"'{_escape_sheet(worksheet)}'!{column}{row}"


def now_london_iso(settings: AppSettings) -> str:
    tz = ZoneInfo(settings.timezone)
    return datetime.now(tz).replace(microsecond=0).isoformat()


def today_london_sheet_date(settings: AppSettings) -> str:
    """Календарная дата для ячейки таблицы (dd.MM.YYYY), часовой пояс из настроек."""
    tz = ZoneInfo(settings.timezone)
    return datetime.now(tz).strftime("%d.%m.%Y")


def column_letter_to_index(col: str) -> int:
    col = col.strip().upper()
    n = 0
    for ch in col:
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"Bad column letter: {col}")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def column_index_to_letter(idx: int) -> str:
    s = ""
    i = idx + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


_CELL_RE = re.compile(r"^\s*([A-Za-z]+)\s*(\d+)\s*$")

# Длительности в этих колонках пишем/читаем как h:mm (например 1:05); остальные — число секунд.
_HMM_DURATION_COLUMNS = frozenset({"C", "E", "K", "L", "M", "N", "O", "P"})
_HMM_CELL_RE = re.compile(r"^\s*(\d+)\s*:\s*(\d{1,2})\s*$")


def _parse_cell(cell: str) -> tuple[str, int]:
    m = _CELL_RE.match(cell)
    if not m:
        raise ValueError(f"Bad cell ref: {cell!r}")
    return m.group(1).upper(), int(m.group(2))


def _to_float(raw: Any) -> float:
    if raw is None:
        return 0.0
    s = str(raw).strip().replace(",", ".")
    if s == "":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _to_int(raw: Any) -> int:
    if raw is None:
        return 0
    s = str(raw).strip()
    if s == "":
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _column_letter_key(col: str) -> str:
    return col.strip().upper()


def _format_seconds_hmm_column(column: str, seconds: float) -> Any:
    """Для колонок из ``_HMM_DURATION_COLUMNS`` — строка ``h:mm``; иначе секунды числом."""
    if _column_letter_key(column) not in _HMM_DURATION_COLUMNS:
        return round(float(seconds), 3)
    total = max(0, int(round(float(seconds))))
    h = total // 3600
    m = (total % 3600) // 60
    return f"{h}:{m:02d}"


def _sheet_cell_seconds(raw: Any, column: str) -> float:
    """Секунды из ячейки: для hmm-колонок разбор ``h:mm``, иначе как float (в т.ч. старые числа)."""
    if _column_letter_key(column) not in _HMM_DURATION_COLUMNS:
        return _to_float(raw)
    s = normalize_sheet_cell_scalar(raw)
    if not s:
        return 0.0
    m = _HMM_CELL_RE.match(s)
    if m:
        h = int(m.group(1))
        minutes = int(m.group(2))
        if minutes >= 60:
            return _to_float(raw)
        return float(h * 3600 + minutes * 60)
    return _to_float(raw)


class GoogleSheetsApi:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.gs: GoogleSheetsConfig = settings.google_sheets
        path = settings.resolve(self.gs.service_account_json_path)
        if not path.is_file():
            raise FileNotFoundError(f"Service account JSON not found: {path}")
        self._service = sheets_service_from_service_account(str(path))
        self._sheet = self.gs.worksheet_name
        self._lock = threading.Lock()
        self._a2_lock = threading.Lock()

    # ------------------------------------------------------------- low-level

    def _get_value(self, range_a1: str) -> str:
        def op() -> dict[str, Any]:
            return (
                self._service.spreadsheets()
                .values()
                .get(spreadsheetId=self.gs.spreadsheet_id, range=range_a1)
                .execute()
            )

        result = execute_sheets_op_with_quota_retry(op, lock=self._lock)
        values = result.get("values") or []
        if not values or not values[0]:
            return ""
        return normalize_sheet_cell_scalar(values[0][0])

    def _batch_get_first_cells(self, ranges_a1: list[str]) -> list[str]:
        """Один HTTP-вызов: по каждому A1 только первая ячейка, порядок = порядку ``ranges``.

        Не использовать один ``get`` на диапазон от min до max колонки: API Sheets отбрасывает
        крайние пустые ячейки, из-за этого значения могут «съехать» (напр. статус из F попадёт
        как имя из A).
        """
        if not ranges_a1:
            return []

        def op() -> dict[str, Any]:
            return (
                self._service.spreadsheets()
                .values()
                .batchGet(
                    spreadsheetId=self.gs.spreadsheet_id,
                    ranges=ranges_a1,
                    majorDimension="ROWS",
                )
                .execute()
            )

        result = execute_sheets_op_with_quota_retry(op, lock=self._lock)
        out: list[str] = []
        for vr in result.get("valueRanges", []):
            vals = vr.get("values") or []
            if vals and vals[0]:
                out.append(normalize_sheet_cell_scalar(vals[0][0]))
            else:
                out.append("")
        while len(out) < len(ranges_a1):
            out.append("")
        return out[: len(ranges_a1)]

    def _get_row_values(self, columns: list[str], row: int) -> list[str]:
        if not columns:
            return []
        stripped: list[str] = []
        for c in columns:
            column_letter_to_index(c)
            stripped.append(c.strip().upper())
        ranges = [_a1(self._sheet, col_l, row) for col_l in stripped]
        return self._batch_get_first_cells(ranges)

    def _batch_update(self, updates: list[tuple[str, list[list[Any]]]]) -> None:
        batch_update_raw(self._service, self.gs.spreadsheet_id, updates, lock=self._lock)

    # ------------------------------------------------------------ counter A2

    def acquire_next_row_index(self) -> int:
        """Атомарно: читает A2, парсит как int (пусто/мусор → ``first_data_row``),
        пишет N+1 в A2, возвращает N."""
        cell = self.gs.counter_cell
        a1 = f"'{_escape_sheet(self._sheet)}'!{cell}"
        first = self.gs.first_data_row
        with self._a2_lock:
            raw = self._get_value(a1)
            try:
                n = int(float(str(raw).strip())) if str(raw).strip() else first
            except ValueError:
                n = first
            if n < first:
                n = first
            self._batch_update([(a1, [[n + 1]])])
            return n

    def reset_counter_to_first(self) -> None:
        cell = self.gs.counter_cell
        a1 = f"'{_escape_sheet(self._sheet)}'!{cell}"
        first = self.gs.first_data_row
        with self._a2_lock:
            self._batch_update([(a1, [[first]])])

    # -------------------------------------------------------- per-row reads

    def read_row_identity(self, row: int) -> tuple[str, str, str]:
        """Статус, имя аккаунта, ADS profile id — одним ``values.get`` по строке."""
        gs = self.gs
        vals = self._get_row_values(
            [gs.status_column, gs.account_name_column, gs.ads_profile_id_column],
            row,
        )
        return vals[0], vals[1], vals[2]

    def read_account_name(self, row: int) -> str:
        return self._get_value(_a1(self._sheet, self.gs.account_name_column, row))

    def read_status(self, row: int) -> str:
        return self._get_value(_a1(self._sheet, self.gs.status_column, row))

    def read_ads_profile_id(self, row: int) -> str:
        return self._get_value(_a1(self._sheet, self.gs.ads_profile_id_column, row))

    def read_row_totals(
        self,
        row: int,
        important_ids: list[str],
    ) -> dict[str, float]:
        cols = [
            self.gs.total_seconds_column,
            self.gs.sessions_count_column,
            self.gs.mass_total_column,
            *[
                self.gs.important_site_columns[sid]
                for sid in important_ids
                if sid in self.gs.important_site_columns
            ],
        ]
        if not cols:
            return {}
        vals = self._get_row_values(cols, row)
        out: dict[str, float] = {}
        out["total"] = _sheet_cell_seconds(vals[0], cols[0])
        out["sessions"] = float(_to_int(vals[1]))
        out["mass"] = _sheet_cell_seconds(vals[2], cols[2])
        i = 3
        for sid in important_ids:
            if sid not in self.gs.important_site_columns:
                continue
            out[sid] = _sheet_cell_seconds(vals[i], cols[i])
            i += 1
        return out

    # -------------------------------------------------------- per-row writes

    def write_status(self, row: int, status: str, *, with_timestamp: bool = False) -> None:
        data: list[tuple[str, list[list[Any]]]] = [
            (_a1(self._sheet, self.gs.status_column, row), [[status]])
        ]
        if with_timestamp:
            data.append(
                (
                    _a1(self._sheet, self.gs.last_update_column, row),
                    [[now_london_iso(self.settings)]],
                )
            )
        self._batch_update(data)

    def write_profile_id(self, row: int, profile_id: str) -> None:
        self._batch_update(
            [(_a1(self._sheet, self.gs.ads_profile_id_column, row), [[profile_id]])]
        )

    def read_window_layout_cell(self, row: int) -> str:
        return self._get_value(
            _a1(self._sheet, self.gs.window_layout_column, row),
        )

    def write_window_layout_cell(self, row: int, value: str) -> None:
        self._batch_update(
            [(_a1(self._sheet, self.gs.window_layout_column, row), [[value]])]
        )

    def commit_session_row(
        self,
        row: int,
        *,
        last_seconds: float,
        important_deltas: dict[str, float],
        mass_delta: float,
        status_after: str,
    ) -> None:
        """Шаги 6/7 алгоритма: накопительно прибавить дельты к M/N/O/K/E,
        D += 1, B = дата dd.MM.YYYY (London tz), C = длительность сессии (h:mm),
        E = суммарное время (h:mm), F = status_after.
        Один read + один RAW batchUpdate под общим ``_lock``."""
        gs = self.gs
        important_ids = [sid for sid in important_deltas.keys() if sid in gs.important_site_columns]
        cols = [
            gs.total_seconds_column,
            gs.sessions_count_column,
            gs.mass_total_column,
            *[gs.important_site_columns[sid] for sid in important_ids],
        ]
        cur = self._get_row_values(cols, row)
        cur_total = _sheet_cell_seconds(cur[0], cols[0])
        cur_sessions = _to_int(cur[1])
        cur_mass = _sheet_cell_seconds(cur[2], cols[2])
        new_imp: dict[str, float] = {}
        for idx, sid in enumerate(important_ids):
            col_l = cols[3 + idx]
            new_imp[sid] = _sheet_cell_seconds(cur[3 + idx], col_l) + float(
                important_deltas.get(sid, 0.0)
            )

        date_dmy = today_london_sheet_date(self.settings)
        new_total = cur_total + float(last_seconds)
        new_sessions = cur_sessions + 1
        new_mass = cur_mass + float(mass_delta)

        updates: list[tuple[str, list[list[Any]]]] = [
            (_a1(self._sheet, gs.last_date_column, row), [[date_dmy]]),
            (
                _a1(self._sheet, gs.last_seconds_column, row),
                [[_format_seconds_hmm_column(gs.last_seconds_column, last_seconds)]],
            ),
            (_a1(self._sheet, gs.sessions_count_column, row), [[new_sessions]]),
            (
                _a1(self._sheet, gs.total_seconds_column, row),
                [[_format_seconds_hmm_column(gs.total_seconds_column, new_total)]],
            ),
            (
                _a1(self._sheet, gs.mass_total_column, row),
                [[_format_seconds_hmm_column(gs.mass_total_column, new_mass)]],
            ),
            (_a1(self._sheet, gs.status_column, row), [[status_after]]),
        ]
        for sid, val in new_imp.items():
            col = gs.important_site_columns[sid]
            updates.append(
                (_a1(self._sheet, col, row), [[_format_seconds_hmm_column(col, float(val))]]),
            )
        self._batch_update(updates)

    # ---------------------------------------------------------- legacy path

    def _read_span(self) -> tuple[int, int]:
        indices = [
            column_letter_to_index(self.gs.profile_id_column),
            column_letter_to_index(self.gs.status_column),
            column_letter_to_index(self.gs.notes_column),
            column_letter_to_index(self.gs.last_update_column),
            column_letter_to_index(self.gs.mass_total_column),
            *[column_letter_to_index(c) for c in self.gs.important_site_columns.values()],
        ]
        return min(indices), max(indices)

    def read_profile_rows(self, max_rows: int = 2000) -> list[ProfileRow]:
        first = self.gs.first_data_row
        lo, hi = self._read_span()
        start_l = column_index_to_letter(lo)
        end_l = column_index_to_letter(hi)
        last_row = first + max_rows - 1
        read_range = f"'{_escape_sheet(self._sheet)}'!{start_l}{first}:{end_l}{last_row}"

        def op() -> dict[str, Any]:
            return (
                self._service.spreadsheets()
                .values()
                .get(spreadsheetId=self.gs.spreadsheet_id, range=read_range)
                .execute()
            )

        result = execute_sheets_op_with_quota_retry(op, lock=self._lock)
        values = result.get("values", [])
        width = hi - lo + 1
        important_cols = {
            sid: column_letter_to_index(letter) - lo
            for sid, letter in self.gs.important_site_columns.items()
        }
        mass_ci = column_letter_to_index(self.gs.mass_total_column) - lo
        pid_ci = column_letter_to_index(self.gs.profile_id_column) - lo
        st_ci = column_letter_to_index(self.gs.status_column) - lo
        notes_ci = column_letter_to_index(self.gs.notes_column) - lo
        lu_ci = column_letter_to_index(self.gs.last_update_column) - lo

        rows: list[ProfileRow] = []
        for i, row_vals in enumerate(values):
            row_index = first + i
            padded = list(row_vals) + [""] * (width - len(row_vals))

            def cell(ci: int) -> str:
                return str(padded[ci]).strip() if 0 <= ci < len(padded) else ""

            pid = cell(pid_ci)
            if not pid:
                continue
            imp: dict[str, float] = {}
            for site_id, ci in important_cols.items():
                col_l = self.gs.important_site_columns[site_id]
                imp[site_id] = _sheet_cell_seconds(cell(ci), col_l)
            rows.append(
                ProfileRow(
                    row_index=row_index,
                    profile_id=pid,
                    status=cell(st_ci),
                    notes=cell(notes_ci),
                    last_update=cell(lu_ci),
                    important_seconds=imp,
                    mass_seconds=_sheet_cell_seconds(
                        cell(mass_ci), self.gs.mass_total_column
                    ),
                )
            )
        return rows

    def write_row_bundle(
        self,
        row_index: int,
        status: str,
        notes: str,
        important_totals: dict[str, float],
        mass_total: float,
    ) -> None:
        ts = now_london_iso(self.settings)
        data: list[tuple[str, list[list[Any]]]] = [
            (_a1(self._sheet, self.gs.status_column, row_index), [[status]]),
            (_a1(self._sheet, self.gs.notes_column, row_index), [[notes]]),
            (_a1(self._sheet, self.gs.last_update_column, row_index), [[ts]]),
            (
                _a1(self._sheet, self.gs.mass_total_column, row_index),
                [[_format_seconds_hmm_column(self.gs.mass_total_column, mass_total)]],
            ),
        ]
        for site_id, sec in important_totals.items():
            col = self.gs.important_site_columns.get(site_id)
            if not col:
                logger.warning("Unknown important site id %s — skip column", site_id)
                continue
            data.append(
                (
                    _a1(self._sheet, col, row_index),
                    [[_format_seconds_hmm_column(col, float(sec))]],
                )
            )
        self._batch_update(data)
