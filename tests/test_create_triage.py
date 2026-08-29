"""create_queued_card must never start, approve, or assign work."""

from __future__ import annotations

import httpx
import pytest

from hermes_agent_bridge.config import Settings
from hermes_agent_bridge.hermes import HermesClient, HermesError


def _settings(**overrides) -> Settings:
    base = {
        "bind_host": "0.0.0.0",
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


@pytest.mark.asyncio
async def test_create_triage_posts_unassigned_triage_only():
    posted = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        posted["url"] = str(request.url)
        posted["json"] = httpx.Request(request.method, request.url, content=request.content).read()
        import json

        posted["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"id": "t1", "triage": True, "status": "triage"})

    transport = httpx.MockTransport(handler)
    client = HermesClient(_settings(), transport=transport)
    result = await client.create_queued_card("do a thing", "details")
    await client.aclose()

    assert posted["url"].endswith("/api/plugins/kanban/tasks")
    body = posted["body"]
    assert body["triage"] is True
    assert body["title"] == "do a thing"
    assert "assignee" not in body
    assert "status" not in body
    assert "ready" not in str(body).lower()
    assert "running" not in str(body).lower()
    assert result["id"] == "t1"


@pytest.mark.asyncio
async def test_refuses_specify_decompose_approval_paths():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = HermesClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(HermesError):
        await client.dashboard_post("/api/plugins/kanban/tasks/t1/specify", json={})
    with pytest.raises(HermesError):
        await client.dashboard_post("/api/plugins/kanban/tasks/t1/decompose", json={})
    with pytest.raises(HermesError):
        await client.dashboard_post("/v1/runs/abc/approval", json={"approve": True})
    await client.aclose()


@pytest.mark.asyncio
async def test_ask_disabled_without_api_url():
    client = HermesClient(_settings(api_url=""))
    with pytest.raises(HermesError, match="disabled"):
        await client.ask("hello")
    await client.aclose()
