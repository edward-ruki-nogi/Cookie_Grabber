from __future__ import annotations

import logging
import queue
import threading

from cookie_grabber.log_bus.worker_files import ensure_host_file_logging


class LogBus:
    def __init__(self, maxsize: int = 2000) -> None:
        self._q: queue.Queue[str] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()

    def emit(self, line: str) -> None:
        try:
            self._q.put_nowait(line.rstrip("\n"))
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(line.rstrip("\n"))
            except queue.Full:
                pass

    def drain(self) -> list[str]:
        out: list[str] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out


class QueueHandler(logging.Handler):
    def __init__(self, bus: LogBus) -> None:
        super().__init__()
        self.bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.bus.emit(msg)
        except Exception:
            self.handleError(record)


def setup_logging(bus: LogBus | None = None, level: int = logging.INFO) -> LogBus:
    bus = bus or LogBus()
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    qh = QueueHandler(bus)
    qh.setFormatter(fmt)
    root.addHandler(qh)
    ensure_host_file_logging(fmt)
    return bus
