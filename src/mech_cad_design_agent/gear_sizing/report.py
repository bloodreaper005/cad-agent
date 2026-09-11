from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .errors import GearSizingError
from .sizing import GearDriveResult


SCHEMA_VERSION = "GearDriveSizing/v1"
STANDARDS = (
    "ANSI/AGMA 2101-D04",
    "ISO 6336-3",
    "ISO 54:1996",
    "ISO 281",
    "ASME B106.1M-1985",
)


def to_mapping(result: GearDriveResult) -> dict[str, Any]:
    """Shape the result as a plain JSON-ready mapping."""
    return {
        "schema_version": SCHEMA_VERSION,
        "engine": "gear-drive-sizing",
        "standards": list(STANDARDS),
        "status": result.status,
        "given": result.given,
        "assumptions": [asdict(item) for item in result.assumptions],
        "derived": result.derived,
        "checks": [asdict(item) for item in result.checks],
        "iterations": [asdict(item) for item in result.iterations],
        "warnings": list(result.warnings),
        "limitations": list(result.limitations),
        "unverified_coefficients": list(result.unverified),
    }


def to_json(result: GearDriveResult, *, indent: int | None = 2) -> str:
    """Serialise deterministically. A non-finite value raises rather than ships."""
    try:
        return json.dumps(
            to_mapping(result),
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
            allow_nan=False,
        )
    except ValueError as exc:
        raise GearSizingError(
            "numeric.non_finite",
            "a non-finite value reached the sizing result",
        ) from exc


def build_variables(result: GearDriveResult) -> dict[str, Any]:
    """The values the CAD build consumes, separated from everything else.

    A build reads exactly these. Keeping them in one block makes the review
    before the build a review of what will actually be modelled, rather than
    of the whole sizing document.
    """
    if result.status != "sized":
        raise GearSizingError(
            "sizing.not_sized",
            f"a {result.status} result has no geometry to build",
        )
    geometry = result.derived["geometry"]
    teeth = result.derived["teeth"]
    return {
        "module_mm": geometry["module_mm"],
        "pinion_teeth": teeth["pinion"],
        "gear_teeth": teeth["gear"],
        "pressure_angle_deg": round(
            geometry["pressure_angle_rad"] * 180.0 / 3.141592653589793, 4
        ),
        "face_width_mm": geometry["face_width_mm"],
        "centre_distance_mm": geometry["centre_distance_mm"],
        "bore_mm": result.derived["shaft"]["selected_diameter_mm"],
        "pinion_pitch_mm": geometry["pinion_pitch_mm"],
        "gear_pitch_mm": geometry["gear_pitch_mm"],
        "pinion_tip_mm": geometry["pinion_tip_mm"],
        "gear_tip_mm": geometry["gear_tip_mm"],
        "pinion_root_mm": geometry["pinion_root_mm"],
        "gear_root_mm": geometry["gear_root_mm"],
    }


def _row(label: str, value: object, unit: str = "") -> str:
    if isinstance(value, float):
        # Keep whole numbers readable: 20000 hours, not 2e+04.
        rendered = (
            f"{value:.0f}"
            if value == int(value) and abs(value) < 1e9
            else f"{value:,.4g}"
        )
    else:
        rendered = str(value)
    return f"  {label:<34}{rendered}{(' ' + unit) if unit else ''}"


def to_review_sheet(result: GearDriveResult) -> str:
    """A plain-text sheet to read before approving the build.

    Everything a reviewer needs to accept or reject the design direction:
    what will be modelled, what it was derived from, how much margin it
    carries, and what the engine assumed or could not evaluate.
    """
    given = result.given
    lines = [
        "GEAR DRIVE SIZING",
        "=" * 62,
        "",
        "GIVEN",
        _row("power", given["power_kw"], "kW"),
        _row("pinion speed", given["pinion_speed_rpm"], "rpm"),
        _row("gear speed (requested)", given["gear_speed_rpm"], "rpm"),
        _row("gear type", given["gear_type"]),
        _row("pinion material", given["pinion_material"]),
        _row("gear material", given["gear_material"]),
        _row("duty", given["duty"]),
        _row("life", given["life_hours"], "h"),
        _row("safety factor required", given["safety_factor"]),
        "",
    ]

    if result.status != "sized":
        lines.extend(
            [
                f"RESULT: {result.status.upper()}",
                "",
                "  No module in the series satisfies the requested safety factor.",
                "",
                "FAILED CHECKS",
            ]
        )
        lines.extend(
            _row(check.id, f"{check.actual:.4g}" if check.actual else "-")
            for check in result.checks
            if check.status != "passed"
        )
        return "\n".join(lines) + "\n"

    derived = result.derived
    teeth = derived["teeth"]
    geometry = derived["geometry"]
    forces = derived["forces"]
    shaft = derived["shaft"]
    bending = derived["bending"]
    contact = derived["contact"]

    lines.extend(
        [
            "BUILD VARIABLES  (these are what the CAD model is built from)",
            _row("module", geometry["module_mm"], "mm"),
            _row("pinion teeth", teeth["pinion"]),
            _row("gear teeth", teeth["gear"]),
            _row("pressure angle", 20.0, "deg"),
            _row("face width", geometry["face_width_mm"], "mm"),
            _row("centre distance", geometry["centre_distance_mm"], "mm"),
            _row("bore (both members)", shaft["selected_diameter_mm"], "mm"),
            "",
            "RESULTING GEOMETRY",
            _row("pinion pitch / tip / root",
                 f"{geometry['pinion_pitch_mm']:.2f} / "
                 f"{geometry['pinion_tip_mm']:.2f} / "
                 f"{geometry['pinion_root_mm']:.2f}", "mm"),
            _row("gear pitch / tip / root",
                 f"{geometry['gear_pitch_mm']:.2f} / "
                 f"{geometry['gear_tip_mm']:.2f} / "
                 f"{geometry['gear_root_mm']:.2f}", "mm"),
            _row("transverse contact ratio", geometry["transverse_contact_ratio"]),
            "",
            "KINEMATICS",
            _row("ratio", f"{teeth['ratio']:.4f}"),
            _row("ratio error", f"{teeth['ratio_error']:+.2%}"),
            _row("actual gear speed", derived["actual_gear_speed_rpm"], "rpm"),
            _row("hunting (even wear)", "yes" if teeth["hunting"] else "NO"),
            "",
            "LOADS",
            _row("pinion torque", derived["torque_nm"], "N.m"),
            _row("tangential force", forces["tangential_n"], "N"),
            _row("radial force", forces["radial_n"], "N"),
            _row("pitch line velocity", forces["pitch_velocity_ms"], "m/s"),
            "",
            "MARGINS  (required " + f"{given['safety_factor']}" + ")",
            _row("bending, pinion", bending["pinion"]["safety_factor"]),
            _row("bending, gear", bending["gear"]["safety_factor"]),
            _row("contact, pinion", contact["pinion"]["safety_factor"]),
            _row("contact, gear", contact["gear"]["safety_factor"]),
            _row("governing", derived["governing_check"]),
            "",
            "SHAFT AND BEARINGS",
            _row("shaft diameter", shaft["selected_diameter_mm"], "mm"),
            _row("bending moment", shaft["loads"]["bending_moment_nmm"], "N.mm"),
            _row("bearing span (derived)", shaft["loads"]["span_mm"], "mm"),
            _row("required dynamic capacity",
                 derived["bearings"]["required_dynamic_capacity_n"] / 1000.0, "kN"),
            "",
        ]
    )

    if result.warnings:
        lines.append("WARNINGS")
        lines.extend(f"  - {item}" for item in result.warnings)
        lines.append("")

    lines.append("ASSUMED (not supplied by you)")
    lines.extend(
        f"  - {item.id} = {item.value} {item.unit}".rstrip()
        for item in result.assumptions
    )
    lines.extend(["", "NOT EVALUATED"])
    lines.extend(f"  - {item}" for item in result.limitations)
    lines.extend(
        [
            "",
            "=" * 62,
            "Review the build variables above. Nothing is modelled until you",
            "approve them.",
        ]
    )
    return "\n".join(lines) + "\n"


def to_markdown(result: GearDriveResult) -> str:
    """A short human-readable summary for design evidence."""
    lines = [f"# Gear drive sizing ({result.status})", ""]
    given = result.given
    lines.append(
        f"{given['power_kw']} kW, {given['pinion_speed_rpm']} to "
        f"{given['gear_speed_rpm']} rpm, {given['duty']} duty, "
        f"safety factor {given['safety_factor']}."
    )
    lines.append("")

    if result.status == "sized":
        derived = result.derived
        teeth = derived["teeth"]
        mesh = derived["geometry"]
        shaft = derived["shaft"]
        lines.extend(
            [
                "## Result",
                "",
                f"- Teeth: {teeth['pinion']} / {teeth['gear']}, "
                f"ratio {teeth['ratio']:.4f} ({teeth['ratio_error']:+.2%}), "
                f"{'hunting' if teeth['hunting'] else 'not hunting'}",
                f"- Module: {mesh['module_mm']} mm, face width {mesh['face_width_mm']} mm",
                f"- Pitch diameters: {mesh['pinion_pitch_mm']:.2f} / "
                f"{mesh['gear_pitch_mm']:.2f} mm",
                f"- Centre distance: {mesh['centre_distance_mm']:.2f} mm",
                f"- Tangential force: {derived['forces']['tangential_n']:.0f} N "
                f"at {derived['forces']['pitch_velocity_ms']:.2f} m/s",
                f"- Pinion shaft: {shaft['selected_diameter_mm']:.0f} mm",
                f"- Required bearing capacity: "
                f"{derived['bearings']['required_dynamic_capacity_n'] / 1000:.2f} kN",
                f"- Governing check: {derived['governing_check']}",
                "",
            ]
        )

    lines.extend(["## Checks", ""])
    for check in result.checks:
        mark = "PASS" if check.status == "passed" else "FAIL"
        actual = "" if check.actual is None else f" (actual {check.actual:.4g}"
        expected = "" if check.expected is None else f", required {check.expected:.4g})"
        lines.append(f"- {mark} `{check.id}`{actual}{expected}")

    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in result.warnings)

    lines.extend(["", "## Assumptions", ""])
    for item in result.assumptions:
        lines.append(f"- `{item.id}` = {item.value} {item.unit} — {item.source}")

    lines.extend(["", "## Not evaluated", ""])
    lines.extend(f"- {item}" for item in result.limitations)
    lines.extend(["", "## Coefficients not read from the source standard", ""])
    lines.extend(f"- {item}" for item in result.unverified)
    return "\n".join(lines) + "\n"


__all__ = [
    "SCHEMA_VERSION",
    "STANDARDS",
    "build_variables",
    "to_json",
    "to_mapping",
    "to_markdown",
    "to_review_sheet",
]
