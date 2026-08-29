from __future__ import annotations

import os
import re
from dataclasses import dataclass

_BOARD_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"{name} is required")
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
        hostnames = _csv("PUBLIC_HOSTNAMES")
        if not hostnames:
            raise RuntimeError("PUBLIC_HOSTNAMES is required")
        allowed_origins = _csv("ALLOWED_ORIGINS") or [
            f"https://{hostname}" for hostname in hostnames
        ]
        return cls(
            bind_host=os.environ.get("BIND_HOST", "0.0.0.0"),
            bind_port=int(os.environ.get("BIND_PORT", "8080")),
            public_hostnames=hostnames,
            allowed_origins=allowed_origins,
            cf_team_domain=_require("CF_ACCESS_TEAM_DOMAIN"),
            cf_aud=_require("CF_ACCESS_AUD"),
            dashboard_url=_require("HERMES_DASHBOARD_URL").rstrip("/"),
            dashboard_token=os.environ.get("HERMES_DASHBOARD_TOKEN", "").strip(),
            api_url=os.environ.get("HERMES_API_URL", "").rstrip("/"),
            api_key=os.environ.get("HERMES_API_KEY", "").strip(),
            allowed_kanban_boards=_allowed_kanban_boards(),
            site=_require("SITE"),
        )
