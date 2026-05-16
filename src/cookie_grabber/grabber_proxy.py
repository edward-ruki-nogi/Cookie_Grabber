"""Прокси «9 static»: gala_9static_proxy + порт {port_prefix}{suffix} (5 цифр), ADS payload."""

from __future__ import annotations

import atexit
import logging
import re
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from multiprocessing import Event
from typing import Iterator

from gala_9static_proxy import (
    ProxyPortPool,
    ProxyRunMode,
    StopAfter9ProxyDialog,
    TodayListCache,
    ValidationTimeoutState,
    acquire_9static_port_and_config,
    validate_and_activate_proxy,
    wait_unban_if_needed,
    worker_proxy_cwd,
)

from cookie_grabber.config.settings import AppSettings

logger = logging.getLogger(__name__)

# Живые аллокаторы — при нормальном завершении процесса сбрасываем висящие суффиксы.
_proxy_allocator_registry: weakref.WeakSet = weakref.WeakSet()


def _atexit_release_all_proxy_suffixes() -> None:
    for alloc in list(_proxy_allocator_registry):
        try:
            alloc._release_all_tracked_suffixes()
        except Exception:
            logger.exception("atexit: сброс суффиксов прокси")


atexit.register(_atexit_release_all_proxy_suffixes)

# Уже заданный в ADS суффикс « - {порт}» снимаем, чтобы при смене прокси не дублировать.
_ADS_PROFILE_PORT_SUFFIX = re.compile(r" - \d+$")


def format_ads_profile_name(account_name: str, proxy_port: int) -> str:
    """Имя профиля ADS: «{имя} - {порт}» (лимит ADS на name — 100 символов)."""
    base = _ADS_PROFILE_PORT_SUFFIX.sub("", (account_name or "").strip()).strip()
    label = base or "profile"
    return f"{label} - {proxy_port}"[:100]


@dataclass(frozen=True)
class ProxyLine:
    host: str
    port: int
    proxy_type: str = "socks5"
    username: str | None = None
    password: str | None = None

    def to_playwright_dict(self) -> dict[str, str]:
        server = f"{self.proxy_type}://{self.host}:{self.port}"
        out: dict[str, str] = {"server": server}
        if self.username:
            out["username"] = self.username
        if self.password:
            out["password"] = self.password
        return out

    def to_ads_user_proxy_payload(self) -> dict[str, str]:
        t = (self.proxy_type or "socks5").lower()
        user_proxy_type = "socks5" if t == "socks5" else "http"
        return {
            "user_proxy_type": user_proxy_type,
            "proxy_host": self.host,
            "proxy_port": str(self.port),
            "proxy_user": self.username or "",
            "proxy_password": self.password or "",
        }

    def to_ads_user_proxy_config(self) -> dict[str, str]:
        """Формат ``user_proxy_config`` для ``/api/v1/user/create`` (proxy_soft / proxy_type / …)."""
        t = (self.proxy_type or "socks5").lower()
        proxy_type = "socks5" if t == "socks5" else "http"
        return {
            "proxy_soft": "other",
            "proxy_type": proxy_type,
            "proxy_host": self.host,
            "proxy_port": str(self.port),
            "proxy_user": self.username or "",
            "proxy_password": self.password or "",
        }


@dataclass
class ProxyAcquisition:
    """Результат выдачи прокси для одного аккаунта."""

    proxy: ProxyLine
    port_token: str


class ProxyAllocator:
    """Аллокатор для «9 static»: суффикс порта из ``ProxyPortPool``, активация через API."""

    def __init__(
        self,
        settings: AppSettings,
        port_pool: ProxyPortPool,
        timeout_state: ValidationTimeoutState,
        today_list_cache: TodayListCache | None = None,
    ) -> None:
        self._settings = settings
        self._port_pool = port_pool
        self._timeout_state = timeout_state
        self._today_list_cache = today_list_cache
        self._lock = threading.Lock()
        self._shared_suffixes: dict[int, str] = {}
        _proxy_allocator_registry.add(self)

    def _release_suffix_for_thread(self, tid: int, port_suffix: str) -> None:
        self._port_pool.release(port_suffix)
        with self._lock:
            if self._shared_suffixes.get(tid) == port_suffix:
                del self._shared_suffixes[tid]

    def _release_all_tracked_suffixes(self) -> None:
        """Вернуть в пул все суффиксы, ещё числящиеся за потоками (авария / atexit / close)."""
        with self._lock:
            pairs = list(self._shared_suffixes.items())
            self._shared_suffixes.clear()
        seen: set[str] = set()
        for _tid, token in pairs:
            if token in seen:
                continue
            seen.add(token)
            try:
                self._port_pool.release(token)
            except Exception:
                logger.exception("emergency port_pool.release failed (suffix=%s)", token)

    @contextmanager
    def hold_proxy(self, account_name: str) -> Iterator[ProxyAcquisition | None]:
        """Выдать прокси и **всегда** вернуть суффикс в пул при выходе из блока (в т.ч. по исключению)."""
        acq: ProxyAcquisition | None = None
        try:
            acq = self.acquire(account_name)
            yield acq
        finally:
            if acq is not None:
                try:
                    self.release(acq.port_token)
                except Exception:
                    logger.exception("hold_proxy: release failed (suffix=%s)", acq.port_token)

    def acquire(self, account_name: str) -> ProxyAcquisition | None:
        attempts = max(1, self._settings.proxy.max_retries_per_profile)
        host_ip = (self._settings.proxy.host or "").strip()
        port_prefix = int(self._settings.proxy.port_prefix)
        isp = (self._settings.proxy.isp or "").strip()
        use_today = bool(self._settings.proxy.use_today_list)
        cwd = worker_proxy_cwd()
        tid = threading.get_ident()

        for _ in range(attempts):
            port_suffix: str | None = None
            keep_suffix = False
            try:
                wait_unban_if_needed(self._timeout_state, cwd, account_name)

                port_suffix, _cfg, host, port_str = acquire_9static_port_and_config(
                    self._port_pool,
                    self._shared_suffixes,
                    tid,
                    host_ip,
                    port_prefix,
                )

                ok, _err = validate_and_activate_proxy(
                    host,
                    port_str,
                    "9 static",
                    isp,
                    account_name,
                    validation_timeout_state=self._timeout_state,
                    run_mode=ProxyRunMode.STANDARD,
                    cwd=cwd,
                    today_list_cache=self._today_list_cache if use_today else None,
                    use_today_list=use_today,
                )
                if ok:
                    try:
                        port_int = int(port_str)
                    except ValueError:
                        logger.error("bad proxy port from config: %r", port_str)
                        continue
                    logger.info(
                        "proxy acquired for %s: %s:%s (suffix=%s)",
                        account_name,
                        host,
                        port_str,
                        port_suffix,
                    )
                    keep_suffix = True
                    return ProxyAcquisition(
                        proxy=ProxyLine(host=host, port=port_int, proxy_type="socks5"),
                        port_token=port_suffix,
                    )

                logger.warning(
                    "proxy validate/activate failed for %s on %s:%s — retry",
                    account_name,
                    host,
                    port_str,
                )
            except StopAfter9ProxyDialog:
                raise
            finally:
                if port_suffix is not None and not keep_suffix:
                    self._release_suffix_for_thread(tid, port_suffix)

        return None

    def release(self, port_token: str) -> None:
        tid = threading.get_ident()
        self._port_pool.release(port_token)
        with self._lock:
            if self._shared_suffixes.get(tid) == port_token:
                del self._shared_suffixes[tid]
        logger.info("proxy released (suffix=%s)", port_token)

    def close(self) -> None:
        """Освободить все ещё учтённые суффиксы (жёсткий стоп / завершение оркестратора)."""
        self._release_all_tracked_suffixes()


def build_validation_timeout_state(settings: AppSettings) -> ValidationTimeoutState:
    """Shared state: бан после 2× Read timed out (длительность из настроек)."""
    return ValidationTimeoutState(
        release_event=Event(),
        ban_duration_sec=float(settings.proxy.read_timed_out_ban_sec),
    )
