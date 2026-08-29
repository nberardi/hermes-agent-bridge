from __future__ import annotations

import os
import re
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

_BOARD_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$",
    re.IGNORECASE,
)


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"{name} is required")
    if "\n" in val or "\r" in val:
        raise RuntimeError(f"{name} may not contain a line break")
    return val


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [p.strip() for p in raw.split(",") if p.strip()]


def _allowed_kanban_boards() -> list[str]:
    boards = list(dict.fromkeys(_csv("HERMES_KANBAN_BOARDS")))
    if not boards:
        raise RuntimeError("HERMES_KANBAN_BOARDS is required")
    invalid = [board for board in boards if not _BOARD_SLUG.fullmatch(board)]
    if invalid:
        joined = ", ".join(invalid)
        raise RuntimeError(f"invalid HERMES_KANBAN_BOARDS slug(s): {joined}")
    return boards


def _hostname(value: str, name: str) -> str:
    value = value.strip().lower().rstrip(".")
    if not value or not _HOSTNAME.fullmatch(value):
        raise RuntimeError(f"{name} must be a DNS hostname: {value!r}")
    return value


def _hostnames() -> list[str]:
    values = list(dict.fromkeys(_csv("PUBLIC_HOSTNAMES")))
    if not values:
        raise RuntimeError("PUBLIC_HOSTNAMES is required")
    return [_hostname(value, "PUBLIC_HOSTNAMES") for value in values]


def _http_url(value: str, name: str, *, origin_only: bool = False) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a valid HTTP(S) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"{name} must be a valid HTTP(S) URL")
    if port is not None and not 1 <= port <= 65535:
        raise RuntimeError(f"{name} has an invalid port")
    if origin_only and parsed.path not in {"", "/"}:
        raise RuntimeError(f"{name} entries must be origins without a path")
    return value


def _port() -> int:
    raw = os.environ.get("BIND_PORT", "8080").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("BIND_PORT must be an integer from 1 to 65535") from exc
    if not 1 <= value <= 65535:
        raise RuntimeError("BIND_PORT must be an integer from 1 to 65535")
    return value


def _bind_host() -> str:
    value = os.environ.get("BIND_HOST", "0.0.0.0").strip()
    try:
        ip_address(value)
    except ValueError:
        return _hostname(value, "BIND_HOST")
    return value


def load_env_file(path: str | Path, *, override: bool = False) -> None:
    """Load the installer's simple KEY=VALUE file without executing shell code."""
    env_path = Path(path)
    for number, raw_line in enumerate(env_path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"{env_path}:{number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise RuntimeError(f"{env_path}:{number}: invalid environment key")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    bind_host: str
    bind_port: int
    public_hostnames: list[str]
    allowed_origins: list[str]
    cf_team_domain: str
    cf_aud: str
    dashboard_url: str
    dashboard_token: str
    api_url: str
    api_key: str
    allowed_kanban_boards: list[str]
    site: str

    @property
    def ask_enabled(self) -> bool:
        return bool(self.api_url)

    @property
    def jwks_url(self) -> str:
        return f"https://{self.cf_team_domain}/cdn-cgi/access/certs"

    @property
    def issuer(self) -> str:
        return f"https://{self.cf_team_domain}"

    @classmethod
    def from_env(cls) -> Settings:
        hostnames = _hostnames()
        allowed_origins_raw = _csv("ALLOWED_ORIGINS") or [
            f"https://{hostname}" for hostname in hostnames
        ]
        allowed_origins = list(
            dict.fromkeys(
                _http_url(value, "ALLOWED_ORIGINS", origin_only=True)
                for value in allowed_origins_raw
            )
        )
        dashboard_url = _http_url(
            _require("HERMES_DASHBOARD_URL"), "HERMES_DASHBOARD_URL"
        )
        api_url_raw = os.environ.get("HERMES_API_URL", "").strip()
        return cls(
            bind_host=_bind_host(),
            bind_port=_port(),
            public_hostnames=hostnames,
            allowed_origins=allowed_origins,
            cf_team_domain=_hostname(
                _require("CF_ACCESS_TEAM_DOMAIN"), "CF_ACCESS_TEAM_DOMAIN"
            ),
            cf_aud=_require("CF_ACCESS_AUD"),
            dashboard_url=dashboard_url,
            dashboard_token=os.environ.get("HERMES_DASHBOARD_TOKEN", "").strip(),
            api_url=(_http_url(api_url_raw, "HERMES_API_URL") if api_url_raw else ""),
            api_key=os.environ.get("HERMES_API_KEY", "").strip(),
            allowed_kanban_boards=_allowed_kanban_boards(),
            site=_require("SITE"),
        )
