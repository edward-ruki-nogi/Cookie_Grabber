"""Файловые логи потоков воркеров (logs/worker_<n>.log) для окна просмотра."""

from __future__ import annotations

import itertools
import logging
import threading
from pathlib import Path

from cookie_grabber.runtime_paths import application_root

_worker_slot = threading.local()
_slot_counter = itertools.count(1)
_pool_lock = threading.Lock()
_worker_handlers: list[logging.Handler] = []
_host_file_handler: logging.FileHandler | None = None


def logs_dir() -> Path:
    d = application_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def host_log_path() -> Path:
    return logs_dir() / "host.log"


def worker_log_path(worker_id: int) -> Path:
    return logs_dir() / f"worker_{worker_id}.log"


def current_worker_id() -> int | None:
    wid = getattr(_worker_slot, "id", None)
    return int(wid) if wid is not None else None


class _ThreadFilter(logging.Filter):
    def __init__(self, thread_ident: int) -> None:
        super().__init__()
        self._thread_ident = thread_ident

    def filter(self, record: logging.LogRecord) -> bool:
        return record.thread == self._thread_ident


def ensure_host_file_logging(formatter: logging.Formatter) -> None:
    """Один общий host.log для главного процесса и потоков без слота воркера."""
    global _host_file_handler
    if _host_file_handler is not None:
        return
    path = host_log_path()
    fh = logging.FileHandler(path, encoding="utf-8")

    class _NonWorkerFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return getattr(_worker_slot, "id", None) is None

    fh.addFilter(_NonWorkerFilter())
    fh.setFormatter(formatter)
    logging.getLogger().addHandler(fh)
    _host_file_handler = fh


def reset_worker_file_logging() -> None:
    """Снять файловые обработчики воркеров (перед новым запуском пула)."""
    global _slot_counter
    root = logging.getLogger()
    with _pool_lock:
        for h in _worker_handlers:
            try:
                root.removeHandler(h)
                h.close()
            except Exception:
                pass
        _worker_handlers.clear()
        _slot_counter = itertools.count(1)


def init_worker_file_logging() -> None:
    """Вызывается ThreadPoolExecutor(initializer=...) один раз на поток пула."""
    wid = next(_slot_counter)
    _worker_slot.id = wid
    tid = threading.get_ident()
    path = worker_log_path(wid)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(_ThreadFilter(tid))
    root = logging.getLogger()
    with _pool_lock:
        root.addHandler(fh)
        _worker_handlers.append(fh)


_PANEL_SIDECAR_SEP = "|"


def _read_panel_sidecar(wid: int) -> tuple[str, str]:
    path = logs_dir() / f"worker_{wid}_current.txt"
    if not path.exists():
        return "", ""
    try:
        return parse_worker_panel_sidecar(path.read_text(encoding="utf-8"))
    except OSError:
        return "", ""


def parse_worker_panel_sidecar(text: str) -> tuple[str, str]:
    """Формат ``worker_<n>_current.txt``: ``имя_аккаунта|порт`` (без номера строки)."""
    raw = (text or "").strip()
    if not raw:
        return "", ""
    if _PANEL_SIDECAR_SEP in raw:
        acc, port = raw.split(_PANEL_SIDECAR_SEP, 1)
        return acc.strip(), port.strip()
    return raw, ""


def format_worker_panel_title(worker_id: int, account: str = "", proxy_port: str = "") -> str:
    """Заголовок панели: «Поток X - ADS_YY - ZZZZZ»."""
    parts = [f"Поток {worker_id}"]
    acc = (account or "").strip()
    port = (proxy_port or "").strip()
    if acc:
        parts.append(acc)
    if port:
        parts.append(port)
    return " - ".join(parts)


def set_worker_panel_label(
    *,
    account: str = "",
    proxy_port: str | int = "",
) -> None:
    """``logs/worker_<n>_current.txt`` — имя аккаунта и порт для заголовка в log_viewer."""
    wid = current_worker_id()
    if wid is None:
        return
    d = logs_dir()
    prev_acc, prev_port = _read_panel_sidecar(wid)
    acc = (account or "").strip() or prev_acc
    port_s = str(proxy_port).strip() if proxy_port not in ("", None) else prev_port
    payload = f"{acc}{_PANEL_SIDECAR_SEP}{port_s}"
    try:
        (d / f"worker_{wid}_current.txt").write_text(payload, encoding="utf-8")
        legacy_port = d / f"worker_{wid}_port.txt"
        if legacy_port.exists():
            legacy_port.unlink()
    except OSError:
        pass
