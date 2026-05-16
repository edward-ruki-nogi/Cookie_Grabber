from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, TypeVar

import httplib2
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Таймаут HTTP к Google API (сек.) — избегаем бесконечного зависания
SHEETS_REQUEST_TIMEOUT = 30
# Пауза при 429 / quota exceeded (сек.), до QUOTA_MAX_ATTEMPTS попыток
QUOTA_RETRY_DELAY = 60.0
QUOTA_MAX_ATTEMPTS = 3


def is_quota_or_rate_limit_error(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        if exc.resp is not None and exc.resp.status == 429:
            return True
        try:
            text = (exc.content or b"").decode("utf-8", errors="replace")
        except Exception:
            text = str(exc)
        if "Quota exceeded" in text or "RATE_LIMIT" in text or "quota metric" in text.lower():
            return True
    msg = str(exc)
    return "429" in msg and ("Quota" in msg or "quota" in msg.lower())


def _http_error_sheet_summary(exc: HttpError) -> str:
    """Одна короткая строка для лога без полного тела ошибки."""
    status = getattr(exc.resp, "status", None) if exc.resp else None
    try:
        blob = json.loads((exc.content or b"{}").decode("utf-8", errors="replace"))
        for detail in blob.get("error", {}).get("details") or []:
            if not isinstance(detail, dict):
                continue
            md = detail.get("metadata") or {}
            ql = md.get("quota_limit")
            qv = md.get("quota_limit_value")
            if ql:
                cap = f" [{qv}/min]" if qv is not None else ""
                return f"HTTP {status or '?'} {ql}{cap}"
    except Exception:
        pass
    return f"HTTP {status or '?'} rate_limit"


def execute_sheets_op_with_quota_retry(
    op: Callable[[], T],
    *,
    lock: threading.Lock | None = None,
) -> T:
    """Выполняет один вызов Sheets API с повторами при 429 / quota.

    ``lock`` удерживается только на время ``op()``; пауза при лимите — снаружи,
    чтобы не блокировать остальные потоки на минуту.
    """
    for attempt in range(QUOTA_MAX_ATTEMPTS):
        try:
            if lock is not None:
                with lock:
                    return op()
            return op()
        except HttpError as e:
            if is_quota_or_rate_limit_error(e) and attempt < QUOTA_MAX_ATTEMPTS - 1:
                logger.warning(
                    "Google Sheets: квота, попытка %s/%s, пауза %.0f с (%s)",
                    attempt + 1,
                    QUOTA_MAX_ATTEMPTS,
                    QUOTA_RETRY_DELAY,
                    _http_error_sheet_summary(e),
                )
                time.sleep(QUOTA_RETRY_DELAY)
                continue
            raise


def batch_update_raw(
    service: Any,
    spreadsheet_id: str,
    data_ranges: list[tuple[str, list[list[Any]]]],
    *,
    lock: threading.Lock | None = None,
) -> None:
    body = {
        "valueInputOption": "RAW",
        "data": [{"range": r, "values": v} for r, v in data_ranges],
    }

    def op() -> Any:
        return (
            service.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )

    execute_sheets_op_with_quota_retry(op, lock=lock)


def sheets_service_from_service_account(
    json_path: str,
    *,
    timeout: int = SHEETS_REQUEST_TIMEOUT,
) -> Any:
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(json_path, scopes=scopes)
    http = httplib2.Http(timeout=timeout)
    authed = AuthorizedHttp(creds, http=http)
    return build("sheets", "v4", http=authed, cache_discovery=False)
