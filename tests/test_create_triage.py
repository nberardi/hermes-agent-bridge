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
        "allowed_origins": ["https://mcp.example.com"],
        "cf_team_domain": "example.cloudflareaccess.com",
        "cf_aud": "test-aud",
        "dashboard_url": "http://hermes.internal:9119",
        "dashboard_token": "dash-token",
        "api_url": "",
        "api_key": "",
        "allowed_kanban_boards": ["project-a", "project-b"],
        "site": "site1",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_create_triage_on_allowed_board_is_unassigned_triage_only():
    posted = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        posted["url"] = str(request.url)
        posted["json"] = httpx.Request(
            request.method, request.url, content=request.content
        ).read()
        import json

        posted["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200, json={"id": "t1", "triage": True, "status": "triage"}
        )

    transport = httpx.MockTransport(handler)
    client = HermesClient(_settings(), transport=transport)
    result = await client.create_queued_card("project-a", "do a thing", "details")
    await client.aclose()

    url = httpx.URL(posted["url"])
    assert url.path == "/api/plugins/kanban/tasks"
    assert dict(url.params) == {"board": "project-a"}
    body = posted["body"]
    assert body["triage"] is True
    assert body["title"] == "do a thing"
    assert "assignee" not in body
    assert "status" not in body
    assert "ready" not in str(body).lower()
    assert "running" not in str(body).lower()
    assert result["id"] == "t1"


@pytest.mark.asyncio
async def test_rejects_disallowed_board_before_request():
    requested = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(500)

    client = HermesClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HermesError, match="not allowed"):
        await client.create_queued_card("unknown", "do a thing")
    await client.aclose()

    assert requested is False


@pytest.mark.asyncio
async def test_board_reads_use_explicit_allowed_board():
    requested = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url)
        return httpx.Response(200, json={"ok": True})

    client = HermesClient(_settings(), transport=httpx.MockTransport(handler))
    await client.list_board("project-a")
    await client.get_task("project-b", "t1")
    await client.aclose()

    assert requested[0].path == "/api/plugins/kanban/board"
    assert dict(requested[0].params) == {"board": "project-a"}
    assert requested[1].path == "/api/plugins/kanban/tasks/t1"
    assert dict(requested[1].params) == {"board": "project-b"}


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
