"""Preliminary spur gear-drive sizing.

Takes duty inputs and works through ratio, tooth counts, module selection,
geometry, forces, bending and contact stress, shaft diameter and bearing load.
The result separates what the user gave, what was derived, and what the engine
assumed, and it states plainly what it did not evaluate.

This is sizing evidence, not certification. See sizing.LIMITATIONS.
"""

from __future__ import annotations

from .errors import GearSizingError
from .materials import GEAR_MATERIALS, SHAFT_MATERIALS, GearMaterial, ShaftMaterial
from .report import build_variables, to_json, to_markdown, to_mapping, to_review_sheet
from .sizing import (
    LIMITATIONS,
    UNVERIFIED_COEFFICIENTS,
    Assumption,
    Check,
    GearDriveInput,
    GearDriveResult,
    Iteration,
    size_gear_drive,
)

__all__ = [
    "GEAR_MATERIALS",
    "LIMITATIONS",
    "SHAFT_MATERIALS",
    "UNVERIFIED_COEFFICIENTS",
    "Assumption",
    "Check",
    "GearDriveInput",
    "GearDriveResult",
    "GearMaterial",
    "GearSizingError",
    "Iteration",
    "ShaftMaterial",
    "size_gear_drive",
    "build_variables",
    "to_json",
    "to_mapping",
    "to_markdown",
    "to_review_sheet",
]
