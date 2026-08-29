# hermes-agent-bridge

Streamable HTTP MCP so a remote MCP client can call a self-hosted Hermes **as itself**, over tools, not as a chat bot.

Hermes stays on the private network. This process sits on the same docker/LAN network as Hermes, talks to it over HTTP internally, and is reached from the internet only through an existing Cloudflare Tunnel + Access pattern. The Hermes agent API is **not** published.

MCP is the first protocol. ACP may come later. This is not `hermes mcp serve`.

Setup (hostnames, Access team, dashboard URL, tokens) lives in env on the host, never in this repo.

## What the bridge exposes

Tools:

- `health` — dashboard reachability (and the optional ask gateway if configured)
- `list_allowed_boards` — configured board slugs this deployment authorizes. This is local configuration and does not query Hermes.
- `list_board(board)` / `get_task(board, task_id)` — kanban reads on an explicitly selected allowed board
- `create_queued_card(board, title, body)` — `POST /api/plugins/kanban/tasks?board=<board>` with `triage: true` and **no assignee**. Hermes does not dispatch that. There is **no** tool that approves a card, sets `ready`/`running`, calls `specify` / `decompose` / `dispatch`, or hits `/v1/runs/.../approval`.
- `ask` — one-shot `POST /v1/chat/completions` on the internal OpenAI-compatible gateway. If `HERMES_API_URL` is unset or that gateway is down, `ask` is disabled; everything else still works. The ask gateway is **not** published.

Vault: there is no stable in-tree vault plugin route this cut can call. Kanban is the write surface.

`PUBLIC_HOSTNAMES` must include the tunnel hostname so the MCP SDK does not 421.
Each entry allows that exact hostname both without a port and with any port.
`ALLOWED_ORIGINS` is an optional comma-separated list of browser Origins. It
defaults to the HTTPS Origin for each public hostname; set it explicitly when a
different browser Origin must call the MCP endpoint.

`HERMES_KANBAN_BOARDS` is a required comma-separated allowlist. Every kanban
tool call requires an explicit `board`, and the bridge rejects a board not in
that list before contacting Hermes. `list_allowed_boards` exposes only this
configured list; the bridge does not discover or expose other Hermes boards.

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

Requires **Python 3.12+**. The image and local run both use `python3 -m hermes_agent_bridge`.

One image, env file per site. Copy `deploy/site.env.example` to `/etc/hermes-agent-bridge.env` (or set `HERMES_BRIDGE_ENV` to another path). Fill every **required** key. Do not commit the filled file.

Required:

- `SITE`
- `PUBLIC_HOSTNAMES`
- `HERMES_KANBAN_BOARDS`
- `CF_ACCESS_TEAM_DOMAIN`
- `CF_ACCESS_AUD`
- `HERMES_DASHBOARD_URL`

Optional: `ALLOWED_ORIGINS`, `HERMES_DASHBOARD_TOKEN`, `HERMES_API_URL`, `HERMES_API_KEY`, `BIND_PORT`.

For example, `PUBLIC_HOSTNAMES=mcp.example.com` accepts Host values
`mcp.example.com` and `mcp.example.com:<port>`. The default
`ALLOWED_ORIGINS=https://mcp.example.com` can be overridden with a
comma-separated list such as
`https://client.example.com,https://admin.example.com`.

Leaving `HERMES_KANBAN_BOARDS`, `CF_ACCESS_AUD`, or `HERMES_DASHBOARD_URL`
empty fails at process start. Board slugs must contain only lowercase letters,
digits, hyphens, or underscores, start with a letter or digit, and be at most 64
characters. Duplicate entries are removed while preserving order. Compose loads
that file into the container. It does not interpolate those keys from your
shell.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
set -a
. /etc/hermes-agent-bridge.env
set +a
python3 -m hermes_agent_bridge
```

```bash
docker compose -f deploy/compose.yaml up -d --build
```

Join the container to the same docker network Hermes already uses (`HERMES_DOCKER_NETWORK`, default `hermes`). Do **not** add `ports:`.

`HERMES_DASHBOARD_TOKEN` is whatever the dashboard REST already expects (session bearer). Do not commit it.

## Local tests

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
python3 -m pip install -e .
make ci
```

Install from `requirements-dev.txt` (pinned, hashed). `uv.lock` is the source of truth; regenerate with `uv lock && uv export --extra dev --frozen --no-emit-project -o requirements-dev.txt`.

`make ci` is what GitHub Actions runs: `ruff check`, `ruff format --check`, `mypy src`, `pytest`.

Tests cover fail-closed origin checks: JWT missing/empty/garbage/expired/wrong-AUD, no host publish, queue-only tools, ask disabled without hang.

## Fail closed (merge bar)

1. **Unauth origin.** GET/POST without a Cloudflare Access JWT is 401. Empty, expired, or garbage `Cf-Access-Jwt-Assertion` is 401.
2. **JWT at the origin, not only the edge.** Origin checks the JWT against `CF_ACCESS_TEAM_DOMAIN` and **this app’s AUD**. A JWT for a different Access app on the same team is 401. A neighbor on the docker network with no valid JWT cannot drive `/mcp`, the `health` tool, or `create_queued_card`. Loopback `GET /healthz` is container liveness only and does not call Hermes.
3. **No internet hole.** Compose does not publish a host port. No `ports:`, no router/NAT hole. The MCP client path is the named HTTPS URL, not a published `:port`.
4. **Queue only.** No tool approves a card or promotes it to running. `create_queued_card` lands in triage/unassigned only.
5. **Ask is optional.** If the ask gateway URL is unset or down, `ask` returns a clear error and does not hang. `health` and `create_queued_card` still work.
6. **No private-network cheat.** Do not add a localhost, RFC1918, or stdio MCP connector. Happy path is `https://mcp.example.com/mcp` (your hostname) only.

## Done when

An MCP client can add the Access-protected HTTPS MCP URL and call `health`,
`ask`, or `create_queued_card` with an explicitly allowed board against Hermes,
**and** unauthenticated origin calls are rejected.
