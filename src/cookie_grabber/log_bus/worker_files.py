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


def set_worker_panel_label(
    *,
    account: str = "",
    proxy_port: str | int = "",
    row: int | None = None,
) -> None:
    """Sidecar-файлы для заголовка панели в log_viewer (как Gala worker_*_current.txt)."""
    wid = current_worker_id()
    if wid is None:
        return
    d = logs_dir()
    parts: list[str] = []
    acc = (account or "").strip()
    if acc:
        parts.append(acc)
    if row is not None:
        parts.append(f"строка {row}")
    label = " — ".join(parts)
    try:
        (d / f"worker_{wid}_current.txt").write_text(label, encoding="utf-8")
    except OSError:
        pass
    port_s = str(proxy_port).strip() if proxy_port not in ("", None) else ""
    port_path = d / f"worker_{wid}_port.txt"
    try:
        if port_s:
            port_path.write_text(port_s, encoding="utf-8")
        elif port_path.exists():
            port_path.unlink()
    except OSError:
        pass
