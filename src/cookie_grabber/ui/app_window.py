from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import BooleanVar, StringVar, messagebox

from cookie_grabber.runtime_paths import application_root
from cookie_grabber.updates.github_release import (
    check_for_update,
    download_and_stage_update,
    launch_apply_update,
)
from cookie_grabber.config.settings import (
    PROXY_ISP_ANY,
    PROXY_ISP_VIRGIN,
    PROXY_SOURCE_9STATIC,
    AppSettings,
    FarmingConfig,
    ImportantSite,
    PathsConfig,
    ProxyConfig,
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
_ACCOUNTS_PER_RUN_INF = "\u221e"  # ∞ в поле «Выполнений» для безлимита (в YAML хранится 0)


def _parse_accounts_per_run_display(raw: str) -> int:
    s = (raw or "").strip()
    if not s or s == _ACCOUNTS_PER_RUN_INF or s.casefold() in ("inf", "infinity"):
        return 0
    return int(s, 10)


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
    project_root = application_root()

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
        ent_apr = entries.get("thr_accounts_per_run")
        if ent_apr:
            ent_apr.delete(0, "end")
            ent_apr.insert(0, _ACCOUNTS_PER_RUN_INF if s.accounts_per_run == 0 else str(s.accounts_per_run))
        setv("thr_delay_min", s.action_delay_sec_min)
        setv("thr_delay_max", s.action_delay_sec_max)

        farm = s.farming
        setv("job_important_min", farm.important_sites_minutes_per_site)
        setv("job_mass_min", farm.mass_sites_minutes_per_site)
        setv("job_cookie_timeout", farm.cookie_banner_timeout_sec)
        setv("job_nav_dly_min", farm.between_nav_delay_sec_min)
        setv("job_nav_dly_max", farm.between_nav_delay_sec_max)
        setv("job_revisit_p", farm.revisit_url_probability)
        setv("job_engagement_max", farm.engagement_budget_sec_max)
        show_cursor_bv.set(bool(farm.show_synthetic_mouse))
        ent_mc = entries.get("job_mass_count")
        if ent_mc:
            ent_mc.delete(0, "end")
            ent_mc.insert(
                0,
                _ACCOUNTS_PER_RUN_INF if farm.mass_sites_count == 0 else str(farm.mass_sites_count),
            )

        p = s.proxy
        proxy_source_sv.set(p.source)
        proxy_today_bv.set(bool(p.use_today_list))
        setv("proxy_host", p.host)
        setv("proxy_port_prefix", int(p.port_prefix))
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

        try:
            mass_sites_count = _parse_accounts_per_run_display(entries["job_mass_count"].get())
        except ValueError as exc:
            raise ValueError(
                "Кол-во: ожидается целое число ≥ 0, символ ∞ или пусто (все URL из файла)"
            ) from exc
        if mass_sites_count < 0 or mass_sites_count > 100_000:
            raise ValueError("Кол-во: допустимы значения от 0 до 100000")

        farming_cfg = FarmingConfig(
            important_sites_minutes_per_site=_parse_int(
                entries["job_important_min"].get(), "farming.important_sites_minutes_per_site"
            ),
            mass_sites_minutes_per_site=_parse_int(
                entries["job_mass_min"].get(), "farming.mass_sites_minutes_per_site"
            ),
            mass_sites_count=mass_sites_count,
            cookie_banner_timeout_sec=_parse_float(
                entries["job_cookie_timeout"].get(), "farming.cookie_banner_timeout_sec"
            ),
            between_nav_delay_sec_min=_parse_float(
                entries["job_nav_dly_min"].get(), "farming.between_nav_delay_sec_min"
            ),
            between_nav_delay_sec_max=_parse_float(
                entries["job_nav_dly_max"].get(), "farming.between_nav_delay_sec_max"
            ),
            revisit_url_probability=_parse_float(
                entries["job_revisit_p"].get(), "farming.revisit_url_probability"
            ),
            engagement_budget_sec_max=_parse_float(
                entries["job_engagement_max"].get(), "farming.engagement_budget_sec_max"
            ),
            show_synthetic_mouse=bool(show_cursor_bv.get()),
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
            port_prefix=_parse_int(entries["proxy_port_prefix"].get(), "proxy.port_prefix"),
            isp=proxy_isp_sv.get().strip(),
            read_timed_out_ban_sec=float(_parse_float(entries["proxy_ban_sec"].get(), "proxy.read_timed_out_ban_sec")),
            max_retries_per_profile=base.proxy.max_retries_per_profile,
        )

        try:
            accounts_per_run = _parse_accounts_per_run_display(entries["thr_accounts_per_run"].get())
        except ValueError as exc:
            raise ValueError(
                "Выполнений: ожидается целое число ≥ 0, символ ∞ или пусто для безлимита"
            ) from exc
        if accounts_per_run < 0 or accounts_per_run > 1_000_000:
            raise ValueError("Выполнений: допустимы значения от 0 до 1000000")

        return AppSettings(
            threads=_parse_int(entries["thr_threads"].get(), "threads"),
            accounts_per_run=accounts_per_run,
            action_delay_sec_min=_parse_float(entries["thr_delay_min"].get(), "action_delay_sec_min"),
            action_delay_sec_max=_parse_float(entries["thr_delay_max"].get(), "action_delay_sec_max"),
            navigation_timeout_ms=base.navigation_timeout_ms,
            network_idle_timeout_ms=base.network_idle_timeout_ms,
            ads_power=base.ads_power,
            google_sheets=gs,
            timezone=base.timezone,
            mass_sites_file=base.mass_sites_file,
            farming=farming_cfg,
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
    show_cursor_bv = BooleanVar(value=False)
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
    ctk.CTkCheckBox(
        fm,
        text="Отображение курсора",
        variable=show_cursor_bv,
    ).grid(row=row_m, column=0, columnspan=2, sticky="w", padx=8, pady=4)
    row_m += 1

    ft = ctk.CTkScrollableFrame(tab_thr)
    ft.pack(fill="both", expand=True)
    ft.grid_columnconfigure(1, weight=1)
    rt = 0

    def bump_threads(delta: int) -> None:
        ent = entries.get("thr_threads")
        if ent is None:
            return
        raw = ent.get().strip() or "1"
        try:
            v = int(raw)
        except ValueError:
            v = 1
        v = max(1, min(64, v + delta))
        ent.delete(0, "end")
        ent.insert(0, str(v))

    ctk.CTkLabel(ft, text="Потоков").grid(row=rt, column=0, sticky="w", padx=8, pady=4)
    row_threads = ctk.CTkFrame(ft, fg_color="transparent")
    row_threads.grid(row=rt, column=1, sticky="w", padx=8, pady=4)
    ctk.CTkButton(
        row_threads,
        text="−",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_threads(-1),
    ).pack(side="left", padx=(0, 6))
    e_threads = ctk.CTkEntry(row_threads, width=100, justify="center", height=26)
    e_threads.pack(side="left", padx=(0, 6))
    entries["thr_threads"] = e_threads
    e_threads.insert(0, "2")
    ctk.CTkButton(
        row_threads,
        text="+",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_threads(1),
    ).pack(side="left")
    rt += 1

    def bump_accounts_per_run(delta: int) -> None:
        ent = entries.get("thr_accounts_per_run")
        if ent is None:
            return
        try:
            v = _parse_accounts_per_run_display(ent.get())
        except ValueError:
            v = 0
        v = max(0, min(1_000_000, v + delta))
        ent.delete(0, "end")
        ent.insert(0, _ACCOUNTS_PER_RUN_INF if v == 0 else str(v))

    ctk.CTkLabel(ft, text="Выполнений").grid(row=rt, column=0, sticky="w", padx=8, pady=4)
    row_apr = ctk.CTkFrame(ft, fg_color="transparent")
    row_apr.grid(row=rt, column=1, sticky="w", padx=8, pady=4)
    ctk.CTkButton(
        row_apr,
        text="−",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_accounts_per_run(-1),
    ).pack(side="left", padx=(0, 6))
    e_apr = ctk.CTkEntry(row_apr, width=100, justify="center", height=26)
    e_apr.pack(side="left", padx=(0, 6))
    entries["thr_accounts_per_run"] = e_apr
    e_apr.insert(0, _ACCOUNTS_PER_RUN_INF)
    ctk.CTkButton(
        row_apr,
        text="+",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_accounts_per_run(1),
    ).pack(side="left")
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

    def bump_job_minutes(key: str, delta: int) -> None:
        ent = entries.get(key)
        if ent is None:
            return
        raw = ent.get().strip() or "0"
        try:
            v = int(raw)
        except ValueError:
            v = 0
        v = max(0, min(24 * 60, v + delta))
        ent.delete(0, "end")
        ent.insert(0, str(v))

    ctk.CTkLabel(fj, text="Основные сайты, мин").grid(row=rj, column=0, sticky="w", padx=(8, 2), pady=4)
    row_ji = ctk.CTkFrame(fj, fg_color="transparent")
    row_ji.grid(row=rj, column=1, sticky="w", padx=(0, 8), pady=4)
    ctk.CTkButton(
        row_ji,
        text="−",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_job_minutes("job_important_min", -1),
    ).pack(side="left", padx=(0, 3))
    e_ji = ctk.CTkEntry(row_ji, width=64, justify="center", height=26)
    e_ji.pack(side="left", padx=(0, 3))
    entries["job_important_min"] = e_ji
    e_ji.insert(0, "5")
    ctk.CTkButton(
        row_ji,
        text="+",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_job_minutes("job_important_min", 1),
    ).pack(side="left")
    rj += 1

    def bump_mass_count(delta: int) -> None:
        ent = entries.get("job_mass_count")
        if ent is None:
            return
        try:
            v = _parse_accounts_per_run_display(ent.get())
        except ValueError:
            v = 0
        v = max(0, min(100_000, v + delta))
        ent.delete(0, "end")
        ent.insert(0, _ACCOUNTS_PER_RUN_INF if v == 0 else str(v))

    ctk.CTkLabel(fj, text="Доп сайты, мин").grid(row=rj, column=0, sticky="w", padx=(8, 2), pady=4)
    row_jm = ctk.CTkFrame(fj, fg_color="transparent")
    row_jm.grid(row=rj, column=1, sticky="w", padx=(0, 8), pady=4)
    ctk.CTkButton(
        row_jm,
        text="−",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_job_minutes("job_mass_min", -1),
    ).pack(side="left", padx=(0, 3))
    e_jm = ctk.CTkEntry(row_jm, width=64, justify="center", height=26)
    e_jm.pack(side="left", padx=(0, 3))
    entries["job_mass_min"] = e_jm
    e_jm.insert(0, "5")
    ctk.CTkButton(
        row_jm,
        text="+",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_job_minutes("job_mass_min", 1),
    ).pack(side="left")

    ctk.CTkLabel(fj, text="Кол-во").grid(row=rj, column=2, sticky="w", padx=(6, 2), pady=4)
    row_jc = ctk.CTkFrame(fj, fg_color="transparent")
    row_jc.grid(row=rj, column=3, sticky="w", padx=(0, 8), pady=4)
    ctk.CTkButton(
        row_jc,
        text="−",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_mass_count(-1),
    ).pack(side="left", padx=(0, 3))
    e_jc = ctk.CTkEntry(row_jc, width=64, justify="center", height=26)
    e_jc.pack(side="left", padx=(0, 3))
    entries["job_mass_count"] = e_jc
    e_jc.insert(0, _ACCOUNTS_PER_RUN_INF)
    ctk.CTkButton(
        row_jc,
        text="+",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_mass_count(1),
    ).pack(side="left")
    rj += 1

    ctk.CTkLabel(fj, text="Поведение нагула", font=("", 13, "bold")).grid(
        row=rj, column=0, columnspan=2, sticky="w", padx=8, pady=(12, 4)
    )
    rj += 1
    add_labeled_entry(fj, rj, "Таймаут баннера cookie (сек)", "job_cookie_timeout")
    rj += 1
    add_labeled_entry(fj, rj, "Пауза между переходами, мин (сек)", "job_nav_dly_min")
    rj += 1
    add_labeled_entry(fj, rj, "Пауза между переходами, макс (сек)", "job_nav_dly_max")
    rj += 1
    add_labeled_entry(fj, rj, "Вероятность повторного URL (0–0.2)", "job_revisit_p")
    rj += 1
    add_labeled_entry(fj, rj, "Макс. сек «живых» действий за паузу", "job_engagement_max")
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

    def bump_proxy_port_prefix(delta: int) -> None:
        ent = entries.get("proxy_port_prefix")
        if ent is None:
            return
        raw = ent.get().strip() or "600"
        try:
            v = int(raw)
        except ValueError:
            v = 600
        v = max(100, min(999, v + delta))
        ent.delete(0, "end")
        ent.insert(0, str(v))

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
    ctk.CTkLabel(fp, text="Порт: первые 3 цифры (100–999)").grid(row=rp, column=0, sticky="w", padx=8, pady=4)
    counter_row = ctk.CTkFrame(fp, fg_color="transparent")
    counter_row.grid(row=rp, column=1, sticky="w", padx=8, pady=4)
    ctk.CTkButton(
        counter_row,
        text="−",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_proxy_port_prefix(-1),
    ).pack(side="left", padx=(0, 6))
    e_prefix = ctk.CTkEntry(counter_row, width=100, justify="center", height=26)
    e_prefix.pack(side="left", padx=(0, 6))
    entries["proxy_port_prefix"] = e_prefix
    e_prefix.insert(0, "600")
    ctk.CTkButton(
        counter_row,
        text="+",
        width=28,
        height=26,
        font=("", 14),
        command=lambda: bump_proxy_port_prefix(1),
    ).pack(side="left")
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

    update_busy: list[bool] = [False]

    def _finish_update_button() -> None:
        update_busy[0] = False
        btn_update.configure(state="normal", text="Проверить обновление")

    def _quit_for_update() -> None:
        try:
            wf.write("QUIT\n")
            wf.flush()
        except Exception:
            pass

        def _exit() -> None:
            try:
                wf.close()
                rf.close()
                sock.close()
            except Exception:
                pass
            root.destroy()
            raise SystemExit(0)

        root.after(400, _exit)

    def _confirm_and_apply(staged, info) -> None:
        if not messagebox.askyesno(
            "Обновление",
            f"Версия {info.latest_version} загружена.\n"
            "Закрыть Cookie Grabber и установить обновление сейчас?",
        ):
            _finish_update_button()
            return
        try:
            launch_apply_update(staged)
        except Exception as exc:
            messagebox.showerror("Обновление", f"Не удалось запустить установку:\n{exc}")
            _finish_update_button()
            return
        messagebox.showinfo(
            "Обновление",
            "Установка запущена. Приложение закроется и откроется снова после замены файлов.",
        )
        _quit_for_update()

    def on_check_update() -> None:
        if update_busy[0]:
            return
        update_busy[0] = True
        btn_update.configure(state="disabled", text="Проверка…")

        def worker() -> None:
            err: str | None = None
            info = None
            try:
                info = check_for_update()
            except Exception as exc:
                err = str(exc)

            def ui() -> None:
                if err is not None:
                    messagebox.showerror("Обновление", f"Не удалось проверить обновления:\n{err}")
                    _finish_update_button()
                    return
                assert info is not None
                if not info.has_update:
                    messagebox.showinfo(
                        "Обновление",
                        f"Установлена актуальная версия ({info.current_version}).",
                    )
                    _finish_update_button()
                    return
                if not getattr(sys, "frozen", False):
                    messagebox.showinfo(
                        "Обновление",
                        f"Доступна версия {info.latest_version} (сейчас {info.current_version}).\n"
                        "Автоустановка только в сборке .exe; откроется страница релиза.",
                    )
                    webbrowser.open(info.release_url)
                    _finish_update_button()
                    return
                if not messagebox.askyesno(
                    "Обновление",
                    f"Доступна версия {info.latest_version} (сейчас {info.current_version}).\n"
                    "Скачать и установить?",
                ):
                    _finish_update_button()
                    return
                btn_update.configure(text="Загрузка…")

                def download_worker() -> None:
                    try:
                        staged = download_and_stage_update(info)
                    except Exception as exc:
                        root.after(
                            0,
                            lambda: (
                                messagebox.showerror(
                                    "Обновление", f"Не удалось загрузить обновление:\n{exc}"
                                ),
                                _finish_update_button(),
                            ),
                        )
                        return
                    root.after(0, lambda: _confirm_and_apply(staged, info))

                threading.Thread(target=download_worker, daemon=True).start()

            root.after(0, ui)

        threading.Thread(target=worker, daemon=True).start()

    btn_update = ctk.CTkButton(
        toolbar,
        text="Проверить обновление",
        command=on_check_update,
        width=150,
    )
    btn_update.pack(side="right", padx=4)

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
