"""Installer contract tests that do not mutate the host."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"

COMPLETE_ENV = {
    "SITE": "site-a",
    "PUBLIC_HOSTNAMES": "mcp.example.com",
    "HERMES_KANBAN_BOARDS": "project-a",
    "ALLOWED_ORIGINS": "https://mcp.example.com",
    "CF_ACCESS_TEAM_DOMAIN": "example.cloudflareaccess.com",
    "CF_ACCESS_AUD": "aud-not-a-secret",
    "HERMES_DASHBOARD_URL": "http://hermes.internal:9119",
    "HERMES_DASHBOARD_TOKEN": "dashboard-secret",
    "HERMES_API_URL": "",
    "HERMES_API_KEY": "ask-secret",
    "BIND_HOST": "0.0.0.0",
    "BIND_PORT": "8080",
}


def _run_wizard(env_file: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    fake_bin = env_file.parent / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_chown = fake_bin / "chown"
    fake_chown.write_text("#!/bin/sh\nexit 0\n")
    fake_chown.chmod(0o755)
    command = """
source "$1"
NON_INTERACTIVE=1
configuration_wizard "$2"
write_config "$2"
"""
    return subprocess.run(
        ["bash", "-c", command, "installer-test", str(INSTALLER), str(env_file)],
        env={
            **os.environ,
            **env,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )


def test_noninteractive_wizard_writes_all_settings_with_mode_0600(tmp_path):
    env_file = tmp_path / "etc" / "bridge.env"

    result = _run_wizard(env_file, COMPLETE_ENV)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    text = env_file.read_text()
    for key, value in COMPLETE_ENV.items():
        assert f"{key}={value}" in text
    assert "dashboard-secret" not in result.stdout
    assert "ask-secret" not in result.stdout


def test_upgrade_preserves_existing_optional_secrets(tmp_path):
    env_file = tmp_path / "bridge.env"
    env_file.write_text(
        """SITE=site-a
PUBLIC_HOSTNAMES=mcp.example.com
HERMES_KANBAN_BOARDS=project-a
CF_ACCESS_TEAM_DOMAIN=example.cloudflareaccess.com
CF_ACCESS_AUD=aud
HERMES_DASHBOARD_URL=http://hermes.internal:9119
HERMES_DASHBOARD_TOKEN=keep-me
BIND_HOST=0.0.0.0
BIND_PORT=8080
"""
    )
    clean_env = {key: "" for key in COMPLETE_ENV}

    result = _run_wizard(env_file, clean_env)

    assert result.returncode == 0, result.stderr
    assert "HERMES_DASHBOARD_TOKEN=keep-me" in env_file.read_text()


def test_noninteractive_wizard_rejects_missing_required_value(tmp_path):
    env = {**COMPLETE_ENV, "CF_ACCESS_AUD": ""}

    result = _run_wizard(tmp_path / "bridge.env", env)

    assert result.returncode != 0
    assert "CF_ACCESS_AUD is required" in result.stderr


def test_failed_docker_health_restores_config_before_previous_release(tmp_path):
    events = tmp_path / "events"
    command = r"""
source "$1"
VERSION=v0.2.0
ENV_FILE=/tmp/test.env
cfg_set BIND_HOST 0.0.0.0
cfg_set BIND_PORT 8080
docker() { return 0; }
choose_docker_network() { printf 'hermes\n'; }
switch_current() { printf 'switch %s\n' "$1" >> "$EVENTS"; }
docker_compose() { printf 'compose %s %s\n' "$1" "$VERSION" >> "$EVENTS"; return 0; }
verify_container() { return 1; }
restore_config() { printf 'restore\n' >> "$EVENTS"; }
export EVENTS="$2"
install_docker /opt/hermes-agent-bridge/releases/v0.2.0 /opt/hermes-agent-bridge/releases/v0.1.0
"""

    result = subprocess.run(
        ["bash", "-c", command, "rollback-test", str(INSTALLER), str(events)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    lines = events.read_text().splitlines()
    assert "switch /opt/hermes-agent-bridge/releases/v0.2.0" in lines
    restore_index = lines.index("restore")
    previous_index = lines.index(
        "compose /opt/hermes-agent-bridge/releases/v0.1.0 v0.1.0"
    )
    assert restore_index < previous_index


def test_deployment_assets_keep_fail_closed_boundaries():
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    unit = (ROOT / "deploy" / "hermes-agent-bridge.service").read_text()

    active_compose = "\n".join(
        line for line in compose.splitlines() if not line.lstrip().startswith("#")
    )
    assert "build:" not in active_compose
    assert "ports:" not in active_compose
    assert "ghcr.io/nberardi/hermes-agent-bridge:${HERMES_BRIDGE_VERSION" in compose
    assert "cap_drop:" in compose and "no-new-privileges:true" in compose
    assert "USER 65532:65532" in dockerfile
    assert "HEALTHCHECK" in dockerfile and "127.0.0.1" in dockerfile
    assert "User=hermes-agent-bridge" in unit
    assert "ProtectSystem=strict" in unit
    assert "NoNewPrivileges=true" in unit


def test_proxmox_uses_generated_community_debian_script():
    installer = INSTALLER.read_text()

    assert (
        "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/debian.sh"
        in installer
    )
    assert "mode=generated" in installer
    assert 'var_ctid="$ctid"' in installer
    assert 'var_net="$network"' in installer
    assert 'var_gateway="$gateway"' in installer
    assert "pct create" not in installer
    assert "pct push" in installer and "--perms 0600" in installer


def test_proxmox_generated_mode_receives_static_network_values(tmp_path):
    events = tmp_path / "events"
    command = r"""
source "$1"
TEMP_DIR=""
curl() { printf 'exit 0\n'; }
bash() { printf '%s|%s|%s|%s\n' "$mode" "$var_ctid" "$var_net" "$var_gateway" >> "$EVENTS"; }
pct() { return 0; }
export EVENTS="$2"
create_debian_lxc 123 10.0.0.20/8 10.0.0.1
CREATED_CT=""
"""

    result = subprocess.run(
        ["bash", "-c", command, "proxmox-test", str(INSTALLER), str(events)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert events.read_text().strip() == "generated|123|10.0.0.20/8|10.0.0.1"


@pytest.mark.parametrize("mode", ["docker", "native", "proxmox"])
def test_installer_runs_from_stdin_for_every_mode(mode):
    result = subprocess.run(
        ["bash", "-s", "--", mode, "--help"],
        input=INSTALLER.read_text(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout


def test_readme_streams_installer_for_every_path_without_clone():
    readme = (ROOT / "README.md").read_text()
    installer_url = (
        "https://raw.githubusercontent.com/nberardi/"
        "hermes-agent-bridge/refs/heads/main/install.sh"
    )

    assert readme.count(installer_url) >= 3
    for mode in ("docker", "native", "proxmox"):
        assert f"| sudo bash -s -- {mode}" in readme
    assert "git clone" not in readme
    assert "community-scripts/ProxmoxVE" not in readme


def test_release_workflow_guards_version_and_builds_both_architectures():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert 'tag != f"v{version}"' in workflow
    assert "linux/amd64,linux/arm64" in workflow
    assert "Refusing to overwrite existing image" in workflow
    assert "sha256sum" in workflow
    assert "visibility=public" in workflow
