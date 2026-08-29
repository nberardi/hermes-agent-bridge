# hermes-agent-bridge

Streamable HTTP MCP so a remote MCP client can call a self-hosted Hermes **as itself**, over tools, not as a chat bot.

## Choose an installation

Use one installer and one configuration contract in every mode:

| Mode | Choose it when | Tunnel origin after install |
| --- | --- | --- |
| `docker` | Hermes and cloudflared already share a Docker network | `http://hermes-agent-bridge:<port>` |
| `native` | A Debian/Ubuntu systemd host should run the bridge directly | The checked local/LAN `http://<address>:<port>` |
| `proxmox` | Proxmox VE 8/9 should own a small unprivileged Debian LXC | The checked LXC `http://<address>:<port>` |

Download, verify, and run a release installer (replace `v0.1.0` after later releases):

```bash
version=v0.1.0
curl --fail --location --remote-name \
  "https://github.com/nberardi/hermes-agent-bridge/releases/download/${version}/install.sh"
curl --fail --location --remote-name \
  "https://github.com/nberardi/hermes-agent-bridge/releases/download/${version}/SHA256SUMS"
grep '  install.sh$' SHA256SUMS > install.sh.sha256
sha256sum --check install.sh.sha256
chmod +x install.sh
sudo ./install.sh docker --version "$version"
# Or: sudo ./install.sh native --version "$version"
# Or, on a Proxmox VE host: sudo ./install.sh proxmox --version "$version"
```

The secondary curl-pipe-shell convenience form is:

```bash
curl --fail --location \
  https://github.com/nberardi/hermes-agent-bridge/releases/download/v0.1.0/install.sh \
  | sudo bash -s -- native --version v0.1.0
```

The guided wizard hides secrets and covers `SITE`, public hostnames,
`ALLOWED_ORIGINS`, allowed kanban boards, Cloudflare team/AUD, dashboard URL and
token, optional Ask URL and key, bind address, and bind port. It writes the
configuration to `/etc/hermes-agent-bridge.env` as root with mode `0600`. Pass
`--env-file /absolute/path` to use another location. For automation, supply a
complete env file and add `--non-interactive`.

Rerunning the same command is an explicit in-place upgrade. Existing settings
are the wizard defaults, release directories are installed atomically under
`/opt/hermes-agent-bridge`, and the installer rolls the service and
configuration back when the new version fails its health check. There is no
automatic update timer.

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

## Installation behavior

All modes require `SITE`, `PUBLIC_HOSTNAMES`, `HERMES_KANBAN_BOARDS`,
`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, and `HERMES_DASHBOARD_URL`. Optional
settings are `ALLOWED_ORIGINS`, `HERMES_DASHBOARD_TOKEN`, `HERMES_API_URL`,
`HERMES_API_KEY`, `BIND_HOST`, and `BIND_PORT`. `HERMES_DASHBOARD_TOKEN` is the
bearer token already expected by the dashboard. Never commit a filled env file.
The installer limits `BIND_HOST` to wildcard or loopback addresses so its
loopback-only, unauthenticated liveness check cannot become a network bypass;
Docker requires a wildcard address to remain reachable on its private network.

`PUBLIC_HOSTNAMES` contains DNS hostnames without schemes or paths. Each accepts
that exact Host value with or without a port. `ALLOWED_ORIGINS` defaults to the
HTTPS origin of each public hostname. Board slugs contain lowercase letters,
digits, hyphens, or underscores, begin with a letter or digit, and are at most
64 characters. Duplicate boards are removed in order.

Before activation, every installer runs the same preflight used for
troubleshooting:

```bash
/opt/hermes-agent-bridge/current/venv/bin/python \
  -m hermes_agent_bridge check --env-file /etc/hermes-agent-bridge.env
```

It validates configuration, fetches the Cloudflare Access JWKS, and checks the
Hermes dashboard. A dashboard failure is fatal. If Ask is configured but its
gateway is unavailable, the command prints a warning and succeeds so dashboard
and kanban tools remain usable. Exit codes are `0` for success (including an Ask
warning), `2` for configuration, `3` for JWKS, and `4` for dashboard failure.

### Docker Compose

The installer validates an existing external Docker network shared with Hermes.
Compose pulls `ghcr.io/nberardi/hermes-agent-bridge:vX.Y.Z`; it never builds a
floating local image and never publishes a host port. The multi-architecture
image runs as a numeric non-root user with a read-only filesystem, dropped
capabilities, and a loopback `/healthz` check. If Docker is absent on Debian or
Ubuntu, the interactive installer offers Docker Engine and Compose from
Docker's official apt repository. Other hosts receive exact prerequisites.

### Native Debian or Ubuntu

Native mode supports systemd-based Debian and Ubuntu on amd64 and arm64. It
installs pinned Python 3.12.11 and locked, hash-verified production dependencies
inside the versioned release directory, without changing system Python. The
bridge runs as a dedicated non-login user from a root-owned, non-writable
application tree under a hardened systemd unit. The underlying runtime command
remains `python3 -m hermes_agent_bridge` (using the release's private Python).

### Proxmox VE

Run Proxmox mode as root on VE 8 or 9. It discovers template storage, root
storage, and Linux bridges, prompting only when there is more than one. A new
install defaults to the next free CT ID, Debian 13 (Debian 12 fallback), 1 CPU,
1 GiB RAM, 512 MiB swap, a 4 GiB root disk, DHCP on `vmbr0` when available, and
start-on-boot. Static IPv4 is optional. The container is unprivileged and does
not enable Docker or nesting.

Configuration crosses the host only in a `0600` temporary file and is pushed
with `pct push`; host-side secrets are removed after `pct exec` completes the
native install. `/etc/hermes-agent-bridge-proxmox.conf` records only the CT ID,
so rerunning Proxmox mode upgrades that LXC instead of creating another one.

Every successful mode ends with the exact Tunnel origin, Access
application/AUD and two-policy checklist, service-token header names, MCP URL,
and mode-specific troubleshooting commands.

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
