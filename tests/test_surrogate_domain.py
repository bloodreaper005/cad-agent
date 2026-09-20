from __future__ import annotations

from typing import Any

import pytest

from mech_cad_design_agent.models import canonical_json
from mech_cad_design_agent.surrogate_domain import (
    DOMAIN_DECISION_SCHEMA,
    evaluate_domain,
)
from mech_cad_design_agent.surrogate_screening import parse_surrogate_screening
from mech_cad_design_agent.workspace_bootstrap import BootstrapFailure


def _envelope(**overrides: Any) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "material_class": "linear_elastic",
        "load_case": ["point", "distributed"],
        "characteristic_length_mm": {"min": 5.0, "max": 400.0},
        "bounding_box_aspect_ratio": {"max": 20.0},
    }
    envelope.update(overrides)
    return envelope


def _query(**overrides: Any) -> dict[str, Any]:
    query: dict[str, Any] = {
        "material_class": "linear_elastic",
        "load_case": "point",
        "characteristic_length_mm": 120.0,
        "bounding_box_aspect_ratio": 4.5,
    }
    query.update(overrides)
    return query


def _reasons(violations: tuple[Any, ...]) -> dict[str, str]:
    return {str(item["key"]): str(item["reason"]) for item in violations}


def test_a_query_inside_every_bound_is_in_domain() -> None:
    decision = evaluate_domain(_envelope(), _query())

    assert decision.status == "in_domain"
    assert decision.in_domain is True
    assert decision.violations == ()


def test_a_missing_key_is_a_refusal_not_a_pass() -> None:
    """A surrogate asked about a quantity nobody measured is not applicable."""
    query = _query()
    del query["characteristic_length_mm"]

    decision = evaluate_domain(_envelope(), query)

    assert decision.status == "out_of_domain"
    assert _reasons(decision.violations) == {"characteristic_length_mm": "missing"}


def test_an_empty_query_refuses_every_declared_bound() -> None:
    decision = evaluate_domain(_envelope(), {})

    assert decision.status == "out_of_domain"
    assert set(_reasons(decision.violations)) == set(_envelope())


def test_a_value_below_the_minimum_names_the_bound() -> None:
    decision = evaluate_domain(
        _envelope(), _query(characteristic_length_mm=2.0)
    )

    assert decision.status == "out_of_domain"
    violation = decision.violations[0]
    assert violation["key"] == "characteristic_length_mm"
    assert violation["reason"] == "below_minimum"
    assert violation["expected"] == 5.0
    assert violation["actual"] == 2.0


def test_a_value_above_the_maximum_names_the_bound() -> None:
    decision = evaluate_domain(
        _envelope(), _query(bounding_box_aspect_ratio=64.0)
    )

    assert _reasons(decision.violations) == {
        "bounding_box_aspect_ratio": "above_maximum"
    }


def test_a_value_outside_the_declared_set_is_refused() -> None:
    decision = evaluate_domain(_envelope(), _query(load_case="thermal"))

    assert _reasons(decision.violations) == {"load_case": "not_in_set"}


def test_an_unequal_scalar_is_refused() -> None:
    decision = evaluate_domain(
        _envelope(), _query(material_class="elastoplastic")
    )

    assert _reasons(decision.violations) == {"material_class": "not_equal"}


def test_every_violated_bound_is_reported_not_only_the_first() -> None:
    """Correcting an out-of-domain query needs the whole list, not one at a time."""
    decision = evaluate_domain(
        _envelope(),
        _query(
            material_class="elastoplastic",
            load_case="thermal",
            characteristic_length_mm=1000.0,
        ),
    )

    assert _reasons(decision.violations) == {
        "material_class": "not_equal",
        "load_case": "not_in_set",
        "characteristic_length_mm": "above_maximum",
    }


@pytest.mark.parametrize("actual", ["120", None, True])
def test_a_non_numeric_value_against_a_range_is_refused(actual: object) -> None:
    decision = evaluate_domain(
        _envelope(), _query(characteristic_length_mm=actual)
    )

    assert _reasons(decision.violations) == {
        "characteristic_length_mm": "not_a_finite_number"
    }


def test_an_empty_envelope_is_rejected() -> None:
    with pytest.raises(BootstrapFailure, match="nonempty object"):
        evaluate_domain({}, _query())


def test_an_empty_allowed_set_is_rejected() -> None:
    with pytest.raises(BootstrapFailure, match="empty set"):
        evaluate_domain(_envelope(load_case=[]), _query())


def test_a_malformed_range_bound_is_rejected() -> None:
    with pytest.raises(BootstrapFailure, match="min and/or max"):
        evaluate_domain(
            _envelope(characteristic_length_mm={"lower": 5.0}), _query()
        )


def test_a_violation_reporting_a_frozen_bound_stays_serializable() -> None:
    """The envelope arrives frozen, and a refusal is written to design.json."""
    frozen = parse_surrogate_screening(
        {
            "schema_version": "SurrogateScreening/v1",
            "screened_sha256": "a" * 64,
            "model": {"name": "m", "version": "1", "sha256": "a" * 64},
            "coverage": 0.9,
            "calibration": {
                "method": "cw_adaptive_split_conformal",
                "set_size": 8,
                "set_sha256": "a" * 64,
            },
            "domain": {"characteristic_length_mm": {"min": 5.0, "max": 400.0}},
            "predictions": [
                {"quantity": "q", "lower": 1.0, "upper": 2.0, "units": "MPa"}
            ],
            "assumptions": ["a"],
            "limitations": ["l"],
            "attestation": "screening_estimate",
        }
    ).domain

    decision = evaluate_domain(frozen, {})

    assert decision.status == "out_of_domain"
    assert canonical_json(decision.as_dict())


def test_a_decision_serializes_with_its_schema() -> None:
    decision = evaluate_domain(_envelope(), _query(load_case="thermal"))

    result = decision.as_dict()
    assert result["schema_version"] == DOMAIN_DECISION_SCHEMA
    assert result["status"] == "out_of_domain"
    assert result["violations"][0]["key"] == "load_case"
