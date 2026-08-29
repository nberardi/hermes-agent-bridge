"""MCP Host and Origin validation for the public transport."""

from __future__ import annotations

import pytest
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request

from hermes_agent_bridge.config import Settings
from hermes_agent_bridge.server import _transport_security_settings


def _settings(**overrides) -> Settings:
    base = {
        "bind_host": "0.0.0.0",
        "bind_port": 8080,
        "public_hostnames": ["mcp.example.com"],
        "allowed_origins": ["https://client.example.com"],
        "cf_team_domain": "example.cloudflareaccess.com",
        "cf_aud": "test-aud",
        "dashboard_url": "http://hermes.internal:9119",
        "dashboard_token": "",
        "api_url": "",
        "api_key": "",
        "allowed_kanban_boards": ["project-a"],
        "site": "site1",
    }
    base.update(overrides)
    return Settings(**base)


async def _validate(
    *, host: str, origin: str | None = None, settings: Settings | None = None
):
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/mcp",
            "headers": headers,
        }
    )
    validator = TransportSecurityMiddleware(
        _transport_security_settings(settings or _settings())
    )
    return await validator.validate_request(request)


@pytest.mark.asyncio
async def test_allowed_hostname_without_port():
    assert await _validate(host="mcp.example.com") is None


@pytest.mark.asyncio
async def test_allowed_hostname_with_port():
    settings = _settings(public_hostnames=["mcp.example.com", "other.example.com"])
    assert await _validate(host="other.example.com:8443", settings=settings) is None


@pytest.mark.asyncio
async def test_rejected_unknown_hostname():
    response = await _validate(host="unknown.example.com")
    assert response is not None
    assert response.status_code == 421


@pytest.mark.asyncio
async def test_allowed_origin():
    assert (
        await _validate(host="mcp.example.com", origin="https://client.example.com")
        is None
    )


@pytest.mark.asyncio
async def test_rejected_origin():
    response = await _validate(
        host="mcp.example.com", origin="https://unknown.example.com"
    )
    assert response is not None
    assert response.status_code == 403


def test_allowed_origins_from_env(monkeypatch):
    env = {
        "SITE": "site-a",
        "PUBLIC_HOSTNAMES": "mcp.example.com",
        "ALLOWED_ORIGINS": "https://client.example.com,https://admin.example.com",
        "CF_ACCESS_TEAM_DOMAIN": "example.cloudflareaccess.com",
        "CF_ACCESS_AUD": "test-aud",
        "HERMES_DASHBOARD_URL": "https://dashboard.example.com",
        "HERMES_KANBAN_BOARDS": "project-a",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    settings = Settings.from_env()

    assert settings.allowed_origins == [
        "https://client.example.com",
        "https://admin.example.com",
    ]


def test_allowed_origins_default_to_public_https_hosts(monkeypatch):
    env = {
        "SITE": "site-a",
        "PUBLIC_HOSTNAMES": "mcp.example.com,other.example.com",
        "CF_ACCESS_TEAM_DOMAIN": "example.cloudflareaccess.com",
        "CF_ACCESS_AUD": "test-aud",
        "HERMES_DASHBOARD_URL": "https://dashboard.example.com",
        "HERMES_KANBAN_BOARDS": "project-a",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)

    settings = Settings.from_env()

    assert settings.allowed_origins == [
        "https://mcp.example.com",
        "https://other.example.com",
    ]
