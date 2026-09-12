"""Check the FreeCAD GUI bridge is bound to loopback and not serving remotely.

The rule that the bridge must run with `remote_enabled` false has existed since
0.7 in AGENTS.md and is asserted by the live test suite. Neither of those runs
on a user's machine. The product itself never looked, so a bridge brought up
remote-enabled was a written rule with nothing behind it, on the one component
that executes arbitrary agent-authored Python.

This closes that as far as it honestly can. The bridge's settings file has no
fixed location, so it is read from `MECH_DESIGN_FREECAD_GUI_MCP_SETTINGS` when
that is set. When it is not, the answer is `unverified` rather than `ok`: the
distinction between "checked and safe" and "could not check" belongs in the
result, not in the reader's assumptions.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

SETTINGS_ENVIRONMENT_VARIABLE = "MECH_DESIGN_FREECAD_GUI_MCP_SETTINGS"
LOOPBACK = "127.0.0.1"


@dataclass(frozen=True)
class BridgeSecurity:
    """What could be established about the bridge's exposure."""

    status: str  # "ok", "unverified", or "insecure"
    code: str
    message: str
    settings_path: str | None = None

    @property
    def safe_to_proceed(self) -> bool:
        """An unverified bridge does not block work; a remote-enabled one does.

        Refusing whenever the settings cannot be found would make the check
        unusable, since most installations never set the variable. Refusing when
        the file is present and says the bridge is listening remotely is a
        different matter: that is a fact, not an absence of one.
        """
        return self.status != "insecure"

    def as_component(self) -> dict[str, str]:
        """Render for the system status report.

        Only a bridge known to be exposed is a warning. An unverified one is
        reported as ok with a message saying plainly that it was not checked,
        because the settings variable is unset on most installations and
        degrading everyone's overall status for a check that was never
        available would train the reader to ignore it. The distinction stays
        visible in the message and in `status`.
        """
        return {
            "name": "freecad_gui_bridge",
            "status": "warning" if self.status == "insecure" else "ok",
            "code": self.code,
            "message": self.message,
        }


def inspect_bridge_settings(
    path: str | Path | None = None, *, environ: dict[str, str] | None = None
) -> BridgeSecurity:
    """Read the bridge settings and report whether it is loopback-only."""
    environment = os.environ if environ is None else environ
    raw = path if path is not None else environment.get(
        SETTINGS_ENVIRONMENT_VARIABLE, ""
    )
    if not raw or not str(raw).strip():
        return BridgeSecurity(
            status="unverified",
            code="BRIDGE_SETTINGS_NOT_CONFIGURED",
            message=(
                "FreeCAD GUI bridge exposure was not checked; set "
                f"{SETTINGS_ENVIRONMENT_VARIABLE} to its settings file to verify"
            ),
        )
    settings = Path(str(raw)).expanduser()
    if not settings.is_file():
        return BridgeSecurity(
            status="unverified",
            code="BRIDGE_SETTINGS_MISSING",
            message=f"FreeCAD GUI bridge settings were not found at {settings}",
            settings_path=str(settings),
        )
    try:
        value = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return BridgeSecurity(
            status="unverified",
            code="BRIDGE_SETTINGS_UNREADABLE",
            message=f"FreeCAD GUI bridge settings could not be read: {exc}",
            settings_path=str(settings),
        )
    if not isinstance(value, dict):
        return BridgeSecurity(
            status="unverified",
            code="BRIDGE_SETTINGS_UNREADABLE",
            message="FreeCAD GUI bridge settings are not a JSON object",
            settings_path=str(settings),
        )

    if value.get("remote_enabled") is not False:
        return BridgeSecurity(
            status="insecure",
            code="BRIDGE_REMOTE_ENABLED",
            message=(
                "FreeCAD GUI bridge has remote_enabled set; it executes "
                "arbitrary Python and must be loopback-only"
            ),
            settings_path=str(settings),
        )
    allowed = value.get("allowed_ips")
    if allowed is not None and allowed != LOOPBACK:
        return BridgeSecurity(
            status="insecure",
            code="BRIDGE_NOT_LOOPBACK_ONLY",
            message=(
                f"FreeCAD GUI bridge allows {allowed!r}; it must be "
                f"loopback-only ({LOOPBACK})"
            ),
            settings_path=str(settings),
        )
    return BridgeSecurity(
        status="ok",
        code="BRIDGE_LOOPBACK_ONLY",
        message="FreeCAD GUI bridge is loopback-only",
        settings_path=str(settings),
    )


__all__ = [
    "LOOPBACK",
    "SETTINGS_ENVIRONMENT_VARIABLE",
    "BridgeSecurity",
    "inspect_bridge_settings",
]
