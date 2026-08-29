"""Installation preflight for configuration and upstream reachability."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from enum import IntEnum
from pathlib import Path
from typing import TextIO

import httpx

from .config import Settings, load_env_file


class ExitCode(IntEnum):
    OK = 0
    CONFIG = 2
    JWKS = 3
    DASHBOARD = 4


async def run_checks(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    output: TextIO = sys.stdout,
) -> ExitCode:
    """Run remote checks in dependency order and print secret-free results."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=3.0), transport=transport
    ) as client:
        try:
            response = await client.get(settings.jwks_url)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("keys"):
                raise ValueError("JWKS has no keys")
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            print(f"FAIL Cloudflare JWKS: {exc}", file=output)
            return ExitCode.JWKS
        print("OK   Cloudflare JWKS", file=output)

        headers = {}
        if settings.dashboard_token:
            headers["Authorization"] = f"Bearer {settings.dashboard_token}"
        dashboard_status: int | None = None
        dashboard_error: Exception | None = None
        for path in ("/api/health", "/health"):
            try:
                response = await client.get(
                    f"{settings.dashboard_url}{path}", headers=headers
                )
                dashboard_status = response.status_code
                if response.is_success:
                    dashboard_error = None
                    break
                if response.status_code != 404:
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                dashboard_error = exc
                break
        if (
            dashboard_error is not None
            or dashboard_status is None
            or not response.is_success
        ):
            detail = dashboard_error or f"HTTP {dashboard_status}"
            print(f"FAIL Hermes dashboard: {detail}", file=output)
            return ExitCode.DASHBOARD
        print("OK   Hermes dashboard", file=output)

        if settings.ask_enabled:
            headers = {}
            if settings.api_key:
                headers["Authorization"] = f"Bearer {settings.api_key}"
            try:
                response = await client.get(
                    f"{settings.api_url}/health", headers=headers
                )
                response.raise_for_status()
                print("OK   optional Ask gateway", file=output)
            except httpx.HTTPError as exc:
                print(f"WARN optional Ask gateway unavailable: {exc}", file=output)
        else:
            print("SKIP optional Ask gateway (HERMES_API_URL unset)", file=output)
    return ExitCode.OK


def _default_env_file() -> Path | None:
    configured = os.environ.get("HERMES_BRIDGE_ENV", "").strip()
    if configured:
        return Path(configured)
    default = Path("/etc/hermes-agent-bridge.env")
    return default if default.is_file() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hermes_agent_bridge check")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    try:
        env_file = args.env_file or _default_env_file()
        if env_file is not None:
            load_env_file(env_file, override=True)
        settings = Settings.from_env()
    except (OSError, RuntimeError) as exc:
        print(f"FAIL configuration: {exc}", file=sys.stderr)
        return ExitCode.CONFIG
    print("OK   configuration")
    return asyncio.run(run_checks(settings))
