from __future__ import annotations

from typing import Any, Mapping, Sequence

from .knowledge_matching import normalize_search_term


LEDGER_SCHEMA = "DesignCorrectionLedger/v1"
SUMMARY_SCHEMA = "DesignMistakeSummary/v1"
CORRECTION_ORIGIN = "validation_correction"
AGENT_ORIGIN = "agent"

_FAILURE_FIELDS = ("check_id", "validator", "message", "mandatory")
_UNKNOWN = "unknown"


def _text(value: object, fallback: str = _UNKNOWN) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def failure_signature(failure: Mapping[str, object]) -> str:
    """Identify a repeatable validation defect independently of its wording."""
    return f"{_text(failure.get('validator'))}::{_text(failure.get('check_id'))}"


def collect_failures(report: object) -> list[dict[str, object]]:
    """Extract every failed check from a validation report, in report order."""
    if not isinstance(report, Mapping):
        return []
    checks = report.get("checks")
    if not isinstance(checks, list):
        return []
    failures: list[dict[str, object]] = []
    for check in checks:
        if not isinstance(check, Mapping):
            continue
        if check.get("status") == "passed":
            continue
        failures.append(
            {
                "check_id": _text(check.get("id")),
                "validator": _text(check.get("validator")),
                "message": _text(check.get("message"), ""),
                "mandatory": check.get("mandatory") is True,
            }
        )
    return failures


def build_attempt_entry(
    *,
    attempt: int,
    recorded_at: str,
    model_sha256: str,
    model_status: str,
    validation_status: str,
    warning: str | None,
    failures: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Shape one append-only correction-ledger entry."""
    normalized: list[dict[str, object]] = []
    for failure in failures:
        normalized.append(
            {field: failure.get(field) for field in _FAILURE_FIELDS}
        )
    return {
        "attempt": attempt,
        "recorded_at": recorded_at,
        "model_sha256": model_sha256,
        "model_status": model_status,
        "validation_status": validation_status,
        "warning": warning,
        "failures": normalized,
    }


def _normalize_ledger(ledger: object) -> list[dict[str, object]]:
    if not isinstance(ledger, list):
        return []
    entries: list[dict[str, object]] = []
    for raw in ledger:
        if not isinstance(raw, Mapping):
            continue
        failures = raw.get("failures")
        entries.append(
            {
                "attempt": raw.get("attempt"),
                "recorded_at": raw.get("recorded_at"),
                "model_sha256": raw.get("model_sha256"),
                "model_status": raw.get("model_status"),
                "validation_status": raw.get("validation_status"),
                "warning": raw.get("warning"),
                "failures": [
                    dict(item)
                    for item in (failures if isinstance(failures, list) else [])
                    if isinstance(item, Mapping)
                ],
            }
        )
    return entries


def _group(
    entries: Sequence[Mapping[str, object]], *, mandatory_only: bool
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for entry in entries:
        seen: set[str] = set()
        for failure in entry["failures"]:  # type: ignore[index]
            if mandatory_only and failure.get("mandatory") is not True:
                continue
            signature = failure_signature(failure)
            record = grouped.setdefault(
                signature,
                {
                    "signature": signature,
                    "check_id": _text(failure.get("check_id")),
                    "validator": _text(failure.get("validator")),
                    "mandatory": failure.get("mandatory") is True,
                    "occurrences": 0,
                    "attempts": [],
                    "first_message": _text(failure.get("message"), ""),
                },
            )
            if signature in seen:
                continue
            seen.add(signature)
            record["occurrences"] += 1
            record["attempts"].append(entry.get("attempt"))
    return grouped


def _sorted_records(
    grouped: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, object]]:
    return [
        dict(grouped[signature])
        for signature in sorted(grouped, key=str)
    ]


def summarize_corrections(ledger: object) -> dict[str, object]:
    """Report which validation defects were corrected and which still stand.

    Corrections are recognized only while the newest attempt passes, so a design
    that currently fails reports outstanding defects rather than claiming a fix.
    """
    entries = _normalize_ledger(ledger)
    newest = entries[-1] if entries else None
    passing = newest is not None and newest.get("validation_status") == "passed"
    corrected = (
        _sorted_records(_group(entries[:-1], mandatory_only=True)) if passing else []
    )
    outstanding = (
        []
        if newest is None or passing
        else _sorted_records(_group([newest], mandatory_only=False))
    )
    return {
        "schema_version": SUMMARY_SCHEMA,
        "attempts": len(entries),
        "failed_attempts": sum(
            1 for entry in entries if entry.get("validation_status") != "passed"
        ),
        "resolved_by_model_sha256": (
            newest.get("model_sha256") if passing and newest is not None else None
        ),
        "corrected": corrected,
        "outstanding": outstanding,
    }


def _spaced(value: str) -> str:
    """Read an identifier as the words it is made of: 'rib-wall' -> 'rib wall'."""
    return " ".join(
        "".join(character if character.isalnum() else " " for character in value).split()
    )


def _search_terms(record: Mapping[str, object]) -> list[str]:
    """Offer both the identifier and its wording, so a plain query can match."""
    terms: list[str] = []

    def add(value: str) -> None:
        normalized = normalize_search_term(value)
        if normalized and normalized != _UNKNOWN and normalized not in terms:
            terms.append(normalized)

    check_id = str(record.get("check_id"))
    validator = str(record.get("validator"))
    add(check_id)
    add(_spaced(check_id))
    add(validator)
    add(_spaced(validator))
    add(f"{validator} {check_id}")
    return terms


def derive_lesson_candidates(
    *,
    ledger: object,
    evidence_relative_paths: Sequence[str],
    product_family_id: object = None,
) -> list[dict[str, object]]:
    """Turn verified validation corrections into deterministic lesson candidates.

    A candidate is derived only for a mandatory check that failed on an earlier
    attempt and passed in the accepted model, so every derived lesson records a
    mistake this design actually made and actually fixed.
    """
    evidence = [
        value
        for value in evidence_relative_paths
        if isinstance(value, str) and value.strip()
    ]
    if not evidence:
        return []
    corrected = summarize_corrections(ledger)["corrected"]
    if not isinstance(corrected, list):
        return []
    candidates: list[dict[str, object]] = []
    for record in corrected:
        check_id = str(record["check_id"])
        validator = str(record["validator"])
        occurrences = int(record["occurrences"])
        attempt_text = "attempt" if occurrences == 1 else "attempts"
        detail = str(record.get("first_message") or "").strip()
        problem = (
            f"Mandatory validation check '{check_id}' failed under '{validator}' "
            f"on {occurrences} {attempt_text} before the accepted model passed it."
        )
        if detail:
            problem = f"{problem} The first reported failure was: {detail}"
        candidate = {
            "problem": problem,
            "decision": (
                f"The model was corrected and revalidated until '{check_id}' "
                f"passed under '{validator}' on the confirmed model."
            ),
            "evidence": list(dict.fromkeys(evidence)),
            "applicability": (
                f"Designs validated by '{validator}' that must satisfy the "
                f"'{check_id}' check."
            ),
            # A validator may name its checks positionally, so the identifier
            # alone can be meaningless in another design. The reported message
            # carries what was actually being asserted, so keep it in the
            # action a reader has to follow.
            "prevention_action": (
                f"While modeling, verify what '{check_id}' asserts under "
                f"'{validator}' before reporting completion"
                + (f": {detail}" if detail else "")
                + "."
            ),
            "search_terms": _search_terms(record),
            "scope": "organization_general",
            "origin": CORRECTION_ORIGIN,
            "correction_signature": str(record["signature"]),
            "correction_occurrences": occurrences,
        }
        if isinstance(product_family_id, str) and product_family_id.strip():
            candidate["product_family_id"] = product_family_id.strip()
        candidates.append(candidate)
    return candidates


__all__ = [
    "AGENT_ORIGIN",
    "CORRECTION_ORIGIN",
    "LEDGER_SCHEMA",
    "SUMMARY_SCHEMA",
    "build_attempt_entry",
    "collect_failures",
    "derive_lesson_candidates",
    "failure_signature",
    "summarize_corrections",
]
