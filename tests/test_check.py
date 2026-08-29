"""Preflight command behavior and stable exit codes."""

from __future__ import annotations

import io

import httpx
import pytest

from hermes_agent_bridge.check import ExitCode, run_checks
from hermes_agent_bridge.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "bind_host": "0.0.0.0",
        "bind_port": 8080,
        "public_hostnames": ["mcp.example.com"],
        "allowed_origins": ["https://mcp.example.com"],
        "cf_team_domain": "example.cloudflareaccess.com",
        "cf_aud": "aud",
        "dashboard_url": "http://hermes.internal:9119",
        "dashboard_token": "secret",
        "api_url": "",
        "api_key": "",
        "allowed_kanban_boards": ["project-a"],
        "site": "site-a",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_preflight_success_and_disabled_ask():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cdn-cgi/access/certs"):
            return httpx.Response(200, json={"keys": [{"kid": "one"}]})
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"ok": True})

    output = io.StringIO()
    code = await run_checks(
        _settings(), transport=httpx.MockTransport(handler), output=output
    )

    assert code == ExitCode.OK
    assert "SKIP optional Ask" in output.getvalue()
    assert "secret" not in output.getvalue()


@pytest.mark.asyncio
async def test_bad_jwks_has_distinct_exit_code():
    output = io.StringIO()
    code = await run_checks(
        _settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        output=output,
    )

    assert code == ExitCode.JWKS
    assert "FAIL Cloudflare JWKS" in output.getvalue()


@pytest.mark.asyncio
async def test_dashboard_failure_is_fatal():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/certs"):
            return httpx.Response(200, json={"keys": [{}]})
        return httpx.Response(503)

    code = await run_checks(
        _settings(), transport=httpx.MockTransport(handler), output=io.StringIO()
    )

    assert code == ExitCode.DASHBOARD


@pytest.mark.asyncio
async def test_ask_failure_is_warning_with_success_exit():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/certs"):
            return httpx.Response(200, json={"keys": [{}]})
        if request.url.host == "ask.internal":
            return httpx.Response(503)
        return httpx.Response(200)

    output = io.StringIO()
    code = await run_checks(
        _settings(api_url="http://ask.internal:8642"),
        transport=httpx.MockTransport(handler),
        output=output,
    )

    assert code == ExitCode.OK
    assert "WARN optional Ask" in output.getvalue()
