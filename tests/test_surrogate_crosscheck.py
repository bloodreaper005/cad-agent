from __future__ import annotations

from math import pi, sqrt
from typing import Any

import pytest

from mech_cad_design_agent.surrogate_crosscheck import (
    CROSSCHECK_SCHEMA,
    crosscheck_screening,
)
from mech_cad_design_agent.surrogate_screening import (
    SCREENING_SCHEMA,
    SurrogateScreening,
    parse_surrogate_screening,
)


DIGEST = "a" * 64

# Solid round 20 mm cantilever, 1 kN at 100 mm: sigma = M c / I.
BENDING = {
    "kind": "round_cantilever_bending",
    "force_n": 1000.0,
    "length_mm": 100.0,
    "diameter_mm": 20.0,
}
BENDING_MPA = 1000.0 * 100.0 * 10.0 / (pi * 20.0**4 / 64.0)


def _screening(
    *, lower: float, upper: float, units: str = "MPa", quantity: str = "von_mises_peak"
) -> SurrogateScreening:
    document: dict[str, Any] = {
        "schema_version": SCREENING_SCHEMA,
        "screened_sha256": DIGEST,
        "model": {"name": "sfem-mesh-gnn", "version": "0.1.0", "sha256": DIGEST},
        "coverage": 0.9,
        "calibration": {
            "method": "cw_adaptive_split_conformal",
            "set_size": 2400,
            "set_sha256": DIGEST,
        },
        "domain": {"material_class": "linear_elastic"},
        "predictions": [
            {"quantity": quantity, "lower": lower, "upper": upper, "units": units}
        ],
        "assumptions": ["linear elastic, small displacement"],
        "limitations": ["screening estimate, not a strength certification"],
        "attestation": "screening_estimate",
    }
    return parse_surrogate_screening(document)


def test_an_interval_containing_the_closed_form_value_agrees() -> None:
    result = crosscheck_screening(_screening(lower=120.0, upper=140.0), BENDING)

    assert result.status == "interval_contains"
    assert result.closed_form_mpa == pytest.approx(BENDING_MPA)
    assert result.criterion is not None and "3-24" in result.criterion


def test_an_interval_excluding_the_closed_form_value_is_reported() -> None:
    """A miscalibrated surrogate is caught by the anchor, not hidden by it."""
    result = crosscheck_screening(_screening(lower=200.0, upper=260.0), BENDING)

    assert result.status == "interval_excludes"
    assert result.closed_form_mpa == pytest.approx(BENDING_MPA)


def test_the_closed_form_value_is_never_replaced_by_the_surrogate() -> None:
    agreeing = crosscheck_screening(_screening(lower=120.0, upper=140.0), BENDING)
    disagreeing = crosscheck_screening(_screening(lower=1.0, upper=2.0), BENDING)

    assert agreeing.closed_form_mpa == disagreeing.closed_form_mpa
    assert disagreeing.closed_form_mpa == pytest.approx(BENDING_MPA)


def test_relative_position_locates_the_anchor_within_the_interval() -> None:
    result = crosscheck_screening(_screening(lower=120.0, upper=140.0), BENDING)

    assert result.relative_position == pytest.approx((BENDING_MPA - 120.0) / 20.0)


@pytest.mark.parametrize(
    ("load_case", "expected"),
    [
        ({"kind": "axial", "force_n": 10000.0, "area_mm2": 100.0}, 100.0),
        (
            {"kind": "round_torsion", "torque_nmm": 100000.0, "diameter_mm": 20.0},
            sqrt(3.0) * 100000.0 * 10.0 / (pi * 20.0**4 / 32.0),
        ),
    ],
)
def test_the_elementary_cases_reduce_to_closed_form(
    load_case: dict[str, Any], expected: float
) -> None:
    result = crosscheck_screening(
        _screening(lower=expected * 0.9, upper=expected * 1.1), load_case
    )

    assert result.status == "interval_contains"
    assert result.closed_form_mpa == pytest.approx(expected)


def test_an_unsupported_load_case_is_not_applicable() -> None:
    """An anchor that had to be guessed at would not be an anchor."""
    result = crosscheck_screening(
        _screening(lower=120.0, upper=140.0),
        {"kind": "thermal_transient", "watts": 40.0},
    )

    assert result.status == "not_applicable"
    assert result.reason == "load_case_has_no_closed_form"
    assert result.closed_form_mpa is None


@pytest.mark.parametrize(
    "load_case",
    [
        {"kind": "round_cantilever_bending", "force_n": 1000.0, "length_mm": 100.0},
        {
            "kind": "round_cantilever_bending",
            "force_n": 1000.0,
            "length_mm": 100.0,
            "diameter_mm": -20.0,
        },
        {
            "kind": "axial",
            "force_n": "heavy",
            "area_mm2": 100.0,
        },
    ],
)
def test_a_malformed_load_case_declines_rather_than_raising(
    load_case: dict[str, Any]
) -> None:
    result = crosscheck_screening(_screening(lower=120.0, upper=140.0), load_case)

    assert result.status == "not_applicable"
    assert result.reason == "load_case_has_no_closed_form"


def test_a_quantity_the_surrogate_did_not_predict_is_not_applicable() -> None:
    result = crosscheck_screening(
        _screening(lower=1.0, upper=2.0, quantity="max_displacement"), BENDING
    )

    assert result.status == "not_applicable"
    assert result.reason == "quantity_not_predicted"


def test_units_that_cannot_be_compared_are_not_applicable() -> None:
    result = crosscheck_screening(
        _screening(lower=120.0, upper=140.0, units="ksi"), BENDING
    )

    assert result.status == "not_applicable"
    assert result.reason == "units_not_comparable"


def test_a_result_serializes_with_its_schema() -> None:
    result = crosscheck_screening(_screening(lower=120.0, upper=140.0), BENDING)

    serialized = result.as_dict()
    assert serialized["schema_version"] == CROSSCHECK_SCHEMA
    assert serialized["status"] == "interval_contains"
    assert serialized["interval"] == {"lower": 120.0, "upper": 140.0}
