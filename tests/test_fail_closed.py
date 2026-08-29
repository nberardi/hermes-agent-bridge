"""QA fail-closed 3–6: no host publish, queue-only tools, ask optional, no RFC1918 cheat."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from hermes_agent_bridge.hermes import HermesClient, HermesError
from hermes_agent_bridge.server import mcp

ROOT = Path(__file__).resolve().parents[1]


def _settings(**overrides):
    from hermes_agent_bridge.config import Settings

    base = {
        "bind_host": "127.0.0.1",
        "bind_port": 8080,
        "public_hostnames": ["mcp.example.com"],
        "cf_team_domain": "example.cloudflareaccess.com",
        "cf_aud": "test-aud",
        "dashboard_url": "http://hermes.internal:9119",
        "dashboard_token": "dash-token",
        "api_url": "",
        "api_key": "",
        "kanban_board": "",
        "site": "site1",
    }
    base.update(overrides)
    return Settings(**base)


def test_compose_does_not_publish_a_host_port():
    text = (ROOT / "deploy" / "compose.yaml").read_text()
    # keys only; comments may mention what not to do
    keys = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    body = "\n".join(keys)
    assert re.search(r"(?m)^\s*ports\s*:", body) is None
    assert "0.0.0.0" not in body
    assert "host_ip" not in body
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert not re.search(r"(?m)^\s*EXPOSE\b", dockerfile, re.IGNORECASE)


def test_examples_do_not_embed_credentials():
    env_ex = (ROOT / ".env.example").read_text()
    for line in env_ex.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, _, val = line.partition("=")
        if key.endswith(("AUD", "TOKEN", "KEY")):
            assert val.strip() == "", line


@pytest.mark.asyncio
async def test_tools_are_queue_only():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "create_queued_card" in names
    assert "health" in names
    assert "ask" in names
    forbidden = ("approv", "promote", "dispatch", "specify", "decompose", "running", "ready")
    joined = " ".join(sorted(names)).lower()
    for needle in forbidden:
        assert needle not in joined, names


@pytest.mark.asyncio
async def test_ask_down_8642_is_disabled_not_hang():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 8642 or str(request.url).endswith("/health"):
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"ok": True})

    client = HermesClient(
        _settings(api_url="http://hermes.internal:8642"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HermesError, match="disabled"):
        await client.ask("hello")
    await client.aclose()


@pytest.mark.asyncio
async def test_health_and_create_work_when_ask_disabled():
    posted = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tasks") and request.method == "POST":
            posted["body"] = request.content
            return httpx.Response(200, json={"id": "t1", "triage": True})
        return httpx.Response(200, json={"status": "ok"})

    client = HermesClient(_settings(api_url=""), transport=httpx.MockTransport(handler))
    health = await client.health()
    assert health["ask"] == "disabled"
    created = await client.create_queued_card("queued")
    assert created["id"] == "t1"
    await client.aclose()


def test_happy_path_is_named_https_not_rfc1918():
    readme = (ROOT / "README.md").read_text()
    assert "https://mcp.example.com/mcp" in readme
    assert "RFC1918" in readme or "rfc1918" in readme.lower()
    assert "stdio" in readme.lower()
