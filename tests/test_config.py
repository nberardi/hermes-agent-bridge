"""Deployment configuration validation."""

from __future__ import annotations

import pytest

from hermes_agent_bridge.config import Settings, load_env_file


def _set_required_env(monkeypatch, *, boards: str | None) -> None:
    env = {
        "SITE": "site-a",
        "PUBLIC_HOSTNAMES": "mcp.example.com",
        "CF_ACCESS_TEAM_DOMAIN": "example.cloudflareaccess.com",
        "CF_ACCESS_AUD": "test-aud",
        "HERMES_DASHBOARD_URL": "https://dashboard.example.com",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if boards is None:
        monkeypatch.delenv("HERMES_KANBAN_BOARDS", raising=False)
    else:
        monkeypatch.setenv("HERMES_KANBAN_BOARDS", boards)


def test_allowed_kanban_boards_are_required(monkeypatch):
    _set_required_env(monkeypatch, boards=None)
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "project-a")

    with pytest.raises(RuntimeError, match="HERMES_KANBAN_BOARDS is required"):
        Settings.from_env()


def test_allowed_kanban_boards_are_deduplicated_in_order(monkeypatch):
    _set_required_env(monkeypatch, boards="project-b,project-a,project-b")

    settings = Settings.from_env()

    assert settings.allowed_kanban_boards == ["project-b", "project-a"]


@pytest.mark.parametrize(
    "boards",
    ["Project-A", "-project", "_project", "project/a", "project.a", "a" * 65],
)
def test_invalid_kanban_board_slug_is_rejected(monkeypatch, boards):
    _set_required_env(monkeypatch, boards=boards)

    with pytest.raises(RuntimeError, match="invalid HERMES_KANBAN_BOARDS"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("PUBLIC_HOSTNAMES", "https://mcp.example.com", "DNS hostname"),
        ("PUBLIC_HOSTNAMES", "mcp.example.com:443", "DNS hostname"),
        (
            "CF_ACCESS_TEAM_DOMAIN",
            "https://example.cloudflareaccess.com",
            "DNS hostname",
        ),
        ("HERMES_DASHBOARD_URL", "dashboard.example.com", "HTTP"),
        ("HERMES_API_URL", "ftp://hermes.example.com", "HTTP"),
        ("ALLOWED_ORIGINS", "https://client.example.com/path", "without a path"),
        ("BIND_HOST", "https://127.0.0.1", "DNS hostname"),
        ("BIND_PORT", "70000", "1 to 65535"),
    ],
)
def test_invalid_network_configuration_is_rejected(monkeypatch, key, value, message):
    _set_required_env(monkeypatch, boards="project-a")
    monkeypatch.setenv(key, value)

    with pytest.raises(RuntimeError, match=message):
        Settings.from_env()


def test_env_file_loader_does_not_execute_shell(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist"
    env_file = tmp_path / "bridge.env"
    env_file.write_text(f"SITE=$(touch {marker})\nPUBLIC_HOSTNAMES=mcp.example.com\n")
    monkeypatch.delenv("SITE", raising=False)

    load_env_file(env_file)

    assert not marker.exists()
    assert "$" in __import__("os").environ["SITE"]
