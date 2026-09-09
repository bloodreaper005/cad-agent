from __future__ import annotations

from mech_cad_design_agent.mistake_learning import (
    CORRECTION_ORIGIN,
    build_attempt_entry,
    collect_failures,
    derive_lesson_candidates,
    failure_signature,
    summarize_corrections,
)


EVIDENCE = ["validation/model_validation.json", "validation/model_validation.png"]


def _check(
    check_id: str,
    *,
    status: str = "failed",
    validator: str = "freecad-model-validation",
    message: str = "the shape is not a solid",
    mandatory: bool = True,
) -> dict[str, object]:
    return {
        "id": check_id,
        "validator": validator,
        "status": status,
        "message": message,
        "mandatory": mandatory,
    }


def _attempt(
    attempt: int,
    *,
    validation_status: str,
    failures: list[dict[str, object]] | None = None,
    model_sha256: str | None = None,
) -> dict[str, object]:
    """Build a ledger entry the way the session does, through the report reader."""
    return build_attempt_entry(
        attempt=attempt,
        recorded_at=f"2026-09-08T00:0{attempt}:00+00:00",
        model_sha256=model_sha256 or (str(attempt) * 64),
        model_status="completed" if validation_status == "passed" else "needs_attention",
        validation_status=validation_status,
        warning=None if validation_status == "passed" else "mandatory check failed",
        failures=collect_failures({"checks": failures or []}),
    )


def test_failure_signature_ignores_wording() -> None:
    first = {"check_id": "solid", "validator": "v", "message": "one"}
    second = {"check_id": "solid", "validator": "v", "message": "two"}
    assert failure_signature(first) == failure_signature(second) == "v::solid"


def test_collect_failures_reads_every_non_passing_check() -> None:
    report = {
        "checks": [
            _check("solid"),
            _check("volume", status="passed"),
            _check("radius", status="warning", mandatory=False),
            "not-a-check",
        ]
    }
    failures = collect_failures(report)
    assert [failure["check_id"] for failure in failures] == ["solid", "radius"]
    assert failures[0]["mandatory"] is True
    assert failures[1]["mandatory"] is False


def test_collect_failures_tolerates_a_malformed_report() -> None:
    assert collect_failures(None) == []
    assert collect_failures({"checks": "not-a-list"}) == []


def test_a_corrected_mandatory_failure_becomes_one_candidate() -> None:
    ledger = [
        _attempt(1, validation_status="failed", failures=[_check("solid")]),
        _attempt(2, validation_status="failed", failures=[_check("solid")]),
        _attempt(3, validation_status="passed"),
    ]
    summary = summarize_corrections(ledger)
    assert summary["attempts"] == 3
    assert summary["failed_attempts"] == 2
    assert summary["outstanding"] == []
    assert len(summary["corrected"]) == 1
    corrected = summary["corrected"][0]
    assert corrected["signature"] == "freecad-model-validation::solid"
    assert corrected["occurrences"] == 2
    assert corrected["attempts"] == [1, 2]

    candidates = derive_lesson_candidates(
        ledger=ledger, evidence_relative_paths=EVIDENCE
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["origin"] == CORRECTION_ORIGIN
    assert candidate["correction_occurrences"] == 2
    assert "solid" in str(candidate["problem"])
    assert "the shape is not a solid" in str(candidate["problem"])
    assert candidate["evidence"] == EVIDENCE
    assert candidate["scope"] == "organization_general"
    for field in ("problem", "decision", "applicability", "prevention_action"):
        assert isinstance(candidate[field], str) and candidate[field].strip()


def test_a_design_that_never_failed_derives_nothing() -> None:
    ledger = [_attempt(1, validation_status="passed")]
    summary = summarize_corrections(ledger)
    assert summary["corrected"] == []
    assert summary["failed_attempts"] == 0
    assert derive_lesson_candidates(
        ledger=ledger, evidence_relative_paths=EVIDENCE
    ) == []


def test_an_empty_ledger_derives_nothing() -> None:
    for ledger in ([], None, "not-a-ledger"):
        assert summarize_corrections(ledger)["corrected"] == []
        assert derive_lesson_candidates(
            ledger=ledger, evidence_relative_paths=EVIDENCE
        ) == []


def test_a_still_failing_design_reports_outstanding_rather_than_corrected() -> None:
    ledger = [
        _attempt(1, validation_status="passed"),
        _attempt(2, validation_status="failed", failures=[_check("solid")]),
    ]
    summary = summarize_corrections(ledger)
    assert summary["corrected"] == []
    assert summary["resolved_by_model_sha256"] is None
    assert [record["signature"] for record in summary["outstanding"]] == [
        "freecad-model-validation::solid"
    ]
    assert derive_lesson_candidates(
        ledger=ledger, evidence_relative_paths=EVIDENCE
    ) == []


def test_advisory_failures_are_summarized_but_never_become_lessons() -> None:
    ledger = [
        _attempt(
            1,
            validation_status="failed",
            failures=[_check("radius", mandatory=False)],
        ),
        _attempt(2, validation_status="passed"),
    ]
    assert summarize_corrections(ledger)["corrected"] == []
    assert derive_lesson_candidates(
        ledger=ledger, evidence_relative_paths=EVIDENCE
    ) == []


def test_outstanding_reporting_includes_advisory_failures() -> None:
    ledger = [
        _attempt(
            1,
            validation_status="failed",
            failures=[_check("radius", mandatory=False), _check("solid")],
        )
    ]
    signatures = [
        record["signature"] for record in summarize_corrections(ledger)["outstanding"]
    ]
    assert signatures == [
        "freecad-model-validation::radius",
        "freecad-model-validation::solid",
    ]


def test_distinct_defects_derive_distinct_candidates_in_signature_order() -> None:
    ledger = [
        _attempt(
            1,
            validation_status="failed",
            failures=[
                _check("solid"),
                _check("clearance", validator="freecad-assembly"),
            ],
        ),
        _attempt(2, validation_status="passed"),
    ]
    candidates = derive_lesson_candidates(
        ledger=ledger, evidence_relative_paths=EVIDENCE
    )
    assert [candidate["correction_signature"] for candidate in candidates] == [
        "freecad-assembly::clearance",
        "freecad-model-validation::solid",
    ]


def test_derivation_is_deterministic_and_carries_no_timestamp() -> None:
    ledger = [
        _attempt(1, validation_status="failed", failures=[_check("solid")]),
        _attempt(2, validation_status="passed"),
    ]
    first = derive_lesson_candidates(ledger=ledger, evidence_relative_paths=EVIDENCE)
    second = derive_lesson_candidates(ledger=ledger, evidence_relative_paths=EVIDENCE)
    assert first == second
    assert "2026-09-08" not in str(first)


def test_derivation_requires_evidence() -> None:
    ledger = [
        _attempt(1, validation_status="failed", failures=[_check("solid")]),
        _attempt(2, validation_status="passed"),
    ]
    assert derive_lesson_candidates(ledger=ledger, evidence_relative_paths=[]) == []
    assert derive_lesson_candidates(ledger=ledger, evidence_relative_paths=["  "]) == []


def test_a_product_family_scopes_derived_candidates_when_supplied() -> None:
    ledger = [
        _attempt(1, validation_status="failed", failures=[_check("solid")]),
        _attempt(2, validation_status="passed"),
    ]
    candidate = derive_lesson_candidates(
        ledger=ledger,
        evidence_relative_paths=EVIDENCE,
        product_family_id="carriers",
    )[0]
    assert candidate["product_family_id"] == "carriers"


def test_a_defect_repeated_within_one_attempt_counts_once() -> None:
    ledger = [
        _attempt(
            1,
            validation_status="failed",
            failures=[_check("solid"), _check("solid", message="still not a solid")],
        ),
        _attempt(2, validation_status="passed"),
    ]
    corrected = summarize_corrections(ledger)["corrected"][0]
    assert corrected["occurrences"] == 1
    assert corrected["attempts"] == [1]


def test_search_terms_offer_both_the_identifier_and_its_wording() -> None:
    ledger = [
        _attempt(
            1,
            validation_status="failed",
            failures=[_check("rib-wall-thickness")],
        ),
        _attempt(2, validation_status="passed"),
    ]
    terms = derive_lesson_candidates(
        ledger=ledger, evidence_relative_paths=EVIDENCE
    )[0]["search_terms"]
    assert "rib-wall-thickness" in terms
    assert "rib wall thickness" in terms
    assert "freecad model validation" in terms
    assert len(terms) == len(set(terms))


def test_prevention_action_carries_the_reported_failure_detail() -> None:
    """A positional check id means nothing elsewhere; the message does."""
    ledger = [
        _attempt(
            1,
            validation_status="failed",
            failures=[
                _check("dimension.2", message="Rib1.bbox_x within declared tolerance 0.1")
            ],
        ),
        _attempt(2, validation_status="passed"),
    ]
    action = derive_lesson_candidates(
        ledger=ledger, evidence_relative_paths=EVIDENCE
    )[0]["prevention_action"]
    assert "dimension.2" in action
    assert "Rib1.bbox_x within declared tolerance 0.1" in action
    assert action.endswith(".")


def test_prevention_action_reads_cleanly_without_a_message() -> None:
    ledger = [
        _attempt(
            1, validation_status="failed", failures=[_check("solid", message="")]
        ),
        _attempt(2, validation_status="passed"),
    ]
    action = derive_lesson_candidates(
        ledger=ledger, evidence_relative_paths=EVIDENCE
    )[0]["prevention_action"]
    assert action.endswith("before reporting completion.")
    assert "::" not in action
