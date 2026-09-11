from __future__ import annotations

import json

import pytest

from mech_cad_design_agent.gear_sizing import GearDriveInput, size_gear_drive, to_mapping
from mech_cad_design_agent.gear_validation_spec import build_gear_validation_spec


def _sized_mapping() -> dict:
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
    assert result.status == "sized"
    return to_mapping(result)


def test_spec_names_both_required_objects() -> None:
    spec = build_gear_validation_spec(_sized_mapping())
    assert spec["required_objects"] == ["Pinion", "Gear"]


def test_spec_checks_the_design_values_not_only_geometry() -> None:
    """A model built at the wrong module must fail on the module property,
    not only on a downstream bounding-box symptom."""
    spec = build_gear_validation_spec(_sized_mapping())
    by_metric = {
        (item["object"], item["metric"]): item["expected"] for item in spec["dimensions"]
    }
    assert by_metric[("Pinion", "property:Module")] == 3.0
    assert by_metric[("Pinion", "property:Teeth")] == 17.0
    assert by_metric[("Gear", "property:Teeth")] == 52.0
    assert by_metric[("Gear", "property:CentreDistanceMM")] == 103.5
    assert by_metric[("Pinion", "property:BoreMM")] == 25.0


def test_spec_carries_an_interference_gate() -> None:
    spec = build_gear_validation_spec(_sized_mapping())
    assert spec["interference_pairs"] == [
        {"a": "Pinion", "b": "Gear", "max_common_volume_mm3": 1.0}
    ]


def test_spec_is_json_serialisable() -> None:
    spec = build_gear_validation_spec(_sized_mapping())
    assert json.loads(json.dumps(spec)) == spec


def test_object_names_are_overridable() -> None:
    spec = build_gear_validation_spec(
        _sized_mapping(), pinion_object="InputGear", gear_object="OutputGear"
    )
    assert spec["required_objects"] == ["InputGear", "OutputGear"]
    assert spec["interference_pairs"][0] == {
        "a": "InputGear",
        "b": "OutputGear",
        "max_common_volume_mm3": 1.0,
    }


def test_refuses_a_result_that_was_not_sized() -> None:
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
    assert rejected.status == "rejected"
    with pytest.raises(ValueError, match="rejected"):
        build_gear_validation_spec(to_mapping(rejected))
