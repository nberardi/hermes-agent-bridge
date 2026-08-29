# hermes-agent-bridge

Streamable HTTP MCP so a remote MCP client can call a self-hosted Hermes **as itself**, over tools, not as a chat bot.

Hermes stays on the private network. This process sits on the same docker/LAN network as Hermes, talks to it over HTTP internally, and is reached from the internet only through an existing Cloudflare Tunnel + Access pattern. The Hermes agent API is **not** published.

MCP is the first protocol. ACP may come later. This is not `hermes mcp serve`.

Setup (hostnames, Access team, dashboard URL, tokens) lives in env on the host, never in this repo.

## What the bridge exposes

Tools (all go to this site's Hermes over the private network):

- `health` — dashboard reachability (and the optional ask gateway if configured)
- `list_board` / `get_task` — kanban reads
- `create_queued_card` — `POST /api/plugins/kanban/tasks` with `triage: true` and **no assignee**. Hermes does not dispatch that. There is **no** tool that approves a card, sets `ready`/`running`, calls `specify` / `decompose` / `dispatch`, or hits `/v1/runs/.../approval`.
- `ask` — one-shot `POST /v1/chat/completions` on the internal OpenAI-compatible gateway. If `HERMES_API_URL` is unset or that gateway is down, `ask` is disabled; everything else still works. The ask gateway is **not** published.

Vault: there is no stable in-tree vault plugin route this cut can call. Kanban is the write surface.

`PUBLIC_HOSTNAMES` must include the tunnel hostname so the MCP SDK does not 421.

## What this is not

- Not a Slack or Telegram bot
- Not a wrapper around `hermes mcp serve`
- Not an internet listener on the Hermes dashboard or ask gateway
- No host port published, no router hole. `cloudflared` reaches the container on the docker network.

## Cloudflare (existing pattern; no new product)

Same idea as putting Access in front of any private origin:

1. Public hostname on an **existing** tunnel, routed to this service (`http://hermes-agent-bridge:8080` or whatever the docker DNS name is).
2. Access **application** on that hostname. Set `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` from that app; they are not in git.
3. **Two policies, not one:**
   - **Service Auth** — service token for the MCP client
   - **Allow** — operator login
   Do not mix Service Auth and Allow in a single policy.
4. Origin JWT: this process validates `Cf-Access-Jwt-Assertion` against `https://$CF_ACCESS_TEAM_DOMAIN/cdn-cgi/access/certs` with the per-app **AUD**. A neighbor on the docker network without that JWT is 401.

## MCP client connector

Add a custom remote MCP:

- URL: `https://mcp.example.com/mcp` (replace with your Access hostname)
- Headers:
  - `CF-Access-Client-Id`
  - `CF-Access-Client-Secret`

Those are the Cloudflare Access **service token** id/secret. They never go in this repo.

The client must not use localhost, RFC1918, or stdio to reach Hermes. Happy path is the named HTTPS URL only.

## Deploy

One image, env file per site. Copy `deploy/site.env.example` to a host path outside git (e.g. `/etc/hermes-agent-bridge.env`) and fill Access AUD, team domain, public hostname, and the dashboard URL. Do not commit the filled file.

```bash
export $(grep -v '^#' /etc/hermes-agent-bridge.env | xargs)
docker compose -f deploy/compose.yaml up -d --build
```

Join the container to the same docker network Hermes already uses (`HERMES_DOCKER_NETWORK`, default `hermes`). Do **not** add `ports:`.

`HERMES_DASHBOARD_TOKEN` is whatever the dashboard REST already expects (session bearer). Do not commit it.

## Local tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Tests cover fail-closed origin checks: JWT missing/empty/garbage/expired/wrong-AUD, no host publish, queue-only tools, ask disabled without hang.

## Fail closed (merge bar)

1. **Unauth origin.** GET/POST without a Cloudflare Access JWT is 401. Empty, expired, or garbage `Cf-Access-Jwt-Assertion` is 401.
2. **JWT at the origin, not only the edge.** Origin checks the JWT against `CF_ACCESS_TEAM_DOMAIN` and **this app’s AUD**. A JWT for a different Access app on the same team is 401. A neighbor on the docker network with no valid JWT cannot drive `/mcp`, the `health` tool, or `create_queued_card`. Loopback `GET /healthz` is container liveness only and does not call Hermes.
3. **No internet hole.** Compose does not publish a host port. No `ports:`, no router/NAT hole. The MCP client path is the named HTTPS URL, not a published `:port`.
4. **Queue only.** No tool approves a card or promotes it to running. `create_queued_card` lands in triage/unassigned only.
5. **Ask is optional.** If the ask gateway URL is unset or down, `ask` returns a clear error and does not hang. `health` and `create_queued_card` still work.
6. **No private-network cheat.** Do not add a localhost, RFC1918, or stdio MCP connector. Happy path is `https://mcp.example.com/mcp` (your hostname) only.

## Done when

An MCP client can add the Access-protected HTTPS MCP URL and call `health` or `ask` or `create_queued_card` against Hermes, **and** unauthenticated origin calls are rejected.
