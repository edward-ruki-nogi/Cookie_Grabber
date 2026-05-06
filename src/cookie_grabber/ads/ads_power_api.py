from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from cookie_grabber.config.settings import AdsPowerConfig, AppSettings

logger = logging.getLogger(__name__)


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

    def start_browser(self, profile_id: str) -> BrowserStartResult:
        ver = (self.cfg.api_version or "v1").lower()
        if ver == "v2":
            url = urljoin(self._base, self.cfg.browser_start_path_v2.lstrip("/"))
            body: dict[str, Any] = {"profile_id": profile_id}
            r = self._client.post(url, json=body, headers=self._headers())
        else:
            url = urljoin(self._base, self.cfg.browser_start_path_v1.lstrip("/"))
            r = self._client.get(
                url,
                params={"user_id": profile_id},
                headers=self._headers(),
            )
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
        if ver == "v2":
            url = urljoin(self._base, self.cfg.browser_stop_path_v2.lstrip("/"))
            r = self._client.post(
                url,
                json={"profile_id": profile_id},
                headers=self._headers(),
            )
        else:
            url = urljoin(self._base, self.cfg.browser_stop_path_v1.lstrip("/"))
            r = self._client.get(
                url,
                params={"user_id": profile_id},
                headers=self._headers(),
            )
        if r.status_code >= 400:
            logger.warning("ADS stop HTTP %s: %s", r.status_code, r.text[:500])
            return
        try:
            data = r.json()
            if int(data.get("code", 0)) != 0:
                logger.warning("ADS stop reported error: %s", data)
        except Exception:
            logger.warning("ADS stop parse error: %s", r.text[:500])

    def update_profile_proxy(self, profile_id: str, proxy_payload: dict[str, Any]) -> None:
        url = urljoin(self._base, self.cfg.update_user_proxy_path.lstrip("/"))
        body = {"user_id": profile_id, **proxy_payload}
        r = self._client.post(url, json=body, headers=self._headers())
        if r.status_code >= 400:
            raise RuntimeError(f"update proxy HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        if int(data.get("code", -1)) != 0:
            raise RuntimeError(f"update proxy failed: {data}")

    def create_profile(self, group_id: str, name: str | None = None) -> str:
        """Создать профиль ADS Power в указанной группе.

        Возвращает ``profile_id`` (он же ``user_id``).
        Поддерживает ``api_version`` v1 и v2.
        """
        if not group_id:
            raise ValueError("create_profile: group_id is empty")
        ver = (self.cfg.api_version or "v1").lower()
        if ver == "v2":
            path = self.cfg.create_path_v2
            body: dict[str, Any] = {"group_id": str(group_id)}
            if name:
                body["name"] = name
        else:
            path = self.cfg.create_path_v1
            body = {"group_id": str(group_id)}
            if name:
                body["name"] = name
        url = urljoin(self._base, path.lstrip("/"))
        r = self._client.post(url, json=body, headers=self._headers())
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
            r = self._client.get(url, params=params, headers=self._headers())
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
