"""The FreeCAD GUI bridge exposure check.

The bridge executes arbitrary agent-authored Python, so whether it listens only
on loopback is the most consequential setting in the system. Until now the rule
lived in AGENTS.md and in tests that never run on a user's machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mech_cad_design_agent.freecad_bridge_security import (
    SETTINGS_ENVIRONMENT_VARIABLE,
    inspect_bridge_settings,
)


def _settings(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "mcp_settings.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_a_loopback_only_bridge_is_accepted(tmp_path: Path) -> None:
    result = inspect_bridge_settings(
        _settings(tmp_path, {"remote_enabled": False, "allowed_ips": "127.0.0.1"})
    )
    assert result.status == "ok"
    assert result.code == "BRIDGE_LOOPBACK_ONLY"
    assert result.safe_to_proceed is True


def test_a_remote_enabled_bridge_is_refused(tmp_path: Path) -> None:
    """The case the rule existed for, now actually enforced."""
    result = inspect_bridge_settings(_settings(tmp_path, {"remote_enabled": True}))
    assert result.status == "insecure"
    assert result.code == "BRIDGE_REMOTE_ENABLED"
    assert result.safe_to_proceed is False
    assert result.as_component()["status"] == "warning"


def test_a_bridge_open_beyond_loopback_is_refused(tmp_path: Path) -> None:
    result = inspect_bridge_settings(
        _settings(tmp_path, {"remote_enabled": False, "allowed_ips": "192.168.1.0/24"})
    )
    assert result.status == "insecure"
    assert result.code == "BRIDGE_NOT_LOOPBACK_ONLY"
    assert result.safe_to_proceed is False


def test_a_missing_remote_enabled_key_is_treated_as_enabled(tmp_path: Path) -> None:
    """Absent is not false. A setting that is not switched off is not off."""
    result = inspect_bridge_settings(_settings(tmp_path, {"allowed_ips": "127.0.0.1"}))
    assert result.status == "insecure"


@pytest.mark.parametrize(
    "contents, expected_code",
    [
        ("not json at all", "BRIDGE_SETTINGS_UNREADABLE"),
        ('["a list"]', "BRIDGE_SETTINGS_UNREADABLE"),
    ],
)
def test_unreadable_settings_are_unverified_rather_than_assumed_safe(
    tmp_path: Path, contents: str, expected_code: str
) -> None:
    path = tmp_path / "mcp_settings.json"
    path.write_text(contents, encoding="utf-8")
    result = inspect_bridge_settings(path)
    assert result.status == "unverified"
    assert result.code == expected_code


def test_an_unconfigured_check_reports_unverified_not_ok() -> None:
    """The difference between checked-and-safe and could-not-check is kept.

    It does not block work, because the settings variable is unset on most
    installations, but the result never claims the bridge was verified.
    """
    result = inspect_bridge_settings(environ={})
    assert result.status == "unverified"
    assert result.code == "BRIDGE_SETTINGS_NOT_CONFIGURED"
    assert result.safe_to_proceed is True
    assert SETTINGS_ENVIRONMENT_VARIABLE in result.message
    # Reported as ok so an unavailable optional check does not train the reader
    # to ignore the status line, while the message still says it was not run.
    component = result.as_component()
    assert component["status"] == "ok"
    assert "not checked" in component["message"]


def test_a_settings_path_that_does_not_exist_is_unverified(tmp_path: Path) -> None:
    result = inspect_bridge_settings(tmp_path / "absent.json")
    assert result.status == "unverified"
    assert result.code == "BRIDGE_SETTINGS_MISSING"


def test_the_environment_variable_is_read_when_no_path_is_given(
    tmp_path: Path,
) -> None:
    path = _settings(tmp_path, {"remote_enabled": True})
    result = inspect_bridge_settings(
        environ={SETTINGS_ENVIRONMENT_VARIABLE: str(path)}
    )
    assert result.status == "insecure"
