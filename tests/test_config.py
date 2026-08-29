"""Deployment configuration validation."""

from __future__ import annotations

import pytest

from hermes_agent_bridge.config import Settings


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
