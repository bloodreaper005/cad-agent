from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mech_cad_design_agent import cli
from mech_cad_design_agent.gear_sizing import (
    GearDriveInput,
    GearSizingError,
    build_variables,
    size_gear_drive,
    to_mapping,
    to_review_sheet,
)


def _sized() -> dict:
    return to_mapping(
        size_gear_drive(
            GearDriveInput(
                power_kw=7.5,
                pinion_speed_rpm=1450.0,
                gear_speed_rpm=480.0,
                pinion_material="20MnCr5_carburised_G2",
                gear_material="20MnCr5_carburised_G2",
                duty="moderate",
                life_hours=20000.0,
                safety_factor=1.5,
            )
        )
    )


def _build_arguments(tmp_path: Path, approve: str) -> argparse.Namespace:
    sizing = tmp_path / "sizing.json"
    sizing.write_text(json.dumps(_sized()), encoding="utf-8")
    return argparse.Namespace(
        workspace=tmp_path / "workspace",
        design_id="reduction-stage",
        title="Reduction Stage",
        sizing=sizing,
        approve=approve,
    )


# ---------------------------------------------------------------------------
# The approval gate. Nothing may be modelled without a clear yes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "approve,decision,next_action",
    [
        ("no, the face width is too wide", "REJECT", "revise_the_sizing"),
        ("not yet, let me think about it", "UNCLEAR", "restate_the_decision"),
        ("maybe, if the ratio error is acceptable", "UNCLEAR", "restate_the_decision"),
    ],
)
def test_build_refuses_without_a_clear_approval(
    tmp_path: Path, approve: str, decision: str, next_action: str
) -> None:
    """A refusal must touch nothing: no design job, no workspace, no geometry."""
    arguments = _build_arguments(tmp_path, approve)
    result = cli._gear_build_command(arguments)

    assert result["status"] == "not_approved"
    assert result["decision_state"] == decision
    assert result["built"] is False
    assert result["next_action"] == next_action
    assert not (tmp_path / "workspace").exists()


def test_refusal_exits_as_a_warning_not_a_failure() -> None:
    """Waiting on a decision is not an error; it is a design that has not
    been approved yet."""
    assert cli._gear_exit_code({"status": "not_approved"}) == 1
    assert cli._gear_exit_code({"status": "built"}) == 0
    assert cli._gear_exit_code({"status": "sized", "warnings": []}) == 0
    assert cli._gear_exit_code({"status": "sized", "warnings": ["x"]}) == 1
    assert cli._gear_exit_code({"status": "rejected"}) == 3


@pytest.mark.parametrize(
    "approve", ["yes, looks good, build it", "approved", "go ahead", "同意"]
)
def test_approval_is_read_by_meaning_not_by_a_fixed_phrase(
    tmp_path: Path, approve: str
) -> None:
    """The gate accepts natural wording in either language.

    Getting past the gate is proved by the failure that follows it: the build
    reaches for a workspace that does not exist here. A refusal returns
    `not_approved` instead and never gets that far.
    """
    from mech_cad_design_agent.bootstrap_diagnostics import DiagnosticGateError

    with pytest.raises(DiagnosticGateError):
        cli._gear_build_command(_build_arguments(tmp_path, approve))


def test_a_rejected_sizing_cannot_be_built(tmp_path: Path) -> None:
    rejected = to_mapping(
        size_gear_drive(
            GearDriveInput(
                power_kw=250.0,
                pinion_speed_rpm=1450.0,
                gear_speed_rpm=480.0,
                pinion_material="20MnCr5_carburised_G2",
                gear_material="20MnCr5_carburised_G2",
                duty="moderate",
                life_hours=20000.0,
                safety_factor=3.0,
                max_module_mm=4.0,
            )
        )
    )
    assert rejected["status"] == "rejected"
    sizing = tmp_path / "sizing.json"
    sizing.write_text(json.dumps(rejected), encoding="utf-8")
    arguments = argparse.Namespace(
        workspace=tmp_path / "workspace",
        design_id="reduction-stage",
        title="Reduction Stage",
        sizing=sizing,
        approve="yes, build it",
    )
    with pytest.raises(GearSizingError) as caught:
        cli._gear_build_command(arguments)
    assert caught.value.code == "sizing.not_sized"


# ---------------------------------------------------------------------------
# The review sheet, which is what the approval is given against.
# ---------------------------------------------------------------------------


def test_review_sheet_shows_the_variables_the_build_consumes() -> None:
    sheet = to_review_sheet(
        size_gear_drive(
            GearDriveInput(
                power_kw=7.5,
                pinion_speed_rpm=1450.0,
                gear_speed_rpm=480.0,
                pinion_material="20MnCr5_carburised_G2",
                gear_material="20MnCr5_carburised_G2",
                duty="moderate",
                life_hours=20000.0,
                safety_factor=1.5,
            )
        )
    )
    assert "BUILD VARIABLES" in sheet
    for fragment in ("module", "pinion teeth", "face width", "centre distance", "bore"):
        assert fragment in sheet
    assert "NOT EVALUATED" in sheet
    assert "Nothing is modelled until you" in sheet
    assert "20000 h" in sheet


def test_build_variables_match_the_review_sheet_source() -> None:
    result = size_gear_drive(
        GearDriveInput(
            power_kw=7.5,
            pinion_speed_rpm=1450.0,
            gear_speed_rpm=480.0,
            pinion_material="20MnCr5_carburised_G2",
            gear_material="20MnCr5_carburised_G2",
            duty="moderate",
            life_hours=20000.0,
            safety_factor=1.5,
        )
    )
    variables = build_variables(result)
    assert variables["module_mm"] == 3.0
    assert variables["pinion_teeth"] == 17
    assert variables["gear_teeth"] == 52
    assert variables["face_width_mm"] == 51.0
    assert variables["centre_distance_mm"] == 103.5
    assert variables["bore_mm"] == 25.0
    assert variables["pressure_angle_deg"] == 20.0


def test_build_variables_refuse_an_unsized_result() -> None:
    rejected = size_gear_drive(
        GearDriveInput(
            power_kw=250.0,
            pinion_speed_rpm=1450.0,
            gear_speed_rpm=480.0,
            pinion_material="20MnCr5_carburised_G2",
            gear_material="20MnCr5_carburised_G2",
            duty="moderate",
            life_hours=20000.0,
            safety_factor=3.0,
            max_module_mm=4.0,
        )
    )
    with pytest.raises(GearSizingError):
        build_variables(rejected)


def test_review_sheet_reports_a_rejected_sizing_without_pretending() -> None:
    sheet = to_review_sheet(
        size_gear_drive(
            GearDriveInput(
                power_kw=250.0,
                pinion_speed_rpm=1450.0,
                gear_speed_rpm=480.0,
                pinion_material="20MnCr5_carburised_G2",
                gear_material="20MnCr5_carburised_G2",
                duty="moderate",
                life_hours=20000.0,
                safety_factor=3.0,
                max_module_mm=4.0,
            )
        )
    )
    assert "RESULT: REJECTED" in sheet
    assert "BUILD VARIABLES" not in sheet


def test_gear_parser_exposes_size_and_build() -> None:
    parser = cli._parser()
    sized = parser.parse_args(
        [
            "gear", "size",
            "--power-kw", "7.5",
            "--pinion-rpm", "1450",
            "--gear-rpm", "480",
            "--pinion-material", "20MnCr5_carburised_G2",
            "--gear-material", "20MnCr5_carburised_G2",
            "--duty", "moderate",
            "--life-hours", "20000",
            "--safety-factor", "1.5",
            "--format", "review",
        ]
    )
    assert sized.gear_command == "size"
    assert sized.output_format == "review"

    built = parser.parse_args(
        [
            "gear", "build",
            "--design-id", "x",
            "--title", "T",
            "--sizing", "s.json",
            "--approve", "yes",
        ]
    )
    assert built.gear_command == "build"
    assert built.approve == "yes"
