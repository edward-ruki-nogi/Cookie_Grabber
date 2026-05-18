"""Проверка и установка обновлений с GitHub Releases."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from cookie_grabber import __version__
from cookie_grabber.runtime_paths import application_root

logger = logging.getLogger(__name__)

GITHUB_OWNER = "edward-ruki-nogi"
GITHUB_REPO = "Cookie_Grabber"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
ASSET_NAME_TEMPLATE = "CookieGrabber-{version}-win64.zip"
STAGING_DIR_NAME = ".update_staging"
EXTRACTED_DIR_NAME = "payload"


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    release_url: str
    asset_name: str
    asset_download_url: str
    asset_size: int | None

    @property
    def has_update(self) -> bool:
        return version_gt(self.latest_version, self.current_version)


def _parse_version_tuple(raw: str) -> tuple[int, ...]:
    s = (raw or "").strip().lstrip("vV")
    parts: list[int] = []
    for piece in s.split("."):
        m = re.match(r"(\d+)", piece)
        if m:
            parts.append(int(m.group(1)))
    return tuple(parts or (0,))


def version_gt(latest: str, current: str) -> bool:
    return _parse_version_tuple(latest) > _parse_version_tuple(current)


def _normalize_tag(tag: str) -> str:
    return (tag or "").strip().lstrip("vV")


def _pick_asset(release: dict[str, Any], version: str) -> dict[str, Any] | None:
    expected = ASSET_NAME_TEMPLATE.format(version=version)
    assets = release.get("assets") or []
    for asset in assets:
        if asset.get("name") == expected:
            return asset
    for asset in assets:
        name = (asset.get("name") or "").lower()
        if name.endswith(".zip") and "cookiegrabber" in name and "win" in name:
            return asset
    return None


def check_for_update(timeout: float = 25.0) -> UpdateCheckResult:
    current = __version__
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "cookie-grabber-updater"}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(RELEASES_LATEST_URL, headers=headers)
        resp.raise_for_status()
        release = resp.json()

    tag = _normalize_tag(str(release.get("tag_name") or release.get("name") or ""))
    if not tag:
        raise ValueError("В ответе GitHub нет версии релиза.")

    asset = _pick_asset(release, tag)
    if asset is None:
        raise ValueError(
            f"В релизе v{tag} нет подходящего .zip (ожидалось «{ASSET_NAME_TEMPLATE.format(version=tag)}»)."
        )

    download_url = str(asset.get("browser_download_url") or "")
    if not download_url:
        raise ValueError("У ассета релиза нет browser_download_url.")

    return UpdateCheckResult(
        current_version=current,
        latest_version=tag,
        release_url=str(release.get("html_url") or f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"),
        asset_name=str(asset.get("name") or ""),
        asset_download_url=download_url,
        asset_size=int(asset["size"]) if asset.get("size") is not None else None,
    )


def _staging_root() -> Path:
    root = application_root() / STAGING_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def download_and_stage_update(info: UpdateCheckResult, timeout: float = 600.0) -> Path:
    staging = _staging_root()
    zip_path = staging / info.asset_name
    extracted = staging / EXTRACTED_DIR_NAME

    if extracted.exists():
        shutil.rmtree(extracted, ignore_errors=True)
    extracted.mkdir(parents=True, exist_ok=True)

    headers = {"Accept": "application/octet-stream", "User-Agent": "cookie-grabber-updater"}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", info.asset_download_url, headers=headers) as resp:
            resp.raise_for_status()
            with zip_path.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)

    if info.asset_size is not None and zip_path.stat().st_size != info.asset_size:
        raise ValueError(
            f"Размер загрузки не совпал: {zip_path.stat().st_size} байт, ожидалось {info.asset_size}."
        )

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extracted)

    # PyInstaller onedir zip: либо корень с CookieGrabber.exe, либо вложенная папка CookieGrabber/
    candidates = [extracted]
    nested = extracted / "CookieGrabber"
    if nested.is_dir():
        candidates.insert(0, nested)
    for cand in candidates:
        if (cand / "CookieGrabber.exe").is_file():
            return cand
    if any(cand.glob("*.exe") for cand in candidates):
        return candidates[0]
    raise ValueError("В архиве обновления не найден CookieGrabber.exe.")


def _apply_update_script() -> Path:
    root = application_root()
    bundled = root / "apply_update.bat"
    if bundled.is_file():
        return bundled
    repo_script = Path(__file__).resolve().parents[3] / "scripts" / "apply_update.bat"
    if repo_script.is_file():
        return repo_script
    raise FileNotFoundError("Не найден apply_update.bat рядом с exe или в scripts/.")


def launch_apply_update(staged_payload_dir: Path) -> None:
    if sys.platform != "win32":
        raise OSError("Автоустановка обновления поддерживается только на Windows.")

    target = application_root()
    exe_path = target / "CookieGrabber.exe"
    if not exe_path.is_file() and getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
    if not exe_path.is_file():
        raise FileNotFoundError(f"Не найден CookieGrabber.exe: {exe_path}")

    script = _apply_update_script()
    # cmd /c — иначе .bat с DETACHED_PROCESS часто не стартует; ждём все CookieGrabber.exe в bat.
    args = [
        "cmd.exe",
        "/c",
        str(script),
        str(target),
        str(staged_payload_dir.resolve()),
        str(exe_path.resolve()),
    ]
    log_dir = target / STAGING_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "apply_update_launch.log"
    try:
        log_f = log_path.open("a", encoding="utf-8")
    except OSError:
        log_f = subprocess.DEVNULL  # type: ignore[assignment]
    subprocess.Popen(
        args,
        cwd=str(target),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS,  # type: ignore[attr-defined]
        close_fds=False,
    )
    if hasattr(log_f, "close") and log_f not in (subprocess.DEVNULL, sys.stdout, sys.stderr):
        log_f.close()
