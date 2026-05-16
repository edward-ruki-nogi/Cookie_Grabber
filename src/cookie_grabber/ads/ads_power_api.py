from __future__ import annotations

import copy
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin

import httpx

from cookie_grabber.config.settings import AdsPowerConfig, AppSettings

logger = logging.getLogger(__name__)

# Общий throttle для всех экземпляров AdsPowerApi (по одному на воркер).
_ADS_API_LOCK = threading.Lock()
_ADS_API_LAST_REQUEST_AT = 0.0

ADS_REQUEST_THROTTLE_SEC = 0.6
ADS_RATE_LIMIT_MAX_ATTEMPTS = 5
# Паузы перед попытками 2..5 после ответа «Too many request per second».
ADS_RATE_LIMIT_RETRY_DELAYS_SEC = (1.5, 3.0, 6.0, 12.0)


# fingerprint_config для create: random_ua только внутри этого объекта (иначе ADS игнорирует).
# См. https://localapi-doc-en.adspower.com/docs/Awy6Dg — random_ua: ua_browser, ua_system_version.
def _parse_screen_resolution(raw: Any) -> tuple[int, int] | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s or s in {"none", "random"}:
        return None
    if "_" in s:
        parts = s.split("_", 1)
    elif "x" in s:
        parts = s.split("x", 1)
    else:
        return None
    try:
        w, h = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    if w > 0 and h > 0:
        return (w, h)
    return None


def _message_is_rate_limited(msg: str) -> bool:
    m = msg.lower()
    return "too many request" in m or "too many requests" in m


def _json_is_rate_limited(data: dict[str, Any]) -> bool:
    if int(data.get("code", 0)) == 0:
        return False
    return _message_is_rate_limited(str(data.get("msg", "")))


def _response_is_rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    try:
        data = response.json()
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    return _json_is_rate_limited(data)


_CREATE_PROFILE_FINGERPRINT_CONFIG: dict[str, Any] = {
    "automatic_timezone": "1",
    "language": ["en-US", "en"],
    "flash": "block",
    "fonts": ["all"],
    "webrtc": "disabled",
    "random_ua": {
        "ua_browser": ["chrome"],
        "ua_system_version": ["Windows 10", "Windows 11"],
    },
}


@dataclass(frozen=True)
class BrowserStartResult:
    ws_puppeteer: str
    debug_port: str | None


class AdsPowerApi:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.cfg: AdsPowerConfig = settings.ads_power
        base = self.cfg.api_base_url.rstrip("/") + "/"
        self._client = httpx.Client(timeout=120.0)
        self._base = base

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.cfg.api_key:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        return h

    def _throttled_http(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        op_name: str,
        **kwargs: Any,
    ) -> httpx.Response:
        global _ADS_API_LAST_REQUEST_AT
        last_error: Exception | None = None
        last_data: dict[str, Any] | None = None

        for attempt in range(ADS_RATE_LIMIT_MAX_ATTEMPTS):
            if attempt > 0:
                delay_idx = min(attempt - 1, len(ADS_RATE_LIMIT_RETRY_DELAYS_SEC) - 1)
                delay = ADS_RATE_LIMIT_RETRY_DELAYS_SEC[delay_idx]
                logger.warning(
                    "ADS %s: лимит запросов, повтор %s/%s через %.1f с",
                    op_name,
                    attempt + 1,
                    ADS_RATE_LIMIT_MAX_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)

            with _ADS_API_LOCK:
                now = time.monotonic()
                gap = ADS_REQUEST_THROTTLE_SEC - (now - _ADS_API_LAST_REQUEST_AT)
                if gap > 0:
                    time.sleep(gap)
                response = self._client.request(method, url, headers=self._headers(), **kwargs)
                _ADS_API_LAST_REQUEST_AT = time.monotonic()

            if not _response_is_rate_limited(response):
                return response

            try:
                last_data = response.json()
            except Exception:
                last_data = {"status_code": response.status_code, "text": response.text[:500]}
            last_error = RuntimeError(f"ADS {op_name} rate limited: {last_data}")

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"ADS {op_name}: rate limit after {ADS_RATE_LIMIT_MAX_ATTEMPTS} attempts")

    def start_browser(
        self,
        profile_id: str,
        *,
        launch_args: list[str] | None = None,
    ) -> BrowserStartResult:
        ver = (self.cfg.api_version or "v1").lower()
        if ver == "v2":
            url = urljoin(self._base, self.cfg.browser_start_path_v2.lstrip("/"))
            body: dict[str, Any] = {"profile_id": profile_id}
            if launch_args:
                body["launch_args"] = launch_args
            r = self._throttled_http("POST", url, op_name="start_browser", json=body)
        else:
            url = urljoin(self._base, self.cfg.browser_start_path_v1.lstrip("/"))
            params: dict[str, Any] = {"user_id": profile_id}
            if launch_args:
                params["launch_args"] = json.dumps(launch_args, ensure_ascii=False)
            r = self._throttled_http("GET", url, op_name="start_browser", params=params)
        r.raise_for_status()
        data = r.json()
        if int(data.get("code", -1)) != 0:
            raise RuntimeError(f"ADS start failed: {data}")
        ws = (data.get("data") or {}).get("ws") or {}
        puppeteer = ws.get("puppeteer")
        if not puppeteer:
            raise RuntimeError(f"No puppeteer ws in response: {data}")
        dbg = str((data.get("data") or {}).get("debug_port") or "")
        return BrowserStartResult(ws_puppeteer=str(puppeteer), debug_port=dbg or None)

    def stop_browser(self, profile_id: str) -> None:
        ver = (self.cfg.api_version or "v1").lower()
        try:
            if ver == "v2":
                url = urljoin(self._base, self.cfg.browser_stop_path_v2.lstrip("/"))
                r = self._throttled_http(
                    "POST",
                    url,
                    op_name="stop_browser",
                    json={"profile_id": profile_id},
                )
            else:
                url = urljoin(self._base, self.cfg.browser_stop_path_v1.lstrip("/"))
                r = self._throttled_http(
                    "GET",
                    url,
                    op_name="stop_browser",
                    params={"user_id": profile_id},
                )
        except RuntimeError as exc:
            if "rate limit" in str(exc).lower():
                logger.warning("ADS stop_browser rate limit: %s", exc)
            else:
                raise
            return
        if r.status_code >= 400:
            logger.warning("ADS stop HTTP %s: %s", r.status_code, r.text[:500])
            return
        try:
            data = r.json()
            if int(data.get("code", 0)) != 0:
                logger.warning("ADS stop reported error: %s", data)
        except Exception:
            logger.warning("ADS stop parse error: %s", r.text[:500])

    def update_profile_proxy(
        self,
        profile_id: str,
        proxy_payload: dict[str, Any],
        *,
        name: str | None = None,
    ) -> None:
        url = urljoin(self._base, self.cfg.update_user_proxy_path.lstrip("/"))
        body: dict[str, Any] = {"user_id": profile_id, **proxy_payload}
        if name:
            body["name"] = name
        r = self._throttled_http("POST", url, op_name="update_profile_proxy", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"update proxy HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        if int(data.get("code", -1)) != 0:
            raise RuntimeError(f"update proxy failed: {data}")

    def create_profile(
        self,
        group_id: str,
        name: str | None = None,
        *,
        user_proxy_config: dict[str, Any] | None = None,
    ) -> str:
        """Создать профиль ADS Power в указанной группе.

        Возвращает ``profile_id`` (он же ``user_id``).
        Поддерживает ``api_version`` v1 и v2.

        У ADS при создании часто требуется ``user_proxy_config`` или ``proxy_id``;
        для «9 static» передавайте результат ``ProxyLine.to_ads_user_proxy_config``.

        В тело запроса всегда добавляется ``fingerprint_config`` с вложенным
        ``random_ua``: только Chrome и только Windows 10 / Windows 11 (см. Local API).
        """
        if not group_id:
            raise ValueError("create_profile: group_id is empty")
        ver = (self.cfg.api_version or "v1").lower()
        if ver == "v2":
            path = self.cfg.create_path_v2
            body: dict[str, Any] = {"group_id": str(group_id)}
            if name:
                body["name"] = name
            if user_proxy_config is not None:
                body["user_proxy_config"] = user_proxy_config
        else:
            path = self.cfg.create_path_v1
            body = {"group_id": str(group_id)}
            if name:
                body["name"] = name
            if user_proxy_config is not None:
                body["user_proxy_config"] = user_proxy_config
        body["fingerprint_config"] = copy.deepcopy(_CREATE_PROFILE_FINGERPRINT_CONFIG)
        url = urljoin(self._base, path.lstrip("/"))
        r = self._throttled_http("POST", url, op_name="create_profile", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"ADS create_profile HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        if int(data.get("code", -1)) != 0:
            raise RuntimeError(f"ADS create_profile failed: {data}")
        block = data.get("data") or {}
        pid = (
            block.get("user_id")
            or block.get("profile_id")
            or block.get("id")
            or ""
        )
        if not pid:
            raise RuntimeError(f"ADS create_profile no user_id in response: {data}")
        return str(pid)

    def delete_profile(self, profile_id: str) -> None:
        """Удалить профиль ADS (v1: user_ids, v2: profile_id — массив id)."""
        pid = (profile_id or "").strip()
        if not pid:
            return
        ver = (self.cfg.api_version or "v1").lower()
        if ver == "v2":
            url = urljoin(self._base, "api/v2/browser-profile/delete")
            body: dict[str, Any] = {"profile_id": [pid]}
        else:
            url = urljoin(self._base, "api/v1/user/delete")
            body = {"user_ids": [pid]}
        r = self._throttled_http("POST", url, op_name="delete_profile", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"ADS delete_profile HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        if int(data.get("code", -1)) != 0:
            raise RuntimeError(f"ADS delete_profile failed: {data}")

    def get_profile_screen_resolution(self, profile_id: str) -> tuple[int, int]:
        """Разрешение отпечатка профиля (width, height) для правила «на весь экран»."""
        url = urljoin(self._base, "api/v1/user/list")
        r = self._throttled_http(
            "GET",
            url,
            op_name="get_profile_screen_resolution",
            params={"user_id": profile_id, "page": 1, "page_size": 1},
        )
        r.raise_for_status()
        data = r.json()
        if int(data.get("code", -1)) != 0:
            return (1920, 1080)
        block = data.get("data") or {}
        rows = block.get("list") or []
        if not rows:
            return (1920, 1080)
        row = rows[0]
        parsed = _parse_screen_resolution(
            row.get("screen_resolution")
            or row.get("resolution")
            or (row.get("fingerprint_config") or {}).get("screen_resolution")
        )
        if parsed is not None:
            return parsed
        return (1920, 1080)

    def list_groups(
        self,
        *,
        group_name: str | None = None,
        page_size: int = 2000,
    ) -> list[dict[str, str]]:
        """Список групп Local API: GET /api/v1/group/list → group_id, group_name."""
        url = urljoin(self._base, "api/v1/group/list")
        out: list[dict[str, str]] = []
        page = 1
        cap = min(max(page_size, 1), 2000)
        while True:
            params: dict[str, Any] = {"page": page, "page_size": cap}
            if group_name:
                params["group_name"] = group_name
            r = self._throttled_http("GET", url, op_name="list_groups", params=params)
            r.raise_for_status()
            data = r.json()
            if int(data.get("code", -1)) != 0:
                raise RuntimeError(f"ADS group list failed: {data}")
            block = data.get("data") or {}
            chunk = block.get("list") or []
            for row in chunk:
                gid = str(row.get("group_id", ""))
                gname = str(row.get("group_name", ""))
                if gid:
                    out.append({"group_id": gid, "group_name": gname})
            if len(chunk) < cap:
                break
            page += 1
        return out

    def close(self) -> None:
        self._client.close()
