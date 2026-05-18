"""
Просмотр логов Cookie Grabber: панели потоков (logs/worker_<n>.log) + host.log.
Запуск: python -m cookie_grabber.log_viewer [--workers N] [--refresh MS]
"""

from __future__ import annotations

import argparse
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from cookie_grabber.log_bus.worker_files import host_log_path, logs_dir, worker_log_path
from cookie_grabber.ui.theme import C, LOG_LEVEL, UI_FONT_LOG, UI_FONT_PANEL, UI_FONT_PANEL_HOST, UI_FONT_SM

TAIL_BYTES = 60_000
REFRESH_MS = 1500

PER_PAGE = 12
WORKER_COLS = 3
WORKER_ROWS = 4
ROW_MAIN_PX = 160
ROW_WORKER_PX = 130
WINDOW_CHROME_PX = 72


def setup_ttk_styles(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=C["bg"], foreground=C["text"], font=UI_FONT_SM)
    style.configure("TLabel", background=C["bg"], foreground=C["text"])
    style.configure("Dim.TLabel", background=C["bg"], foreground=C["muted"])
    style.configure(
        "TScrollbar",
        background=C["border"],
        troughcolor=C["bg"],
        bordercolor=C["border"],
        arrowcolor=C["muted"],
    )
    style.map("TScrollbar", background=[("active", C["btn_hover"])])


def _nav_button(parent: tk.Widget, text: str, command) -> tk.Button:
    """Кнопка пагинации: tk.Button центрирует подпись лучше, чем ttk на Windows."""
    return tk.Button(
        parent,
        text=text,
        command=command,
        font=UI_FONT_SM,
        bg=C["surface2"],
        fg=C["text"],
        activebackground=C["btn_hover"],
        activeforeground=C["accent"],
        disabledforeground=C["muted"],
        relief="flat",
        borderwidth=1,
        highlightthickness=1,
        highlightbackground=C["border"],
        highlightcolor=C["border"],
        padx=10,
        pady=3,
        cursor="hand2",
    )


def read_worker_current(worker_id: int) -> str:
    path = logs_dir() / f"worker_{worker_id}_current.txt"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_worker_proxy_port(worker_id: int) -> str:
    path = logs_dir() / f"worker_{worker_id}_port.txt"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_tail(path: Path, max_bytes: int = TAIL_BYTES) -> str:
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return ""
            start = max(0, size - max_bytes)
            f.seek(start)
            if start > 0:
                f.readline()
            return f.read()
    except OSError:
        return "(ошибка чтения)"


def discover_worker_count() -> int:
    d = logs_dir()
    if not d.is_dir():
        return 0
    ids: list[int] = []
    for p in d.glob("worker_*.log"):
        stem = p.stem
        if not stem.startswith("worker_"):
            continue
        suffix = stem.replace("worker_", "", 1)
        try:
            ids.append(int(suffix))
        except ValueError:
            continue
    return max(ids) if ids else 0


def apply_log_highlighting(text_widget: tk.Text, content: str) -> None:
    for tag in ("error", "warning", "info", "debug"):
        text_widget.tag_remove(tag, "1.0", tk.END)
    for match in re.finditer(r"\b(ERROR|WARNING|INFO|DEBUG)\b", content):
        start_idx = f"1.0+{match.start()}c"
        end_idx = f"1.0+{match.end()}c"
        tag = match.group(1).lower()
        text_widget.tag_add(tag, start_idx, end_idx)


def _bind_copy(text: tk.Text) -> None:
    def _on_copy(event=None):
        try:
            data = text.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        text.clipboard_clear()
        text.clipboard_append(data)
        return "break"

    text.bind("<<Copy>>", _on_copy)

    def _on_ctrl_key(event):
        key = (getattr(event, "keysym", "") or "").lower()
        if key in ("c", "cyrillic_es"):
            return _on_copy(event)
        return None

    text.bind("<Control-Key>", _on_ctrl_key)


def _configure_log_tags(text: tk.Text) -> None:
    bold_font = (UI_FONT_LOG[0], UI_FONT_LOG[1], "bold")
    text.tag_configure("error", foreground=LOG_LEVEL["error"], font=bold_font)
    text.tag_configure("warning", foreground=LOG_LEVEL["warning"], font=bold_font)
    text.tag_configure("info", foreground=LOG_LEVEL["info"])
    text.tag_configure("debug", foreground=LOG_LEVEL["debug"])


def _make_log_text(parent: tk.Widget) -> tk.Text:
    text = tk.Text(
        parent,
        wrap="word",
        font=UI_FONT_LOG,
        bg=C["entry"],
        fg=C["text"],
        insertbackground=C["accent"],
        selectbackground=C["accent"],
        selectforeground=C["bg"],
        relief="flat",
        highlightthickness=0,
        padx=12,
        pady=8,
        state="disabled",
    )
    _bind_copy(text)
    _configure_log_tags(text)
    return text


def create_log_panel(parent, title: str, **grid_opts) -> tuple[ttk.Frame, tk.Text, tk.Label]:
    frame = ttk.Frame(parent, padding=0)
    frame.grid(**grid_opts)
    inner = tk.Frame(frame, bg=C["border"], padx=1, pady=1)
    inner.grid(row=0, column=0, sticky="nsew")
    content = tk.Frame(inner, bg=C["surface"])
    content.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    header = tk.Frame(content, bg=C["surface"], height=26)
    header.pack(fill=tk.X)
    header.pack_propagate(False)
    header_lbl = tk.Label(
        header,
        text=title,
        font=UI_FONT_PANEL,
        fg=C["accent"],
        bg=C["surface"],
        anchor="w",
        padx=10,
        pady=4,
    )
    header_lbl.pack(fill=tk.X)
    text_frame = tk.Frame(content, bg=C["entry"])
    text_frame.pack(fill=tk.BOTH, expand=True)
    text = _make_log_text(text_frame)
    scroll = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    text.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    text_frame.rowconfigure(0, weight=1)
    text_frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return frame, text, header_lbl


def create_host_panel(parent, **grid_opts) -> tuple[ttk.Frame, tk.Text, tk.Frame]:
    frame = ttk.Frame(parent, padding=0)
    frame.grid(**grid_opts)
    inner = tk.Frame(frame, bg=C["border"], padx=1, pady=1)
    inner.grid(row=0, column=0, sticky="nsew")
    content = tk.Frame(inner, bg=C["surface"])
    content.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    header = tk.Frame(content, bg=C["surface"], height=30)
    header.pack(fill=tk.X)
    header.pack_propagate(False)
    tk.Label(
        header,
        text="Хост (оркестратор)",
        font=UI_FONT_PANEL_HOST,
        fg=C["accent"],
        bg=C["surface"],
        anchor="w",
        padx=10,
    ).pack(side=tk.LEFT, pady=4)
    nav_frame = tk.Frame(header, bg=C["surface"])
    nav_frame.pack(side=tk.RIGHT, padx=(8, 6), pady=4)
    text_frame = tk.Frame(content, bg=C["entry"])
    text_frame.pack(fill=tk.BOTH, expand=True)
    text = _make_log_text(text_frame)
    scroll = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    text.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    text_frame.rowconfigure(0, weight=1)
    text_frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return frame, text, nav_frame


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Просмотр логов потоков Cookie Grabber")
    parser.add_argument(
        "--workers",
        "-n",
        type=int,
        default=0,
        help="Число панелей (0 = по worker_*.log или 6)",
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=REFRESH_MS,
        help=f"Интервал обновления, мс (по умолчанию {REFRESH_MS})",
    )
    args = parser.parse_args(argv)

    n = args.workers
    if n <= 0:
        n = discover_worker_count()
    if n <= 0:
        n = 6

    total_pages = max(1, (n + PER_PAGE - 1) // PER_PAGE)
    refresh_ms = max(800, int(args.refresh))

    root = tk.Tk()
    root.title(f"Cookie Grabber — логи, потоки 1…{n} ({total_pages} стр.)")
    win_h = ROW_MAIN_PX + WORKER_ROWS * ROW_WORKER_PX + WINDOW_CHROME_PX
    root.geometry(f"1280x{win_h}")
    root.minsize(960, min(900, win_h))
    root.configure(bg=C["bg"])
    setup_ttk_styles(root)

    cols = WORKER_COLS
    for c in range(cols):
        root.columnconfigure(c, weight=1)

    current_page = [0]
    slot_first_refresh = [True] * PER_PAGE

    def page_label_text() -> str:
        p = current_page[0]
        lo = p * PER_PAGE + 1
        hi = min(n, (p + 1) * PER_PAGE)
        return f"Страница {p + 1} / {total_pages}   (потоки {lo}–{hi} из {n})"

    panes: list[tuple[tk.Text, tk.Label]] = []
    for slot in range(PER_PAGE):
        row_slot, col_slot = divmod(slot, cols)
        _, text, header_lbl = create_log_panel(
            root, "—", row=row_slot, column=col_slot, sticky="nsew", padx=6, pady=4
        )
        root.rowconfigure(row_slot, weight=1, minsize=ROW_WORKER_PX)
        panes.append((text, header_lbl))

    main_row = WORKER_ROWS
    _, main_text, nav_frame = create_host_panel(
        root, row=main_row, column=0, columnspan=cols, sticky="nsew", padx=6, pady=(4, 8)
    )
    root.rowconfigure(main_row, weight=1, minsize=ROW_MAIN_PX)

    def set_page(delta: int) -> None:
        p = current_page[0] + delta
        if p < 0 or p >= total_pages:
            return
        current_page[0] = p
        for i in range(PER_PAGE):
            slot_first_refresh[i] = True
        page_lbl.config(text=page_label_text())
        btn_prev.config(state=tk.NORMAL if p > 0 else tk.DISABLED)
        btn_next.config(state=tk.NORMAL if p < total_pages - 1 else tk.DISABLED)

    btn_prev = _nav_button(nav_frame, "◀ Назад", lambda: set_page(-1))
    btn_prev.pack(side=tk.LEFT, padx=2)
    page_lbl = tk.Label(
        nav_frame,
        text=page_label_text(),
        font=UI_FONT_SM,
        fg=C["muted"],
        bg=C["surface"],
        padx=6,
        pady=3,
    )
    page_lbl.pack(side=tk.LEFT, padx=4)
    btn_next = _nav_button(nav_frame, "Вперёд ▶", lambda: set_page(1))
    btn_next.pack(side=tk.LEFT, padx=2)
    if total_pages <= 1:
        btn_prev.config(state=tk.DISABLED)
        btn_next.config(state=tk.DISABLED)
    else:
        btn_prev.config(state=tk.DISABLED)

    first_refresh_main = True
    content_cache: dict[str, str] = {}

    def refresh() -> None:
        nonlocal first_refresh_main

        top_index_main = main_text.index("@0,0")
        first_m, last_m = main_text.yview()
        at_bottom_main = (last_m >= 0.999) or (first_m == 0.0 and last_m == 1.0)
        if first_refresh_main:
            at_bottom_main = True

        main_content = read_tail(host_log_path())
        main_actual = main_content or "(host.log ещё не создан или пуст)"
        if content_cache.get("host") != main_actual:
            content_cache["host"] = main_actual
            main_text.configure(state="normal")
            main_text.delete("1.0", tk.END)
            main_text.insert(tk.END, main_actual)
            apply_log_highlighting(main_text, main_actual)
            if at_bottom_main:
                main_text.see("end-1c")
            else:
                main_text.yview(top_index_main)
            main_text.configure(state="disabled")
        first_refresh_main = False

        base = current_page[0] * PER_PAGE
        for slot in range(PER_PAGE):
            widget, header_lbl = panes[slot]
            wid = base + slot + 1

            if wid > n:
                header_lbl.config(text="—", fg=C["muted"])
                if content_cache.get(f"_empty{slot}") != "1":
                    content_cache[f"_empty{slot}"] = "1"
                    widget.configure(state="normal")
                    widget.delete("1.0", tk.END)
                    widget.configure(state="disabled")
                slot_first_refresh[slot] = False
                continue

            cache_key = f"w{wid}"
            top_index = widget.index("@0,0")
            first, last = widget.yview()
            at_bottom = (last >= 0.999) or (first == 0.0 and last == 1.0)
            if slot_first_refresh[slot]:
                at_bottom = True

            acc = read_worker_current(wid)
            port = read_worker_proxy_port(wid)
            if acc and port:
                title = f"Поток {wid} — {acc} ({port})"
            elif acc:
                title = f"Поток {wid} — {acc}"
            else:
                title = f"Поток {wid}"
            header_lbl.config(text=title, fg=C["accent"])

            content = read_tail(worker_log_path(wid))
            actual = content or "(файл ещё не создан или пуст)"
            if content_cache.get(cache_key) != actual:
                content_cache[cache_key] = actual
                widget.configure(state="normal")
                widget.delete("1.0", tk.END)
                widget.insert(tk.END, actual)
                apply_log_highlighting(widget, actual)
                if at_bottom:
                    widget.see("end-1c")
                else:
                    widget.yview(top_index)
                widget.configure(state="disabled")
            slot_first_refresh[slot] = False

        root.after(refresh_ms, refresh)

    root.after(0, refresh)
    root.mainloop()


if __name__ == "__main__":
    main(sys.argv[1:])
