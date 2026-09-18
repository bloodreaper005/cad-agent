from __future__ import annotations

import math
from typing import Any

import pytest

from mech_cad_design_agent.surrogate_screening import (
    SCREENING_SCHEMA,
    parse_surrogate_screening,
)
from mech_cad_design_agent.workspace_bootstrap import BootstrapFailure


DIGEST = "a" * 64


def _document(**overrides: Any) -> dict[str, Any]:
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
        "domain": {"material_class": "linear_elastic", "load_case": "point"},
        "predictions": [
            {
                "quantity": "von_mises_peak",
                "lower": 142.0,
                "upper": 198.0,
                "units": "MPa",
            }
        ],
        "assumptions": ["linear elastic, small displacement"],
        "limitations": ["screening estimate, not a strength certification"],
        "attestation": "screening_estimate",
    }
    document.update(overrides)
    return document


def _field(**overrides: Any) -> dict[str, Any]:
    field: dict[str, Any] = {
        "quantity": "von_mises",
        "mesh_sha256": DIGEST,
        "values_relative_path": "screening/von_mises.json",
        "interval_relative_path": "screening/von_mises_interval.json",
        "image_relative_path": "screening/von_mises.png",
        "image_sha256": DIGEST,
    }
    field.update(overrides)
    return field


def test_a_complete_document_round_trips() -> None:
    parsed = parse_surrogate_screening(_document())
    result = parsed.as_dict()

    assert result["schema_version"] == SCREENING_SCHEMA
    assert result["attestation"] == "screening_estimate"
    assert result["predictions"] == [
        {
            "quantity": "von_mises_peak",
            "lower": 142.0,
            "upper": 198.0,
            "units": "MPa",
        }
    ]
    assert result["fields"] == []


def test_a_point_estimate_is_refused() -> None:
    """A surrogate that cannot state an interval has nothing admissible to say."""
    document = _document(
        predictions=[{"quantity": "von_mises_peak", "value": 171.0, "units": "MPa"}]
    )

    with pytest.raises(BootstrapFailure, match="quantity, lower, upper, units"):
        parse_surrogate_screening(document)


def test_an_interval_without_a_calibration_record_is_refused() -> None:
    document = _document()
    del document["calibration"]

    with pytest.raises(BootstrapFailure, match="unexpected key set"):
        parse_surrogate_screening(document)


@pytest.mark.parametrize("bound", [math.nan, math.inf, -math.inf])
def test_a_non_finite_bound_is_refused(bound: float) -> None:
    document = _document(
        predictions=[
            {
                "quantity": "von_mises_peak",
                "lower": bound,
                "upper": 198.0,
                "units": "MPa",
            }
        ]
    )

    with pytest.raises(BootstrapFailure, match="must be finite"):
        parse_surrogate_screening(document)


def test_a_boolean_bound_is_not_a_number() -> None:
    document = _document(
        predictions=[
            {
                "quantity": "von_mises_peak",
                "lower": True,
                "upper": 198.0,
                "units": "MPa",
            }
        ]
    )

    with pytest.raises(BootstrapFailure, match="must be a number"):
        parse_surrogate_screening(document)


def test_an_inverted_interval_is_refused() -> None:
    document = _document(
        predictions=[
            {
                "quantity": "von_mises_peak",
                "lower": 198.0,
                "upper": 142.0,
                "units": "MPa",
            }
        ]
    )

    with pytest.raises(BootstrapFailure, match="must not exceed"):
        parse_surrogate_screening(document)


def test_an_empty_prediction_list_is_refused() -> None:
    """An empty list would satisfy every downstream reader vacuously."""
    with pytest.raises(BootstrapFailure, match="nonempty list"):
        parse_surrogate_screening(_document(predictions=[]))


def test_duplicate_prediction_quantities_are_refused() -> None:
    prediction = {
        "quantity": "von_mises_peak",
        "lower": 142.0,
        "upper": 198.0,
        "units": "MPa",
    }

    with pytest.raises(BootstrapFailure, match="must be unique"):
        parse_surrogate_screening(
            _document(predictions=[prediction, dict(prediction)])
        )


def test_an_empty_domain_envelope_is_refused() -> None:
    """An empty envelope claims to cover everything, which is the refusal case."""
    with pytest.raises(BootstrapFailure, match="nonempty object"):
        parse_surrogate_screening(_document(domain={}))


@pytest.mark.parametrize("coverage", [0.0, 1.0, -0.1, 1.5])
def test_coverage_must_lie_strictly_between_zero_and_one(coverage: float) -> None:
    with pytest.raises(BootstrapFailure, match="strictly between"):
        parse_surrogate_screening(_document(coverage=coverage))


@pytest.mark.parametrize("key", ["assumptions", "limitations"])
def test_assumptions_and_limitations_must_be_present(key: str) -> None:
    with pytest.raises(BootstrapFailure, match="nonempty list"):
        parse_surrogate_screening(_document(**{key: []}))


def test_attestation_must_say_it_is_a_screening_estimate() -> None:
    with pytest.raises(BootstrapFailure, match="attestation must be"):
        parse_surrogate_screening(_document(attestation="host_verified"))


def test_a_field_without_per_node_intervals_is_refused() -> None:
    """A rendered contour plot with no uncertainty behind it must not be storable."""
    field = _field()
    del field["interval_relative_path"]

    with pytest.raises(BootstrapFailure, match="field entry is incomplete"):
        parse_surrogate_screening(_document(fields=[field]))


def test_a_complete_field_is_accepted() -> None:
    parsed = parse_surrogate_screening(_document(fields=[_field()]))

    stored = parsed.as_dict()["fields"]
    assert len(stored) == 1
    assert stored[0]["interval_relative_path"] == "screening/von_mises_interval.json"


@pytest.mark.parametrize(
    "path",
    [
        "/absolute/von_mises.json",
        "../escape/von_mises.json",
        "screening\\von_mises.json",
        "C:/screening/von_mises.json",
    ],
)
def test_a_field_path_must_stay_relative_and_contained(path: str) -> None:
    with pytest.raises(BootstrapFailure, match="relative|safe relative"):
        parse_surrogate_screening(
            _document(fields=[_field(values_relative_path=path)])
        )


def test_an_incompatible_schema_version_is_refused() -> None:
    with pytest.raises(BootstrapFailure, match="schema_version is incompatible"):
        parse_surrogate_screening(_document(schema_version="SurrogateScreening/v2"))
