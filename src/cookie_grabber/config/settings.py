from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Встроенные дефолты (раньше — ``config/default_settings.yaml``).
# Пользовательские переопределения: ``config/settings.yaml`` (deep-merge поверх этого).
_DEFAULT_SETTINGS_YAML = """
threads: 2
accounts_per_run: 0
action_delay_sec_min: 1.0
action_delay_sec_max: 5.0
navigation_timeout_ms: 60000
network_idle_timeout_ms: 25000

ads_power:
  api_base_url: "http://127.0.0.1:50325"
  api_version: "v1"
  api_key: ""
  browser_start_path_v1: "/api/v1/browser/start"
  browser_stop_path_v1: "/api/v1/browser/stop"
  browser_start_path_v2: "/api/v2/browser-profile/start"
  browser_stop_path_v2: "/api/v2/browser-profile/stop"
  update_user_proxy_path: "/api/v1/user/update"
  create_path_v1: "/api/v1/user/create"
  create_path_v2: "/api/v2/browser-profile/create"
  signup_group_id: "9450654"

google_sheets:
  spreadsheet_id: ""
  worksheet_name: "Sheet1"
  service_account_json_path: "secrets/service_account.json"
  header_row: 1
  first_data_row: 3
  counter_cell: "A2"
  account_name_column: "A"
  last_date_column: "B"
  last_seconds_column: "C"
  sessions_count_column: "D"
  total_seconds_column: "E"
  status_column: "F"
  ads_profile_id_column: "G"
  profile_id_column: "G"
  notes_column: "H"
  last_update_column: "B"
  important_site_columns:
    PP: "M"
    WH: "N"
    Kwiff: "O"
  mass_total_column: "K"
  window_layout_column: "X"

timezone: "Europe/London"
mass_sites_file: "data/mass_sites.txt"

farming:
  important_sites_minutes_per_site: 5
  mass_sites_minutes_per_site: 5
  mass_sites_count: 0
  cookie_banner_timeout_sec: 20.0
  between_nav_delay_sec_min: 2.0
  between_nav_delay_sec_max: 8.0
  revisit_url_probability: 0.07
  engagement_budget_sec_max: 45.0
  show_synthetic_mouse: false

important_sites:
  - id: "PP"
    url: "https://www.paddypower.com/bet"
    referer: "https://www.google.com/"
    enabled: true
  - id: "WH"
    url: "https://www.williamhill.com/"
    referer: "https://www.google.com/"
    enabled: true
  - id: "Kwiff"
    url: "https://kwiff.com/sports/football/"
    referer: "https://www.google.com/"
    enabled: true

status_values:
  created: "Создан"
  empty: "Ожидает"
  warming: "Прогрев"
  deleted: "Удален"
  fault: "Ошибка"

proxy:
  source: "9 static"
  use_today_list: false
  host: "85.31.96.136"
  port_prefix: 600
  isp: "AS5089 Virgin Media Limited"
  read_timed_out_ban_sec: 1800
  max_retries_per_profile: 5

paths:
  settings_file: "config/settings.yaml"
""".strip()

# Путь к модулю с встроенными дефолтами (для меню «путь к настройкам»).
SETTINGS_SOURCE_PATH = Path(__file__).resolve()


@dataclass
class AdsPowerConfig:
    api_base_url: str = "http://127.0.0.1:50325"
    api_version: str = "v1"
    api_key: str = ""
    browser_start_path_v1: str = "/api/v1/browser/start"
    browser_stop_path_v1: str = "/api/v1/browser/stop"
    browser_start_path_v2: str = "/api/v2/browser-profile/start"
    browser_stop_path_v2: str = "/api/v2/browser-profile/stop"
    update_user_proxy_path: str = "/api/v1/user/update"
    create_path_v1: str = "/api/v1/user/create"
    create_path_v2: str = "/api/v2/browser-profile/create"
    signup_group_id: str = ""


@dataclass
class GoogleSheetsConfig:
    spreadsheet_id: str = ""
    worksheet_name: str = "Sheet1"
    service_account_json_path: str = "secrets/service_account.json"
    header_row: int = 1
    first_data_row: int = 3
    counter_cell: str = "A2"
    account_name_column: str = "A"
    last_date_column: str = "B"
    last_seconds_column: str = "C"
    sessions_count_column: str = "D"
    total_seconds_column: str = "E"
    status_column: str = "F"
    ads_profile_id_column: str = "G"
    profile_id_column: str = "A"
    notes_column: str = "C"
    last_update_column: str = "B"
    important_site_columns: dict[str, str] = field(default_factory=dict)
    mass_total_column: str = "K"
    window_layout_column: str = "X"


@dataclass
class FarmingConfig:
    """Бюджет нагула: минуты на каждый основной/второстепенный URL; mass_sites_count 0 = все строки файла."""

    important_sites_minutes_per_site: int = 5
    mass_sites_minutes_per_site: int = 5
    mass_sites_count: int = 0
    cookie_banner_timeout_sec: float = 20.0
    between_nav_delay_sec_min: float = 2.0
    between_nav_delay_sec_max: float = 8.0
    revisit_url_probability: float = 0.07
    engagement_budget_sec_max: float = 45.0
    # Визуальный индикатор позиции синтетической мыши в окне браузера (оверлей в странице).
    show_synthetic_mouse: bool = False


@dataclass
class ImportantSite:
    id: str
    url: str
    enabled: bool = True
    referer: str = ""


@dataclass
class StatusValues:
    created: str = "Создан"
    empty: str = "Ожидает"
    warming: str = "Прогрев"
    deleted: str = "Удален"
    fault: str = "Ошибка"


PROXY_SOURCE_9STATIC = "9 static"
PROXY_ISP_VIRGIN = "AS5089 Virgin Media Limited"
PROXY_ISP_ANY = "Любой"
PROXY_ISP_CHOICES: frozenset[str] = frozenset({PROXY_ISP_VIRGIN, PROXY_ISP_ANY})


@dataclass
class ProxyConfig:
    """Сценарий «9 static» (gala-9static-proxy): хост панели, порт {port_prefix}{suffix} (5 цифр)."""

    source: str = PROXY_SOURCE_9STATIC
    use_today_list: bool = False
    host: str = "85.31.96.136"
    port_prefix: int = 600
    isp: str = PROXY_ISP_VIRGIN
    read_timed_out_ban_sec: float = 1800.0
    max_retries_per_profile: int = 5


@dataclass
class PathsConfig:
    settings_file: str = "config/settings.yaml"


@dataclass
class AppSettings:
    threads: int = 2
    # Макс. число профилей, с которыми начата работа за один запуск (0 = без лимита).
    accounts_per_run: int = 0
    action_delay_sec_min: float = 1.0
    action_delay_sec_max: float = 5.0
    navigation_timeout_ms: int = 60000
    network_idle_timeout_ms: int = 25000
    ads_power: AdsPowerConfig = field(default_factory=AdsPowerConfig)
    google_sheets: GoogleSheetsConfig = field(default_factory=GoogleSheetsConfig)
    timezone: str = "Europe/London"
    mass_sites_file: str = "data/mass_sites.txt"
    farming: FarmingConfig = field(default_factory=FarmingConfig)
    important_sites: list[ImportantSite] = field(default_factory=list)
    status_values: StatusValues = field(default_factory=StatusValues)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    project_root: Path = field(default_factory=lambda: Path.cwd())

    def resolve(self, p: str | Path) -> Path:
        path = Path(p)
        if path.is_absolute():
            return path
        return (self.project_root / path).resolve()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _dict_to_ads(d: dict[str, Any]) -> AdsPowerConfig:
    return AdsPowerConfig(
        api_base_url=d.get("api_base_url", AdsPowerConfig.api_base_url),
        api_version=str(d.get("api_version", AdsPowerConfig.api_version)),
        api_key=str(d.get("api_key", "")),
        browser_start_path_v1=d.get("browser_start_path_v1", AdsPowerConfig.browser_start_path_v1),
        browser_stop_path_v1=d.get("browser_stop_path_v1", AdsPowerConfig.browser_stop_path_v1),
        browser_start_path_v2=d.get("browser_start_path_v2", AdsPowerConfig.browser_start_path_v2),
        browser_stop_path_v2=d.get("browser_stop_path_v2", AdsPowerConfig.browser_stop_path_v2),
        update_user_proxy_path=d.get("update_user_proxy_path", AdsPowerConfig.update_user_proxy_path),
        create_path_v1=str(d.get("create_path_v1", AdsPowerConfig.create_path_v1)),
        create_path_v2=str(d.get("create_path_v2", AdsPowerConfig.create_path_v2)),
        signup_group_id=str(d.get("signup_group_id", "")),
    )


def _dict_to_sheets(d: dict[str, Any]) -> GoogleSheetsConfig:
    return GoogleSheetsConfig(
        spreadsheet_id=str(d.get("spreadsheet_id", "")),
        worksheet_name=str(d.get("worksheet_name", "Sheet1")),
        service_account_json_path=str(
            d.get("service_account_json_path", GoogleSheetsConfig.service_account_json_path)
        ),
        header_row=int(d.get("header_row", 1)),
        first_data_row=int(d.get("first_data_row", 3)),
        counter_cell=str(d.get("counter_cell", "A2")),
        account_name_column=str(d.get("account_name_column", "A")),
        last_date_column=str(d.get("last_date_column", "B")),
        last_seconds_column=str(d.get("last_seconds_column", "C")),
        sessions_count_column=str(d.get("sessions_count_column", "D")),
        total_seconds_column=str(d.get("total_seconds_column", "E")),
        status_column=str(d.get("status_column", "F")),
        ads_profile_id_column=str(d.get("ads_profile_id_column", "G")),
        profile_id_column=str(d.get("profile_id_column", "A")),
        notes_column=str(d.get("notes_column", "C")),
        last_update_column=str(d.get("last_update_column", "B")),
        important_site_columns=dict(d.get("important_site_columns", {})),
        mass_total_column=str(d.get("mass_total_column", "K")),
        window_layout_column=str(d.get("window_layout_column", "X")),
    )


def _parse_important_sites(raw: list[dict[str, Any]] | None) -> list[ImportantSite]:
    if not raw:
        return []
    out: list[ImportantSite] = []
    for item in raw:
        out.append(
            ImportantSite(
                id=str(item["id"]),
                url=str(item["url"]),
                enabled=bool(item.get("enabled", True)),
                referer=str(item.get("referer", "")),
            )
        )
    return out


def validate_app_settings(settings: AppSettings) -> None:
    """Публичная валидация перед сохранением из UI или тестов."""
    _validate(settings)


def _validate(settings: AppSettings) -> None:
    if settings.threads < 1:
        raise ValueError("threads must be >= 1")
    if settings.accounts_per_run < 0:
        raise ValueError("accounts_per_run must be >= 0")
    if settings.accounts_per_run > 1_000_000:
        raise ValueError("accounts_per_run must be <= 1000000")
    if settings.action_delay_sec_min > settings.action_delay_sec_max:
        raise ValueError("action_delay_sec_min must be <= action_delay_sec_max")
    f = settings.farming
    if f.important_sites_minutes_per_site < 0 or f.important_sites_minutes_per_site > 24 * 60:
        raise ValueError("farming.important_sites_minutes_per_site must be between 0 and 1440")
    if f.mass_sites_minutes_per_site < 0 or f.mass_sites_minutes_per_site > 24 * 60:
        raise ValueError("farming.mass_sites_minutes_per_site must be between 0 and 1440")
    if f.mass_sites_count < 0 or f.mass_sites_count > 100_000:
        raise ValueError("farming.mass_sites_count must be between 0 and 100000")
    if f.cookie_banner_timeout_sec < 0 or f.cookie_banner_timeout_sec > 300:
        raise ValueError("farming.cookie_banner_timeout_sec must be between 0 and 300")
    if f.between_nav_delay_sec_min < 0 or f.between_nav_delay_sec_max < 0:
        raise ValueError("farming between_nav delays must be non-negative")
    if f.between_nav_delay_sec_min > f.between_nav_delay_sec_max:
        raise ValueError("farming.between_nav_delay_sec_min must be <= between_nav_delay_sec_max")
    if f.revisit_url_probability < 0 or f.revisit_url_probability > 0.2:
        raise ValueError("farming.revisit_url_probability must be between 0 and 0.2")
    if f.engagement_budget_sec_max < 1 or f.engagement_budget_sec_max > 600:
        raise ValueError("farming.engagement_budget_sec_max must be between 1 and 600")
    for site in settings.important_sites:
        if site.id not in settings.google_sheets.important_site_columns:
            raise ValueError(
                f"important site id '{site.id}' missing in google_sheets.important_site_columns"
            )

    proxy = settings.proxy
    if (proxy.source or "").strip() != PROXY_SOURCE_9STATIC:
        raise ValueError(f"proxy.source must be '{PROXY_SOURCE_9STATIC}'")
    if not (proxy.host or "").strip():
        raise ValueError("proxy.host must be non-empty")
    if not (100 <= int(proxy.port_prefix) <= 999):
        raise ValueError("proxy.port_prefix must be between 100 and 999 (first 3 digits of port)")
    if proxy.isp not in PROXY_ISP_CHOICES:
        raise ValueError(f"proxy.isp must be one of {sorted(PROXY_ISP_CHOICES)}")
    if proxy.read_timed_out_ban_sec < 0:
        raise ValueError("proxy.read_timed_out_ban_sec must be >= 0")


def load_settings(
    project_root: Path | None = None,
    user_settings_path: Path | None = None,
) -> AppSettings:
    root = (project_root or Path.cwd()).resolve()
    user_path = user_settings_path or (root / "config" / "settings.yaml")
    data: dict[str, Any] = yaml.safe_load(_DEFAULT_SETTINGS_YAML) or {}
    if user_path.is_file():
        with user_path.open(encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
        data = _deep_merge(data, override)

    ads = _dict_to_ads(data.get("ads_power") or {})
    gs = _dict_to_sheets(data.get("google_sheets") or {})
    farm_raw = data.get("farming") or {}
    farming_cfg = FarmingConfig(
        important_sites_minutes_per_site=int(farm_raw.get("important_sites_minutes_per_site", 5)),
        mass_sites_minutes_per_site=int(farm_raw.get("mass_sites_minutes_per_site", 5)),
        mass_sites_count=int(farm_raw.get("mass_sites_count", 0)),
        cookie_banner_timeout_sec=float(farm_raw.get("cookie_banner_timeout_sec", 20.0)),
        between_nav_delay_sec_min=float(farm_raw.get("between_nav_delay_sec_min", 2.0)),
        between_nav_delay_sec_max=float(farm_raw.get("between_nav_delay_sec_max", 8.0)),
        revisit_url_probability=float(farm_raw.get("revisit_url_probability", 0.07)),
        engagement_budget_sec_max=float(farm_raw.get("engagement_budget_sec_max", 45.0)),
        show_synthetic_mouse=bool(farm_raw.get("show_synthetic_mouse", False)),
    )
    sv = data.get("status_values") or {}
    status_values = StatusValues(
        created=str(sv.get("created", StatusValues.created)),
        empty=str(sv.get("empty", StatusValues.empty)),
        warming=str(sv.get("warming", StatusValues.warming)),
        deleted=str(sv.get("deleted", StatusValues.deleted)),
        fault=str(sv.get("fault", sv.get("error", StatusValues.fault))),
    )
    pc = data.get("proxy") or {}
    if "port_prefix" in pc:
        port_prefix_val = int(pc["port_prefix"])
    elif "port_order" in pc:
        port_prefix_val = 600 + int(pc.get("port_order", 0))
    else:
        port_prefix_val = ProxyConfig.port_prefix
    proxy_cfg = ProxyConfig(
        source=str(pc.get("source", PROXY_SOURCE_9STATIC)).strip() or PROXY_SOURCE_9STATIC,
        use_today_list=bool(pc.get("use_today_list", False)),
        host=str(pc.get("host", ProxyConfig.host)),
        port_prefix=port_prefix_val,
        isp=str(pc.get("isp", ProxyConfig.isp)),
        read_timed_out_ban_sec=float(pc.get("read_timed_out_ban_sec", ProxyConfig.read_timed_out_ban_sec)),
        max_retries_per_profile=int(pc.get("max_retries_per_profile", ProxyConfig.max_retries_per_profile)),
    )
    paths_cfg = PathsConfig(
        settings_file=str(data.get("paths", {}).get("settings_file", PathsConfig.settings_file))
    )

    settings = AppSettings(
        threads=int(data.get("threads", 2)),
        accounts_per_run=int(data.get("accounts_per_run", 0)),
        action_delay_sec_min=float(data.get("action_delay_sec_min", 1.0)),
        action_delay_sec_max=float(data.get("action_delay_sec_max", 5.0)),
        navigation_timeout_ms=int(data.get("navigation_timeout_ms", 60000)),
        network_idle_timeout_ms=int(data.get("network_idle_timeout_ms", 25000)),
        ads_power=ads,
        google_sheets=gs,
        timezone=str(data.get("timezone", "Europe/London")),
        mass_sites_file=str(data.get("mass_sites_file", "data/mass_sites.txt")),
        farming=farming_cfg,
        important_sites=_parse_important_sites(data.get("important_sites")),
        status_values=status_values,
        proxy=proxy_cfg,
        paths=paths_cfg,
        project_root=root,
    )
    _validate(settings)
    return settings


def app_settings_to_dict(s: AppSettings) -> dict[str, Any]:
    """Сериализация без ``project_root`` — для сохранения в ``settings.yaml``."""
    ads = s.ads_power
    gs = s.google_sheets
    farm = s.farming
    sv = s.status_values
    pc = s.proxy

    sites: list[dict[str, Any]] = [
        {"id": x.id, "url": x.url, "enabled": x.enabled, "referer": x.referer}
        for x in s.important_sites
    ]

    return {
        "threads": s.threads,
        "accounts_per_run": s.accounts_per_run,
        "action_delay_sec_min": s.action_delay_sec_min,
        "action_delay_sec_max": s.action_delay_sec_max,
        "navigation_timeout_ms": s.navigation_timeout_ms,
        "network_idle_timeout_ms": s.network_idle_timeout_ms,
        "ads_power": {
            "api_base_url": ads.api_base_url,
            "api_version": ads.api_version,
            "api_key": ads.api_key,
            "browser_start_path_v1": ads.browser_start_path_v1,
            "browser_stop_path_v1": ads.browser_stop_path_v1,
            "browser_start_path_v2": ads.browser_start_path_v2,
            "browser_stop_path_v2": ads.browser_stop_path_v2,
            "update_user_proxy_path": ads.update_user_proxy_path,
            "create_path_v1": ads.create_path_v1,
            "create_path_v2": ads.create_path_v2,
            "signup_group_id": ads.signup_group_id,
        },
        "google_sheets": {
            "spreadsheet_id": gs.spreadsheet_id,
            "worksheet_name": gs.worksheet_name,
            "service_account_json_path": gs.service_account_json_path,
            "header_row": gs.header_row,
            "first_data_row": gs.first_data_row,
            "counter_cell": gs.counter_cell,
            "account_name_column": gs.account_name_column,
            "last_date_column": gs.last_date_column,
            "last_seconds_column": gs.last_seconds_column,
            "sessions_count_column": gs.sessions_count_column,
            "total_seconds_column": gs.total_seconds_column,
            "status_column": gs.status_column,
            "ads_profile_id_column": gs.ads_profile_id_column,
            "profile_id_column": gs.profile_id_column,
            "notes_column": gs.notes_column,
            "last_update_column": gs.last_update_column,
            "important_site_columns": dict(gs.important_site_columns),
            "mass_total_column": gs.mass_total_column,
            "window_layout_column": gs.window_layout_column,
        },
        "timezone": s.timezone,
        "mass_sites_file": s.mass_sites_file,
        "farming": {
            "important_sites_minutes_per_site": farm.important_sites_minutes_per_site,
            "mass_sites_minutes_per_site": farm.mass_sites_minutes_per_site,
            "mass_sites_count": farm.mass_sites_count,
            "cookie_banner_timeout_sec": farm.cookie_banner_timeout_sec,
            "between_nav_delay_sec_min": farm.between_nav_delay_sec_min,
            "between_nav_delay_sec_max": farm.between_nav_delay_sec_max,
            "revisit_url_probability": farm.revisit_url_probability,
            "engagement_budget_sec_max": farm.engagement_budget_sec_max,
            "show_synthetic_mouse": farm.show_synthetic_mouse,
        },
        "important_sites": sites,
        "status_values": {
            "created": sv.created,
            "empty": sv.empty,
            "warming": sv.warming,
            "deleted": sv.deleted,
            "fault": sv.fault,
        },
        "proxy": {
            "source": pc.source,
            "use_today_list": pc.use_today_list,
            "host": pc.host,
            "port_prefix": pc.port_prefix,
            "isp": pc.isp,
            "read_timed_out_ban_sec": pc.read_timed_out_ban_sec,
            "max_retries_per_profile": pc.max_retries_per_profile,
        },
        "paths": {"settings_file": s.paths.settings_file},
    }


def save_user_settings(settings: AppSettings) -> Path:
    path = settings.resolve(settings.paths.settings_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = app_settings_to_dict(settings)
    return _atomic_write_yaml(path, blob)


def save_settings_patch(project_root: Path, patch: dict[str, Any]) -> Path:
    """Атомарно накатывает ``patch`` поверх существующего ``config/settings.yaml``.

    Базовый слой дефолтов зашит в ``settings.py`` (``_DEFAULT_SETTINGS_YAML``); здесь только пользовательские переопределения.
    """
    root = project_root.resolve()
    user_path = root / "config" / "settings.yaml"
    user_path.parent.mkdir(parents=True, exist_ok=True)
    base: dict[str, Any] = {}
    if user_path.is_file():
        with user_path.open(encoding="utf-8") as f:
            base = yaml.safe_load(f) or {}
    merged = _deep_merge(base, patch)
    return _atomic_write_yaml(user_path, merged)


def _atomic_write_yaml(path: Path, data: dict[str, Any]) -> Path:
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                data,
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                width=120,
            )
        os.replace(tmp_name, path)
        return path.resolve()
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
