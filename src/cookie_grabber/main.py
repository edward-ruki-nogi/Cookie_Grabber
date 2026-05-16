from __future__ import annotations

import logging
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from cookie_grabber.ads.ads_power_api import AdsPowerApi
from cookie_grabber.config.settings import (
    SETTINGS_SOURCE_PATH,
    AppSettings,
    load_settings,
    save_settings_patch,
)
from cookie_grabber.control.runtime_control import RunControl
from cookie_grabber.log_bus import LogBus, setup_logging
from gala_9static_proxy import ProxyPortPool, TodayListCache

from cookie_grabber.grabber_proxy import ProxyAllocator, build_validation_timeout_state
from cookie_grabber.sheets.google_sheets_api import GoogleSheetsApi
from cookie_grabber.workers.profile_worker import run_account_loop

logger = logging.getLogger(__name__)
console = Console()


class Orchestrator:
    def __init__(self, settings: AppSettings, log_bus: LogBus, project_root: Path) -> None:
        self.settings = settings
        self.log_bus = log_bus
        self.project_root = project_root
        self.control = RunControl()
        self._exec_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._futures: list = []
        self._shared_ads: list[AdsPowerApi] = []
        self._shared_allocator: ProxyAllocator | None = None
        self._shared_port_pool: ProxyPortPool | None = None
        self._shared_today_list_cache: TodayListCache | None = None

    def start(self) -> None:
        with self._exec_lock:
            if not (self.settings.google_sheets.spreadsheet_id or "").strip():
                logger.warning("Заполните google_sheets.spreadsheet_id в config/settings.yaml")
                return
            if self._executor is not None:
                logger.warning("Уже запущено. Дождитесь остановки или завершите (shutdown).")
                return

            sheets = GoogleSheetsApi(self.settings)
            self.control.prepare_new_run(self.settings.accounts_per_run)
            port_pool = ProxyPortPool()
            timeout_state = build_validation_timeout_state(self.settings)
            today_cache = TodayListCache() if self.settings.proxy.use_today_list else None
            self._shared_port_pool = port_pool
            self._shared_today_list_cache = today_cache
            self._shared_allocator = ProxyAllocator(
                self.settings,
                port_pool,
                timeout_state,
                today_list_cache=today_cache,
            )

            n = max(1, int(self.settings.threads))
            self._executor = ThreadPoolExecutor(max_workers=n, thread_name_prefix="acc")
            self._futures = []
            self._shared_ads = []
            for _ in range(n):
                ads = AdsPowerApi(self.settings)
                self._shared_ads.append(ads)
                fut = self._executor.submit(
                    run_account_loop,
                    self.settings,
                    sheets,
                    ads,
                    self._shared_allocator,
                    self.control,
                )
                self._futures.append(fut)
            logger.info("Запущено воркеров аккаунтов: %s", len(self._futures))
            futs_snapshot = list(self._futures)
            threading.Thread(
                target=self._finalize_after_pool_workers_done,
                args=(futs_snapshot,),
                daemon=True,
            ).start()

    def pause(self) -> None:
        self.control.pause.set()
        logger.info("Пауза: новые действия воркеров приостановлены между URL.")

    def resume(self) -> None:
        self.control.pause.clear()
        logger.info("Продолжение.")

    def reload_settings(self) -> None:
        try:
            self.settings = load_settings(self.project_root)
            logger.info("Настройки перезагружены.")
        except Exception:
            logger.exception("Ошибка перезагрузки настроек")

    def request_safe_stop(self) -> None:
        self.control.safe_stop.set()
        logger.info("Мягкое завершение (safe_stop): между URL воркеры остановятся.")
        threading.Thread(target=self._await_workers_after_safe_stop, daemon=True).start()

    def _await_workers_after_safe_stop(self) -> None:
        while True:
            time.sleep(0.25)
            with self._exec_lock:
                ex = self._executor
                futs = list(self._futures)
                if ex is None or not futs:
                    return
                if all(f.done() for f in futs):
                    try:
                        ex.shutdown(wait=True, cancel_futures=False)
                    except Exception:
                        logger.exception("Ошибка при shutdown пула после мягкой остановки")
                    finally:
                        self._cleanup_shared()
                    logger.info("Мягкая остановка: все задачи завершены, пул освобождён.")
                    return

    def _finalize_after_pool_workers_done(self, futs: list) -> None:
        """Когда все воркеры ``run_account_loop`` завершились (квота, safe_stop из воркера и т.д.), освободить пул."""
        if not futs:
            return
        wait(futs, return_when=ALL_COMPLETED)
        with self._exec_lock:
            ex = self._executor
            if ex is None:
                return
            try:
                ex.shutdown(wait=True, cancel_futures=False)
            except Exception:
                logger.exception("Ошибка при shutdown пула после завершения воркеров")
            finally:
                self._cleanup_shared()
        logger.info("Все воркеры аккаунтов завершились, пул освобождён.")

    def _cleanup_shared(self) -> None:
        self._executor = None
        self._futures.clear()
        for a in self._shared_ads:
            try:
                a.close()
            except Exception:
                pass
        self._shared_ads.clear()
        if self._shared_allocator is not None:
            try:
                self._shared_allocator.close()
            except Exception:
                pass
            self._shared_allocator = None
        self._shared_port_pool = None
        self._shared_today_list_cache = None

    def request_shutdown(self) -> None:
        with self._exec_lock:
            self.control.shutdown.set()
            self.control.pause.clear()
            ex = self._executor
            if ex is not None:
                try:
                    ex.shutdown(wait=False, cancel_futures=True)
                finally:
                    self._cleanup_shared()
        logger.info("Резкая остановка (флаг shutdown + cancel futures).")

    def toggle_important_site(self, site_id: str) -> bool | None:
        """Переключить ``enabled`` для важного сайта и сохранить в ``settings.yaml``.

        Возвращает новое значение, либо ``None`` если сайт не найден.
        """
        target = None
        for s in self.settings.important_sites:
            if s.id == site_id:
                target = s
                break
        if target is None:
            logger.warning("Toggle: сайт %s не найден в important_sites", site_id)
            return None
        new_value = not bool(target.enabled)
        target.enabled = new_value
        patch = {
            "important_sites": [
                {
                    "id": s.id,
                    "url": s.url,
                    "enabled": s.enabled,
                    "referer": s.referer,
                }
                for s in self.settings.important_sites
            ]
        }
        try:
            save_settings_patch(self.project_root, patch)
        except Exception:
            logger.exception("Не удалось сохранить config/settings.yaml")
        return new_value

    def list_important_sites(self) -> list[tuple[str, bool, str]]:
        return [(s.id, bool(s.enabled), s.url) for s in self.settings.important_sites]

    def stop(self) -> None:
        """Совместимость с кодом терминального меню: то же, что ``request_shutdown``."""
        self.request_shutdown()

    def describe_paths_line(self) -> str:
        r = self.project_root
        return "\t".join(
            [
                str(r),
                str(SETTINGS_SOURCE_PATH),
                str(r / "config" / "settings.yaml"),
            ]
        )


def _menu_inline(root: Path) -> None:
    try:
        settings = load_settings(root)
    except Exception as exc:
        console.print(f"[red]Ошибка загрузки настроек: {exc}[/red]")
        raise SystemExit(1) from exc

    log_bus = setup_logging(LogBus(), level=logging.INFO)
    orch = Orchestrator(settings, log_bus, root)

    while True:
        console.print(
            "\n[bold]Cookie Grabber[/bold]\n"
            "1) Запуск\n"
            "2) Пауза\n"
            "3) Продолжить\n"
            "4) Остановка (shutdown)\n"
            "5) Логи (live, Ctrl+C назад)\n"
            "6) Путь к настройкам\n"
            "7) Перезагрузить настройки с диска\n"
            "8) Завершение (safe_stop)\n"
            "9) Сайты (PP / WH / Kwiff)\n"
            "0) Выход"
        )
        choice = console.input("> ").strip()
        if choice == "1":
            orch.start()
        elif choice == "2":
            orch.pause()
        elif choice == "3":
            orch.resume()
        elif choice == "4":
            orch.request_shutdown()
        elif choice == "5":
            try:
                with Live(Panel("", title="Logs"), refresh_per_second=8, console=console) as live:
                    buf: list[str] = []
                    while True:
                        for line in log_bus.drain():
                            buf.append(line)
                            buf = buf[-200:]
                        live.update(Panel("\n".join(buf) or "…", title="Logs"))
                        time.sleep(0.12)
            except KeyboardInterrupt:
                pass
        elif choice == "6":
            t = Table(show_header=False)
            t.add_row("Корень проекта", str(root))
            t.add_row("дефолты (встроены в settings.py)", str(SETTINGS_SOURCE_PATH))
            t.add_row("settings (переопределения, если есть)", str(root / "config" / "settings.yaml"))
            console.print(t)
        elif choice == "7":
            orch.reload_settings()
        elif choice == "8":
            orch.request_safe_stop()
        elif choice == "9":
            _sites_submenu(orch)
        elif choice == "0":
            orch.stop()
            break
        else:
            console.print("[dim]Неизвестная команда[/dim]")


def _sites_submenu(orch: Orchestrator) -> None:
    while True:
        sites = orch.list_important_sites()
        t = Table(title="Важные сайты (флаг enabled)")
        t.add_column("№")
        t.add_column("ID")
        t.add_column("Включён")
        t.add_column("URL")
        for i, (sid, enabled, url) in enumerate(sites, start=1):
            t.add_row(str(i), sid, "✓" if enabled else "—", url)
        console.print(t)
        console.print("[dim]Введите номер для переключения, 0 — назад[/dim]")
        raw = console.input("> ").strip()
        if raw == "0" or raw == "":
            return
        try:
            idx = int(raw)
        except ValueError:
            console.print("[dim]Не число[/dim]")
            continue
        if idx < 1 or idx > len(sites):
            console.print("[dim]Вне диапазона[/dim]")
            continue
        sid = sites[idx - 1][0]
        new_val = orch.toggle_important_site(sid)
        if new_val is None:
            console.print(f"[red]Сайт {sid} не найден[/red]")
        else:
            console.print(f"[green]{sid}: enabled = {new_val}[/green]")


_ONE_LINE_MARKER = "###PATHS_ONE_LINE###"
_SITES_LINE_MARKER = "###SITES_LIST###"


def _parse_sites_payload(payload: str) -> list[tuple[str, bool, str]]:
    rest = payload.removeprefix(_SITES_LINE_MARKER)
    out: list[tuple[str, bool, str]] = []
    if not rest:
        return out
    for chunk in rest.split("\u001f"):
        parts = chunk.split("|", 2)
        if len(parts) != 3:
            continue
        sid, enabled_s, url = parts
        out.append((sid, enabled_s == "1", url))
    return out


def _sites_remote_submenu(rf, wf) -> None:
    while True:
        wf.write("SITES\n")
        wf.flush()
        line = rf.readline()
        if not line:
            console.print("[red]Сервер закрыл соединение.[/red]")
            return
        tail = line.rstrip("\n")
        parts = tail.split("\t", 1)
        if len(parts) != 2 or parts[0] != "OK":
            console.print(f"[red]{tail}[/red]")
            return
        sites = _parse_sites_payload(parts[1])
        t = Table(title="Важные сайты (флаг enabled)")
        t.add_column("№")
        t.add_column("ID")
        t.add_column("Включён")
        t.add_column("URL")
        for i, (sid, enabled, url) in enumerate(sites, start=1):
            t.add_row(str(i), sid, "✓" if enabled else "—", url)
        console.print(t)
        console.print("[dim]Введите номер для переключения, 0 — назад[/dim]")
        raw = console.input("> ").strip()
        if raw == "0" or raw == "":
            return
        try:
            idx = int(raw)
        except ValueError:
            console.print("[dim]Не число[/dim]")
            continue
        if idx < 1 or idx > len(sites):
            console.print("[dim]Вне диапазона[/dim]")
            continue
        sid = sites[idx - 1][0]
        wf.write(f"TOGGLE\t{sid}\n")
        wf.flush()
        line = rf.readline()
        if not line:
            console.print("[red]Сервер закрыл соединение.[/red]")
            return
        tail = line.rstrip("\n")
        parts = tail.split("\t", 1)
        if len(parts) != 2:
            console.print(f"[red]{tail}[/red]")
            continue
        if parts[0] == "OK":
            console.print(f"[green]{parts[1]}[/green]")
        else:
            console.print(f"[red]{parts[1]}[/red]")


def _dispatch_rpc(cmd: str, orch: Orchestrator) -> tuple[str, str]:
    if cmd == "START":
        orch.start()
        return "OK", "Команда «Запуск» отправлена (смотрите логи в терминале Cursor)."
    if cmd == "PAUSE":
        orch.pause()
        return "OK", "Пауза."
    if cmd == "RESUME":
        orch.resume()
        return "OK", "Продолжение."
    if cmd == "STOP" or cmd == "SHUTDOWN":
        orch.request_shutdown()
        return "OK", "Shutdown отправлен."
    if cmd == "SAFE_STOP":
        orch.request_safe_stop()
        return "OK", "Safe_stop отправлен."
    if cmd == "PATHS":
        line = orch.describe_paths_line()
        return "OK", f"{_ONE_LINE_MARKER}{line}"
    if cmd == "RELOAD":
        orch.reload_settings()
        return "OK", "Перезагрузка выполнена (или см. ошибки в терминале Cursor)."
    if cmd == "SITES":
        rows = orch.list_important_sites()
        # Формат: id|enabled(0/1)|url ; разделитель между сайтами — '\u001f'
        parts = [f"{sid}|{int(enabled)}|{url}" for sid, enabled, url in rows]
        return "OK", _SITES_LINE_MARKER + "\u001f".join(parts)
    if cmd.startswith("TOGGLE\t"):
        sid = cmd.split("\t", 1)[1].strip()
        new_val = orch.toggle_important_site(sid)
        if new_val is None:
            return "ERR", f"Сайт {sid} не найден"
        return "OK", f"{sid}: enabled = {new_val}"
    if cmd == "QUIT":
        orch.request_shutdown()
        return "OK", "Выход."
    return "ERR", "Неизвестная команда"


def _run_menu_rpc_host(project_root: Path) -> None:
    try:
        settings = load_settings(project_root)
    except Exception as exc:
        logger.error("Ошибка загрузки настроек: %s", exc)
        raise SystemExit(1) from exc

    log_bus = LogBus()
    setup_logging(log_bus, level=logging.INFO)
    orch = Orchestrator(settings, log_bus, project_root)

    _preflight_gui_dependencies()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    srv.settimeout(120)

    proc: subprocess.Popen | None = None
    conn: socket.socket | None = None
    try:
        proc = _spawn_detached_gui_client(project_root, port)
        try:
            conn, _ = srv.accept()
        except TimeoutError:
            logger.error(
                "Окно GUI не подключилось за 120 с. Варианты: "
                "`python -m cookie_grabber.main --inline-menu`; "
                "проверьте лог ошибок процесса GUI: `%s`",
                _gui_client_log_path(project_root),
            )
            if proc is not None and proc.poll() is not None:
                logger.error("Процесс GUI уже завершился с кодом %s.", proc.returncode)
            _log_gui_stderr_tail(project_root)
            raise SystemExit(1) from None
        conn.settimeout(None)
        fr = conn.makefile("r", encoding="utf-8", newline="\n")
        fw = conn.makefile("w", encoding="utf-8", newline="\n")
        try:
            for line in fr:
                cmd = line.strip()
                if not cmd:
                    continue
                kind, payload = _dispatch_rpc(cmd, orch)
                fw.write(f"{kind}\t{payload}\n")
                fw.flush()
                if cmd == "QUIT":
                    break
        finally:
            fw.close()
            fr.close()
    except KeyboardInterrupt:
        logger.info("Прерывание, остановка…")
        orch.request_shutdown()
    finally:
        srv.close()
        if conn:
            conn.close()
        if proc is not None and proc.poll() is None:
            proc.terminate()


def _gui_client_log_path(project_root: Path) -> Path:
    return project_root / "gui_client_last_error.log"


def _log_gui_stderr_tail(project_root: Path, max_chars: int = 6000) -> None:
    path = _gui_client_log_path(project_root)
    try:
        txt = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return
    if not txt:
        return
    logger.error(
        "Фрагмент вывода GUI-процесса (%s):\n%s",
        path,
        txt[-max_chars:],
    )


def _preflight_gui_dependencies() -> None:
    try:
        import customtkinter as _ctk  # noqa: F401
    except ImportError:
        logger.error(
            "Не установлен `customtkinter`. Выполните: pip install customtkinter  "
            "или запускайте без GUI: python -m cookie_grabber.main --inline-menu"
        )
        raise SystemExit(1) from None


def _spawn_detached_gui_client(project_root: Path, port: int) -> subprocess.Popen:
    log_path = _gui_client_log_path(project_root)
    log_f = log_path.open("w", encoding="utf-8")
    args = [
        sys.executable,
        "-m",
        "cookie_grabber.gui_spawn",
        "127.0.0.1",
        str(port),
    ]
    kwargs: dict[str, object] = {
        "cwd": str(project_root),
        "stderr": log_f,
        "stdout": log_f,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
    return subprocess.Popen(args, **kwargs)  # type: ignore[arg-type]


def _spawn_detached_menu_client(project_root: Path, port: int) -> subprocess.Popen:
    args = [
        sys.executable,
        "-m",
        "cookie_grabber.main",
        "--menu-client",
        "127.0.0.1",
        str(port),
    ]
    kwargs: dict = {"cwd": str(project_root)}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
    return subprocess.Popen(args, **kwargs)


def _rpc_host_menu_fallback(project_root: Path) -> None:
    """Резервное Rich-меню если явно нужен текстовый клиент."""
    try:
        settings = load_settings(project_root)
    except Exception as exc:
        logger.error("Ошибка загрузки настроек: %s", exc)
        raise SystemExit(1) from exc

    log_bus = LogBus()
    setup_logging(log_bus, level=logging.INFO)
    orch = Orchestrator(settings, log_bus, project_root)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    srv.settimeout(120)

    proc: subprocess.Popen | None = None
    conn: socket.socket | None = None
    try:
        proc = _spawn_detached_menu_client(project_root, port)
        try:
            conn, _ = srv.accept()
        except TimeoutError:
            logger.error("Клиент меню не подключился за 120 с.")
            raise SystemExit(1) from None
        conn.settimeout(None)
        fr = conn.makefile("r", encoding="utf-8", newline="\n")
        fw = conn.makefile("w", encoding="utf-8", newline="\n")
        try:
            for line in fr:
                cmd = line.strip()
                if not cmd:
                    continue
                kind, payload = _dispatch_rpc(cmd, orch)
                fw.write(f"{kind}\t{payload}\n")
                fw.flush()
                if cmd == "QUIT":
                    break
        finally:
            fw.close()
            fr.close()
    except KeyboardInterrupt:
        logger.info("Прерывание, остановка…")
        orch.request_shutdown()
    finally:
        srv.close()
        if conn:
            conn.close()
        if proc is not None and proc.poll() is None:
            proc.terminate()


def _run_menu_rpc_client(host: str, port: int) -> None:
    try:
        sock = socket.create_connection((host, port))
    except OSError as exc:
        console.print(f"[red]Не удалось подключиться к меню-серверу: {exc}[/red]")
        raise SystemExit(1) from exc

    ctrl_c_note = "[dim]Ctrl+C здесь только отключает окно меню (shutdown — пункт 4).[/dim]"

    with sock:
        rf = sock.makefile("r", encoding="utf-8", newline="\n")
        wf = sock.makefile("w", encoding="utf-8", newline="\n")
        console.print("[bold]Cookie Grabber — меню[/bold]")
        console.print("[dim]Логи приложения выводятся в терминале Cursor.[/dim]")
        console.print(ctrl_c_note)
        try:
            while True:
                console.print(
                    "\n1) Запуск\n"
                    "2) Пауза\n"
                    "3) Продолжить\n"
                    "4) Остановка (shutdown)\n"
                    "5) Подсказка по логам\n"
                    "6) Путь к настройкам\n"
                    "7) Перезагрузить настройки с диска\n"
                    "8) Завершение (safe_stop)\n"
                    "9) Сайты (PP / WH / Kwiff)\n"
                    "0) Выход"
                )
                choice = console.input("> ").strip()
                cmd_map = {
                    "1": "START",
                    "2": "PAUSE",
                    "3": "RESUME",
                    "4": "SHUTDOWN",
                    "6": "PATHS",
                    "7": "RELOAD",
                    "8": "SAFE_STOP",
                    "0": "QUIT",
                }
                if choice == "5":
                    console.print(
                        "[dim]Весь поток логов идёт в окно терминала, из которого "
                        "запущен cookie-grabber (Cursor).[/dim]"
                    )
                    continue
                if choice == "9":
                    _sites_remote_submenu(rf, wf)
                    continue
                if choice not in cmd_map:
                    console.print("[dim]Неизвестная команда[/dim]")
                    continue
                wf.write(cmd_map[choice] + "\n")
                wf.flush()
                line = rf.readline()
                if not line:
                    console.print("[red]Сервер закрыл соединение.[/red]")
                    break
                tail = line.rstrip("\n")
                parts = tail.split("\t", 1)
                if len(parts) != 2:
                    console.print(f"[red]{tail}[/red]")
                    continue
                prefix, payload = parts
                if prefix == "OK":
                    if payload.startswith(_ONE_LINE_MARKER):
                        rest = payload.removeprefix(_ONE_LINE_MARKER).split("\t", 2)
                        if len(rest) == 3:
                            t = Table(show_header=False)
                            t.add_row("Корень проекта", rest[0])
                            t.add_row("дефолты (встроены в settings.py)", rest[1])
                            t.add_row("settings (переопределения, если есть)", rest[2])
                            console.print(t)
                        continue
                    console.print(f"[green]{payload}[/green]")
                    if cmd_map.get(choice) == "QUIT":
                        break
                else:
                    console.print(f"[red]{payload}[/red]")
        except KeyboardInterrupt:
            console.print(f"\n{ctrl_c_note}")
            wf.write("QUIT\n")
            wf.flush()
            _ = rf.readline()


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]

    if "--gui-client" in argv:
        i = argv.index("--gui-client")
        try:
            host = argv[i + 1]
            port = int(argv[i + 2])
        except (IndexError, ValueError):
            sys.stderr.write("Использование: --gui-client HOST PORT\n")
            raise SystemExit(2)
        from cookie_grabber.ui.app_window import run_gui_client

        try:
            run_gui_client(host, port)
        except KeyboardInterrupt:
            pass
        return

    if "--menu-client" in argv:
        i = argv.index("--menu-client")
        try:
            host = argv[i + 1]
            port = int(argv[i + 2])
        except (IndexError, ValueError):
            sys.stderr.write("Использование: --menu-client HOST PORT\n")
            raise SystemExit(2)
        try:
            _run_menu_rpc_client(host, port)
        except KeyboardInterrupt:
            console.print("\n[dim]Отключено окно меню[/dim]")
        return

    project_root = Path.cwd()
    use_inline_menu = "--inline-menu" in argv
    use_console_detach = "--detach-console-menu" in argv

    try:
        if sys.platform == "win32" and not use_inline_menu:
            if use_console_detach:
                _rpc_host_menu_fallback(project_root)
            else:
                _run_menu_rpc_host(project_root)
            return
        _menu_inline(project_root)
    except KeyboardInterrupt:
        console.print("\n[dim]Выход[/dim]")


if __name__ == "__main__":
    main()
