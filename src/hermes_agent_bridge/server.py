"""Streamable HTTP MCP server. Origin JWT required. Hermes stays on the LAN."""

from __future__ import annotations

import logging

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .access import AccessJWTMiddleware
from .config import Settings
from .hermes import HermesClient, HermesError

log = logging.getLogger(__name__)

settings: Settings | None = None
client: HermesClient | None = None

mcp = MCPServer(
    name="hermes-agent-bridge",
    instructions=(
        "Call this site's Hermes as an MCP client. "
        "Queue work with create_queued_card only; nothing here starts or approves a run. "
        "Reach this server only at the configured PUBLIC_HOSTNAMES HTTPS URL, "
        "never localhost, RFC1918, or stdio."
    ),
)


def _settings() -> Settings:
    if settings is None:
        raise RuntimeError("settings not loaded")
    return settings


def _client() -> HermesClient:
    global client
    if client is None:
        client = HermesClient(_settings())
    return client


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> PlainTextResponse:
    """Container liveness. Does not call Hermes. JWT required except from loopback."""
    return PlainTextResponse("ok")


@mcp.tool()
async def health() -> dict:
    """Reachability of this site's Hermes dashboard and optional ask gateway. Does not start work."""
    return await _client().health()


@mcp.tool()
async def list_board() -> dict:
    """Read the Hermes kanban board (cards, columns, triage). Read-only."""
    data = await _client().list_board()
    return data if isinstance(data, dict) else {"board": data}


@mcp.tool()
async def get_task(task_id: str) -> dict:
    """Read one kanban card by id. Read-only."""
    data = await _client().get_task(task_id)
    return data if isinstance(data, dict) else {"task": data}


@mcp.tool()
async def create_queued_card(title: str, body: str = "") -> dict:
    """Queue a card in triage, unassigned. Does not start, approve, or dispatch it.

    Hermes will not auto-run this. There is no tool on this bridge that promotes
    a card to ready/running or that hits specify/decompose/approval.
    """
    data = await _client().create_queued_card(title, body)
    return data if isinstance(data, dict) else {"created": data}


@mcp.tool()
async def ask(prompt: str, model: str = "") -> dict:
    """One-shot question via the internal OpenAI-compatible gateway (:8642).

    Disabled (clear error, no hang) when HERMES_API_URL is unset or :8642 is down.
    Does not expose run approval.
    """
    s = _settings()
    if not s.ask_enabled:
        return {"error": "ask is disabled on this site (HERMES_API_URL unset)"}
    try:
        text = await _client().ask(prompt, model or None)
        return {"reply": text}
    except HermesError as exc:
        return {"error": str(exc)}


def build_app(s: Settings):
    """ASGI app with Access JWT at origin and PUBLIC_HOSTNAMES so MCP does not 421."""
    global settings, client
    settings = s
    client = None
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*s.public_hostnames, "localhost", "127.0.0.1"],
        allowed_origins=[f"https://{h}" for h in s.public_hostnames],
    )
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=security,
        host=s.bind_host,
    )
    # Last added runs first: JWT before MCP. No path skip; neighbors need a JWT.
    app.add_middleware(
        AccessJWTMiddleware,
        jwks_url=s.jwks_url,
        audience=s.cf_aud,
        issuer=s.issuer,
    )
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    s = Settings.from_env()
    app = build_app(s)
    import uvicorn

    log.info(
        "hermes-agent-bridge site=%s dashboard=%s ask=%s hosts=%s",
        s.site,
        s.dashboard_url,
        s.ask_enabled,
        ",".join(s.public_hostnames),
    )
    # Do not enable proxy_headers: that would let X-Forwarded-For spoof loopback.
    uvicorn.run(app, host=s.bind_host, port=s.bind_port, proxy_headers=False)
