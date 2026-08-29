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

### Option A: Docker

Use this when Hermes and cloudflared already share a Docker network. The
network must exist before installation. Its name is often `hermes`.

Run on the Docker host:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/nberardi/hermes-agent-bridge/refs/heads/main/install.sh \
  | sudo bash -s -- docker
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
curl -fsSL \
  https://raw.githubusercontent.com/nberardi/hermes-agent-bridge/refs/heads/main/install.sh \
  | sudo bash -s -- native
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
3. Downloads the Proxmox Community Scripts Debian installer and passes it the
   selected CT ID and network settings in generated mode.
4. Transfers configuration through a protected temporary file.
5. Runs the native installer inside the LXC with `pct exec` and then removes
   host-side temporary secrets.

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

To upgrade, rerun the same installation command. The installer uses its current
release by default:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/nberardi/hermes-agent-bridge/refs/heads/main/install.sh \
  | sudo bash -s -- docker
# Or rerun the native or Proxmox command originally chosen.
```

Existing settings become the wizard defaults. Releases are installed under
`/opt/hermes-agent-bridge`, and a failed health verification rolls the service
and configuration back. There is no automatic update timer.

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

## Use the bridge

After adding the MCP URL and Cloudflare Access headers to your MCP client, the
client can call these tools:

| Tool | What it does |
| --- | --- |
| `health` | Checks whether the Hermes dashboard and optional Ask gateway are reachable. |
| `list_allowed_boards` | Lists the kanban boards this bridge is configured to access. |
| `list_board(board)` | Lists cards on an allowed board. |
| `get_task(board, task_id)` | Reads one card from an allowed board. |
| `create_queued_card(board, title, body)` | Creates an unassigned triage card without approving or starting it. |
| `ask` | Sends a question to the optional internal Ask gateway. |

The bridge deliberately cannot approve, assign, start, plan, decompose, or
dispatch work. It does not expose the Hermes vault. Its only write operation is
creating an unassigned kanban card. The `ask` tool reports that it is disabled
when `HERMES_API_URL` is not configured or its gateway is down.

## Security boundaries

These rules describe what the installation protects:

- `/mcp` requires a valid Cloudflare Access JWT for the configured team and
  application AUD. Missing tokens and tokens for another application receive
  `401`.
- Docker does not publish a host port. Cloudflare reaches the bridge through
  the private Docker network.
- Loopback `GET /healthz` reports liveness only. It does not call Hermes, and
  its loopback exception cannot be used by forwarded requests or network
  neighbors.
- The supported MCP endpoint is the Access-protected HTTPS hostname, not a
  localhost, private-address, or stdio shortcut.
- A failure of the optional Ask gateway does not disable dashboard or kanban
  tools.

## Development

This section is only for contributors changing the bridge itself. It is not
part of installation.

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
python3 -m pip install -e .
make ci
```

Python 3.12 or newer is required. `uv.lock` is the dependency source of truth;
`requirements-dev.txt` is the pinned, hashed development export.
