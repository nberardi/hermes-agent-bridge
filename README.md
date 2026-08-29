# hermes-agent-bridge

Streamable HTTP MCP so a remote MCP client can call a self-hosted Hermes **as
itself**, over tools, not as a chat bot.

Hermes stays on the private network. This bridge runs beside Hermes, talks to
its dashboard internally, and is reached from the internet only through an
existing Cloudflare Tunnel and Access application. It does not publish the
Hermes API.

## Install

Choose **one** of the three paths below. Do not run all three.

- Choose **Docker** when Hermes and cloudflared already run on the same Docker
  network.
- Choose **Native Linux** when the bridge should run directly on a Debian or
  Ubuntu machine or VM.
- Choose **Proxmox** when the installer should create and manage a dedicated
  Debian LXC for the bridge.

Proxmox eventually uses the native service inside its LXC, but you only run the
Proxmox command. You do not run the native command yourself afterward.

### Before you start

Have these values ready:

- The public MCP hostname, such as `mcp.example.com`
- The private Hermes dashboard URL reachable from the installation target
- The Hermes kanban board slugs this bridge may access
- The Cloudflare Access team domain and application AUD
- Optional dashboard and Ask-gateway credentials

Cloudflare Tunnel and Access must already exist. The installer does not create
or change them, your firewall, or router/NAT rules.

### Download and verify the installer for Docker or native Linux

Run this on the Docker host or native Linux host. If you chose Proxmox, skip
this download and continue to Option C, which streams the installer directly
from GitHub.

```bash
version=v0.1.0
curl --fail --location --remote-name \
  "https://github.com/nberardi/hermes-agent-bridge/releases/download/${version}/install.sh"
curl --fail --location --remote-name \
  "https://github.com/nberardi/hermes-agent-bridge/releases/download/${version}/SHA256SUMS"
grep '  install.sh$' SHA256SUMS > install.sh.sha256
sha256sum --check install.sh.sha256
chmod +x install.sh
```

Now follow Option A or Option B. Proxmox users start at Option C instead.

### Option A: Docker

Use this when Hermes and cloudflared already share a Docker network. The
network must exist before installation. Its name is often `hermes`.

Run on the Docker host:

```bash
sudo ./install.sh docker --version v0.1.0
```

The installer:

1. Prompts for the bridge configuration and existing Hermes Docker network.
2. Offers to install Docker Engine and Compose from Docker's official apt
   repository when they are missing on Debian or Ubuntu.
3. Pulls the pinned, non-root GHCR image for the selected version.
4. Starts it on the external Hermes network without publishing a host port.
5. Runs the shared preflight and waits for the container health check.

On success, use the origin printed by the installer for the Cloudflare Tunnel.
It will look like:

```text
http://hermes-agent-bridge:8080
```

This origin works only when cloudflared is attached to the same Docker network.

### Option B: Native Linux

Use this for a systemd-based Debian or Ubuntu host or VM on amd64 or arm64. The
host must be able to reach Hermes on the private network.

Run on that Linux host:

```bash
sudo ./install.sh native --version v0.1.0
```

The installer:

1. Prompts for the bridge configuration.
2. Installs a private pinned Python 3.12 runtime and hash-locked dependencies
   under `/opt/hermes-agent-bridge` without modifying system Python.
3. Creates the non-login `hermes-agent-bridge` service user.
4. Installs and starts a hardened systemd service.
5. Runs the shared preflight and verifies the loopback health endpoint.

On success, the installer prints the local or LAN origin for the Cloudflare
Tunnel, for example:

```text
http://192.0.2.20:8080
```

The service still runs with `python3 -m hermes_agent_bridge`, using the private
Python installed for that release.

### Option C: Proxmox

Use this on a Proxmox VE 8 or 9 host when you want a dedicated LXC. The
installer creates the LXC and installs the bridge for you.

Run this directly on the Proxmox VE host:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/nberardi/hermes-agent-bridge/refs/heads/main/install.sh \
  | sudo bash -s -- proxmox
```

The installer:

1. Suggests the next free CT ID. Press Enter to accept it or enter another
   unused ID.
2. Uses DHCP by default. If you choose static networking, it asks for the LXC
   IP/CIDR and gateway.
3. Creates the unprivileged Debian LXC with the Proxmox Community Scripts
   generated-mode installer.
4. Transfers configuration through a protected temporary file.
5. Runs the native installer inside the LXC with `pct exec` and then removes
   host-side temporary secrets.

For a static network, the community script receives the equivalent of:

```bash
mode=generated \
var_ctid='105' \
var_net='192.168.1.20/24' \
var_gateway='192.168.1.1' \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/debian.sh)"
```

The LXC does not install Docker. On success, the installer prints the CT ID,
LXC address, and Tunnel origin, for example:

```text
LXC address: 192.0.2.30
Cloudflare Tunnel origin: http://192.0.2.30:8080
```

Rerunning the Proxmox command upgrades the recorded LXC. It does not create a
new one.

## Finish the Cloudflare setup

After any one installation path succeeds:

1. Route the public hostname on the existing tunnel to the exact origin printed
   by the installer.
2. Create or select the Cloudflare Access application for that hostname. Its
   AUD must match `CF_ACCESS_AUD` in the bridge configuration.
3. Use two separate Access policies:
   - **Service Auth** for the MCP client's service token
   - **Allow** for operator login
4. Do not mix Service Auth and Allow in one policy.

The bridge validates `Cf-Access-Jwt-Assertion` at the origin against the team
JWKS and this application's AUD. A Docker or LAN neighbor without a valid JWT
still receives `401`.

Add the remote MCP connector using:

- URL: `https://mcp.example.com/mcp`
- Header: `CF-Access-Client-Id`
- Header: `CF-Access-Client-Secret`

Replace the example hostname with your Access hostname. The client must not use
localhost, RFC1918, or stdio to reach Hermes.

## Configuration and upgrades

Every method uses the same guided wizard. Secret prompts are hidden. The final
configuration lives at `/etc/hermes-agent-bridge.env`, owned by root with mode
`0600`.

Required settings:

- `SITE`
- `PUBLIC_HOSTNAMES`
- `HERMES_KANBAN_BOARDS`
- `CF_ACCESS_TEAM_DOMAIN`
- `CF_ACCESS_AUD`
- `HERMES_DASHBOARD_URL`

Optional settings:

- `ALLOWED_ORIGINS`
- `HERMES_DASHBOARD_TOKEN`
- `HERMES_API_URL` and `HERMES_API_KEY` for Ask
- `BIND_HOST` and `BIND_PORT`

`PUBLIC_HOSTNAMES` contains DNS hostnames without schemes or paths.
`ALLOWED_ORIGINS` defaults to the HTTPS origin of each public hostname. Board
slugs contain lowercase letters, digits, hyphens, or underscores, begin with a
letter or digit, and are at most 64 characters.

Use another absolute configuration path with `--env-file PATH`. For automation,
provide a complete env file and add `--non-interactive`.

Rerun the same installation command with a new version for an explicit in-place
upgrade:

```bash
sudo ./install.sh docker --version v0.2.0
# Or rerun the native or Proxmox command originally chosen.
```

Existing settings become the wizard defaults. Releases are installed under
`/opt/hermes-agent-bridge`, and a failed health verification rolls the service
and configuration back. There is no automatic update timer.

The checksum-verified download above is preferred. A secondary convenience
form is available when curl-pipe-shell is acceptable:

```bash
curl --fail --location \
  https://github.com/nberardi/hermes-agent-bridge/releases/download/v0.1.0/install.sh \
  | sudo bash -s -- native --version v0.1.0
```

## Check an installation

The shared preflight validates configuration, fetches the Cloudflare Access
JWKS, and checks the Hermes dashboard. A dashboard failure is fatal. An
unavailable optional Ask gateway is a warning and does not disable the kanban
tools.

Native Linux:

```bash
/opt/hermes-agent-bridge/current/venv/bin/python \
  -m hermes_agent_bridge check --env-file /etc/hermes-agent-bridge.env
```

Docker:

```bash
docker exec hermes-agent-bridge python -m hermes_agent_bridge check
```

Proxmox:

```bash
pct exec <CTID> -- /opt/hermes-agent-bridge/current/venv/bin/python \
  -m hermes_agent_bridge check --env-file /etc/hermes-agent-bridge.env
```

Exit codes are `0` for success, including an Ask warning; `2` for configuration;
`3` for JWKS; and `4` for a dashboard failure.

## What the bridge exposes

Tools:

- `health` checks dashboard reachability and the optional Ask gateway.
- `list_allowed_boards` returns only the configured board slugs.
- `list_board(board)` and `get_task(board, task_id)` read an explicitly allowed
  kanban board.
- `create_queued_card(board, title, body)` creates an unassigned triage card.
  It does not start or approve work.
- `ask` sends one question to the internal OpenAI-compatible gateway. It is
  disabled when `HERMES_API_URL` is unset or the gateway is down.

There is no tool that approves a card, sets `ready` or `running`, calls
`specify`, `decompose`, or `dispatch`, or reaches a run-approval endpoint.
There is no stable in-tree vault route in this release. Kanban is the write
surface.

## Local development

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
python3 -m pip install -e .
make ci
```

Python 3.12 or newer is required. `uv.lock` is the dependency source of truth;
`requirements-dev.txt` is the pinned, hashed development export.

## Fail-closed merge bar

1. Requests to `/mcp` without a valid Cloudflare Access JWT receive `401`.
2. The origin verifies the JWT against the team domain and this application's
   AUD. A token for another Access app is rejected.
3. Loopback `GET /healthz` is liveness only and does not call Hermes. Forwarded
   requests and network neighbors cannot use its JWT exception.
4. Compose has no published host port. Cloudflare reaches it over the private
   Docker network.
5. The bridge can queue an unassigned triage card but cannot approve, promote,
   or dispatch it.
6. Ask is optional. Its failure does not block dashboard and kanban tools.
7. The supported MCP path is the named Access-protected HTTPS URL, not a local,
   private-address, or stdio shortcut.

## Done when

An MCP client can add the Access-protected HTTPS MCP URL and call `health`,
`ask`, or `create_queued_card` with an explicitly allowed board against Hermes,
and unauthenticated origin calls are rejected.
