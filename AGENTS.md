# Agent notes

Streamable HTTP MCP bridge to a private-network Hermes. Host setup (hostnames, Access team/AUD, dashboard URL, tokens) lives in env, never in this repo.

## Start

Python 3.12+.

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
python3 -m pip install -e .
```

Runtime (after required env is filled): `python3 -m hermes_agent_bridge`.

Required env: `SITE`, `PUBLIC_HOSTNAMES`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `HERMES_DASHBOARD_URL`. Copy `deploy/site.env.example`; do not commit the filled file.

## Test and lint

`make ci` is what GitHub Actions runs: `ruff check`, `ruff format --check`, `mypy src`, `pytest`.

Do not skip CI. Do not merge red.

## Do not commit

- `.env`, filled host env files, tokens, AUDs, real dashboard URLs, real hostnames
- Secrets, credentials, or a guessed network layout

Keep PRs small. Keep docs generic (`example.com` only).
