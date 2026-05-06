from __future__ import annotations

import os
import socket
import sys
from dataclasses import replace
from pathlib import Path
from tkinter import BooleanVar, StringVar, messagebox

from cookie_grabber.config.settings import (
    PROXY_ISP_ANY,
    PROXY_ISP_VIRGIN,
    PROXY_SOURCE_9STATIC,
    AppSettings,
    ImportantSite,
    PathsConfig,
    ProxyConfig,
    SessionTimeBudget,
    load_settings,
    save_user_settings,
    validate_app_settings,
)

# Порядок и состав важных сайтов в GUI — URL при первом сохранении подхватываются из настроек или отсюда.
_PICKABLE_SITE_IDS: tuple[str, ...] = ("PP", "WH", "Kwiff")
_DEFAULT_SITE_URLS: dict[str, str] = {
    "PP": "https://www.paddypower.com/",
    "WH": "https://www.williamhill.com/",
    "Kwiff": "https://www.kwiff.com/",
}


def _parse_int(raw: str, field: str) -> int:
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{field}: ожидается целое число, получено «{raw}»") from exc


def _parse_float(raw: str, field: str) -> float:
    raw = raw.strip()
    try:
        return float(raw.replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"{field}: ожидается число, получено «{raw}»") from exc


def _url_for_pickable_site(site_id: str, base: AppSettings) -> str:
    for x in base.important_sites:
        if x.id == site_id:
            return x.url
    return _DEFAULT_SITE_URLS.get(site_id, "https://example.com/")


def _open_path_default_app(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess

            subprocess.run(["open", str(path)], check=False)
        else:
            import subprocess

            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:
        messagebox.showerror("Открыть файл", str(exc))


def run_gui_client(host: str, port: int) -> None:
    project_root = Path.cwd()

    try:
        sock = socket.create_connection((host, int(port)), timeout=60)
    except OSError as exc:
        sys.stderr.write(f"Cookie Grabber GUI: нет соединения с хостом: {exc}\n")
        raise SystemExit(1) from exc

    rf = sock.makefile("r", encoding="utf-8", newline="\n")
    wf = sock.makefile("w", encoding="utf-8", newline="\n")

    import customtkinter as ctk_loc

    ctk = ctk_loc
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("Cookie Grabber")

    def rpc(cmd: str) -> tuple[str, str]:
        wf.write(cmd.strip() + "\n")
        wf.flush()
        line = rf.readline()
        if not line:
            return "ERR", "Сервер закрыл соединение"
        parts = line.rstrip("\n").split("\t", 1)
        if len(parts) != 2:
            return "ERR", line.strip()
        return parts[0], parts[1]

    entries: dict[str, ctk.CTkEntry] = {}
    site_check_vars: dict[str, BooleanVar] = {}

    def add_labeled_entry(
        master: ctk.CTkScrollableFrame,
        row: int,
        label: str,
        key: str,
        width: int = 420,
    ) -> None:
        ctk.CTkLabel(master, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        e = ctk.CTkEntry(master, width=width)
        e.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        entries[key] = e

    def load_fields_from_disk() -> None:
        try:
            s = load_settings(project_root)
        except Exception as exc:
            messagebox.showerror("Загрузка настроек", str(exc))
            return

        def setv(key: str, val: object) -> None:
            ent = entries.get(key)
            if ent:
                ent.delete(0, "end")
                ent.insert(0, str(val))

        gs = s.google_sheets
        setv("gs_spreadsheet_id", gs.spreadsheet_id)
        setv("gs_worksheet", gs.worksheet_name)

        setv("thr_threads", s.threads)
        setv("thr_delay_min", s.action_delay_sec_min)
        setv("thr_delay_max", s.action_delay_sec_max)

        setv("job_session_mode", s.session_time_budget.mode)
        setv("job_important_share", s.session_time_budget.important_share)
        setv("job_mass_share", s.session_time_budget.mass_share)

        p = s.proxy
        proxy_source_sv.set(p.source)
        proxy_today_bv.set(bool(p.use_today_list))
        setv("proxy_host", p.host)
        proxy_order_sv.set(str(int(p.port_order)))
        proxy_isp_sv.set(p.isp)
        setv("proxy_ban_sec", int(p.read_timed_out_ban_sec))

        by_id = {x.id: x for x in s.important_sites}
        for site_id in _PICKABLE_SITE_IDS:
            v = site_check_vars.get(site_id)
            if v is not None:
                site = by_id.get(site_id)
                v.set(bool(site.enabled) if site is not None else False)

    def gather_settings() -> AppSettings:
        base = load_settings(project_root)

        gs = replace(
            base.google_sheets,
            spreadsheet_id=entries["gs_spreadsheet_id"].get().strip(),
            worksheet_name=(entries["gs_worksheet"].get().strip() or base.google_sheets.worksheet_name),
        )

        budget = SessionTimeBudget(
            mode=entries["job_session_mode"].get().strip() or "shares",
            important_share=_parse_float(entries["job_important_share"].get(), "important_share"),
            mass_share=_parse_float(entries["job_mass_share"].get(), "mass_share"),
        )

        important_sites: list[ImportantSite] = []
        for site_id in _PICKABLE_SITE_IDS:
            v = site_check_vars[site_id]
            enabled = bool(v.get())
            url = _url_for_pickable_site(site_id, base)
            important_sites.append(ImportantSite(id=site_id, url=url, enabled=enabled))

        proxy_cfg = ProxyConfig(
            source=proxy_source_sv.get().strip() or PROXY_SOURCE_9STATIC,
            use_today_list=bool(proxy_today_bv.get()),
            host=entries["proxy_host"].get().strip(),
            port_order=_parse_int(proxy_order_sv.get(), "proxy.port_order"),
            isp=proxy_isp_sv.get().strip(),
            read_timed_out_ban_sec=float(_parse_float(entries["proxy_ban_sec"].get(), "proxy.read_timed_out_ban_sec")),
            max_retries_per_profile=base.proxy.max_retries_per_profile,
        )

        return AppSettings(
            threads=_parse_int(entries["thr_threads"].get(), "threads"),
            action_delay_sec_min=_parse_float(entries["thr_delay_min"].get(), "action_delay_sec_min"),
            action_delay_sec_max=_parse_float(entries["thr_delay_max"].get(), "action_delay_sec_max"),
            navigation_timeout_ms=base.navigation_timeout_ms,
            network_idle_timeout_ms=base.network_idle_timeout_ms,
            ads_power=base.ads_power,
            google_sheets=gs,
            timezone=base.timezone,
            mass_sites_file=base.mass_sites_file,
            session_time_budget=budget,
            important_sites=important_sites,
            status_values=base.status_values,
            proxy=proxy_cfg,
            paths=PathsConfig(settings_file=base.paths.settings_file),
            project_root=project_root,
        )

    def open_secondary_sites_file() -> None:
        try:
            s = load_settings(project_root)
            path = s.resolve(s.mass_sites_file)
        except Exception as exc:
            messagebox.showerror("Настройки", str(exc))
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file():
                path.touch()
        except OSError as exc:
            messagebox.showerror("Файл", str(exc))
            return
        _open_path_default_app(path)

    def fit_window() -> None:
        root.update_idletasks()
        w = max(480, root.winfo_reqwidth() + 16)
        h = max(260, root.winfo_reqheight() + 16)
        sw = root.winfo_screenwidth() - 40
        sh = root.winfo_screenheight() - 80
        root.geometry(f"{min(w, sw)}x{min(h, sh)}")

    toolbar = ctk.CTkFrame(root, fg_color="transparent")
    toolbar.pack(fill="x", padx=10, pady=10)

    def do_save(reload_host: bool) -> bool:
        try:
            s = gather_settings()
            validate_app_settings(s)
            save_user_settings(s)
        except Exception as exc:
            messagebox.showerror("Сохранение", str(exc))
            return False
        if reload_host:
            kind, msg = rpc("RELOAD")
            if kind != "OK":
                messagebox.showerror("RELOAD", msg)
                return False
        return True

    def on_start() -> None:
        if not do_save(reload_host=True):
            return
        kind, msg = rpc("START")
        if kind != "OK":
            messagebox.showerror("Старт", msg)
        else:
            messagebox.showinfo("Старт", msg)

    def on_safe() -> None:
        kind, msg = rpc("SAFE_STOP")
        if kind != "OK":
            messagebox.showerror("Завершение", msg)
        else:
            messagebox.showinfo("Завершение", msg)

    def on_shutdown() -> None:
        kind, msg = rpc("SHUTDOWN")
        if kind != "OK":
            messagebox.showerror("Стоп", msg)
        else:
            messagebox.showinfo("Стоп", msg)

    def on_save_only() -> None:
        if do_save(reload_host=True):
            messagebox.showinfo("Сохранить", "Настройки записаны и перезагружены на хосте.")

    green = ("#2FA572", "#1F7A4A")
    green_h = ("#38B882", "#258A5E")

    ctk.CTkButton(toolbar, text="Старт", command=on_start, fg_color=green, hover_color=green_h, width=100).pack(
        side="left", padx=4
    )
    ctk.CTkButton(toolbar, text="Завершение", command=on_safe, width=110).pack(side="left", padx=4)
    ctk.CTkButton(
        toolbar,
        text="Стоп",
        command=on_shutdown,
        fg_color=("gray35", "#5c2121"),
        hover_color=("gray45", "#7a2828"),
    ).pack(side="left", padx=4)
    ctk.CTkButton(toolbar, text="Сохранить", command=on_save_only, width=100).pack(side="left", padx=4)

    settings_visible: list[bool] = [False]
    settings_frame = ctk.CTkFrame(root)

    tv = ctk.CTkTabview(settings_frame)
    tv.pack(fill="both", expand=True, padx=4, pady=4)

    tab_main = tv.add("Основные")
    tab_thr = tv.add("Многопоток")
    tab_job = tv.add("Задания")
    tab_proxy = tv.add("Прокси")

    proxy_source_sv = StringVar(value=PROXY_SOURCE_9STATIC)
    proxy_today_bv = BooleanVar(value=False)
    proxy_order_sv = StringVar(value="0")
    proxy_isp_sv = StringVar(value=PROXY_ISP_VIRGIN)

    fm = ctk.CTkScrollableFrame(tab_main)
    fm.pack(fill="both", expand=True)
    fm.grid_columnconfigure(1, weight=1)

    row_m = 0
    ctk.CTkLabel(fm, text="Google Sheets", font=("", 13, "bold")).grid(row=row_m, column=0, columnspan=2, sticky="w", pady=(0, 4))
    row_m += 1
    add_labeled_entry(fm, row_m, "spreadsheet_id", "gs_spreadsheet_id")
    row_m += 1
    add_labeled_entry(fm, row_m, "Имя листа", "gs_worksheet")
    row_m += 1

    ft = ctk.CTkScrollableFrame(tab_thr)
    ft.pack(fill="both", expand=True)
    ft.grid_columnconfigure(1, weight=1)
    rt = 0
    add_labeled_entry(ft, rt, "threads", "thr_threads")
    rt += 1
    add_labeled_entry(ft, rt, "action_delay_sec_min", "thr_delay_min")
    rt += 1
    add_labeled_entry(ft, rt, "action_delay_sec_max", "thr_delay_max")
    rt += 1

    fj = ctk.CTkScrollableFrame(tab_job)
    fj.pack(fill="both", expand=True)
    fj.grid_columnconfigure(1, weight=1)
    rj = 0

    btn_secondary = ctk.CTkButton(
        fj,
        text="Второстепенные сайты",
        command=open_secondary_sites_file,
        width=220,
    )
    btn_secondary.grid(row=rj, column=0, columnspan=2, sticky="w", padx=8, pady=8)
    rj += 1

    add_labeled_entry(fj, rj, "session_time_budget.mode", "job_session_mode")
    rj += 1
    add_labeled_entry(fj, rj, "important_share", "job_important_share")
    rj += 1
    add_labeled_entry(fj, rj, "mass_share", "job_mass_share")
    rj += 1

    ctk.CTkLabel(fj, text="Важные сайты", font=("", 13, "bold")).grid(
        row=rj, column=0, columnspan=2, sticky="w", padx=8, pady=(12, 4)
    )
    rj += 1

    for site_id in _PICKABLE_SITE_IDS:
        bv = BooleanVar(value=False)
        site_check_vars[site_id] = bv
        cb = ctk.CTkCheckBox(fj, text=site_id, variable=bv)
        cb.grid(row=rj, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        rj += 1

    fp = ctk.CTkScrollableFrame(tab_proxy)
    fp.pack(fill="both", expand=True)
    fp.grid_columnconfigure(1, weight=1)
    rp = 0
    ctk.CTkLabel(fp, text="Источник").grid(row=rp, column=0, sticky="w", padx=8, pady=4)
    ctk.CTkOptionMenu(
        fp,
        values=[PROXY_SOURCE_9STATIC],
        variable=proxy_source_sv,
        width=420,
    ).grid(row=rp, column=1, sticky="ew", padx=8, pady=4)
    rp += 1
    ctk.CTkCheckBox(fp, text="Today list", variable=proxy_today_bv).grid(
        row=rp, column=0, columnspan=2, sticky="w", padx=8, pady=4
    )
    rp += 1
    add_labeled_entry(fp, rp, "IP (панель 9proxy)", "proxy_host")
    rp += 1
    ctk.CTkLabel(fp, text="Порт порядок (0–9)").grid(row=rp, column=0, sticky="w", padx=8, pady=4)
    ctk.CTkOptionMenu(
        fp,
        values=[str(i) for i in range(10)],
        variable=proxy_order_sv,
        width=420,
    ).grid(row=rp, column=1, sticky="ew", padx=8, pady=4)
    rp += 1
    ctk.CTkLabel(fp, text="ISP").grid(row=rp, column=0, sticky="w", padx=8, pady=4)
    ctk.CTkOptionMenu(
        fp,
        values=[PROXY_ISP_VIRGIN, PROXY_ISP_ANY],
        variable=proxy_isp_sv,
        width=420,
    ).grid(row=rp, column=1, sticky="ew", padx=8, pady=4)
    rp += 1
    add_labeled_entry(fp, rp, "Бан ошибки API (сек, 2× Read timed out)", "proxy_ban_sec")

    def toggle_settings() -> None:
        if settings_visible[0]:
            settings_frame.pack_forget()
            settings_visible[0] = False
            btn_toggle.configure(text="Настройки  ▾")
        else:
            settings_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
            settings_visible[0] = True
            btn_toggle.configure(text="Настройки  ▴")
        fit_window()

    btn_toggle = ctk.CTkButton(toolbar, text="Настройки  ▾", command=toggle_settings, width=130)
    btn_toggle.pack(side="left", padx=12)

    ctk.CTkLabel(
        root,
        text="Логи и воркеры — в терминале Cursor. Окно только для команд и правок настроек.",
        text_color="gray60",
    ).pack(fill="x", padx=12, pady=(0, 6))

    load_fields_from_disk()
    fit_window()

    def on_close() -> None:
        try:
            wf.write("QUIT\n")
            wf.flush()
        except Exception:
            pass
        try:
            wf.close()
            rf.close()
            sock.close()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
