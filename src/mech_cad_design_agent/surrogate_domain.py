from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .workspace_bootstrap import BootstrapFailure


DOMAIN_DECISION_SCHEMA = "SurrogateDomainDecision/v1"

DomainStatus = Literal["in_domain", "out_of_domain"]

_RANGE_KEYS = {"min", "max"}


def _invalid(message: str) -> BootstrapFailure:
    return BootstrapFailure("SURROGATE_DOMAIN_INVALID", message)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _plain(value: object) -> object:
    """Return JSON-serializable data.

    A declared envelope arrives frozen, so a bound reported back inside a
    violation would otherwise carry a mappingproxy into `canonical_json` and
    fail the atomic write at the point the refusal is recorded.
    """
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _violation(
    key: str, reason: str, expected: object, actual: object
) -> dict[str, Any]:
    return {
        "key": key,
        "reason": reason,
        "expected": _plain(expected),
        "actual": _plain(actual),
    }


def _check_range(
    key: str, bound: Mapping[str, object], actual: object
) -> dict[str, Any] | None:
    if not _RANGE_KEYS & bound.keys() or not bound.keys() <= _RANGE_KEYS:
        raise _invalid(f"domain bound {key!r} must declare min and/or max")
    measured = _number(actual)
    if measured is None:
        return _violation(key, "not_a_finite_number", dict(bound), actual)
    for edge, reason, fails in (
        ("min", "below_minimum", lambda a, b: a < b),
        ("max", "above_maximum", lambda a, b: a > b),
    ):
        if edge not in bound:
            continue
        limit = _number(bound[edge])
        if limit is None:
            raise _invalid(f"domain bound {key!r} {edge} must be a finite number")
        if fails(measured, limit):
            return _violation(key, reason, limit, measured)
    return None


@dataclass(frozen=True)
class DomainDecision:
    """Whether a query sits inside the envelope a surrogate was fitted over."""

    status: DomainStatus
    violations: tuple[Mapping[str, Any], ...]

    @property
    def in_domain(self) -> bool:
        return self.status == "in_domain"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DOMAIN_DECISION_SCHEMA,
            "status": self.status,
            "violations": [dict(item) for item in self.violations],
        }


def evaluate_domain(
    domain: Mapping[str, object], query: Mapping[str, object]
) -> DomainDecision:
    """Decide whether `query` lies inside the declared training envelope.

    The rule is the one `knowledge_matching.applicability_matches` already
    uses: strictly conjunctive, and a key the envelope declares but the query
    omits is a refusal rather than a pass. A surrogate asked about a quantity
    nobody measured has not been shown to be applicable; silence is not
    agreement.

    Every violated bound is reported, not just the first, because an engineer
    correcting an out-of-domain query needs the whole list rather than one
    bound at a time.
    """
    if not isinstance(domain, Mapping) or not domain:
        raise _invalid("domain envelope must be a nonempty object")
    if not isinstance(query, Mapping):
        raise _invalid("domain query must be an object")

    violations: list[dict[str, Any]] = []
    for key in sorted(domain, key=str):
        expected = domain[key]
        if key not in query:
            violations.append(_violation(str(key), "missing", expected, None))
            continue
        actual = query[key]
        if isinstance(expected, Mapping):
            failure = _check_range(str(key), expected, actual)
            if failure is not None:
                violations.append(failure)
        elif isinstance(expected, (list, tuple)):
            if not expected:
                raise _invalid(f"domain bound {key!r} must not be an empty set")
            if actual not in expected:
                violations.append(
                    _violation(str(key), "not_in_set", list(expected), actual)
                )
        elif actual != expected:
            violations.append(_violation(str(key), "not_equal", expected, actual))

    return DomainDecision(
        status="in_domain" if not violations else "out_of_domain",
        violations=tuple(violations),
    )


__all__ = [
    "DOMAIN_DECISION_SCHEMA",
    "DomainDecision",
    "DomainStatus",
    "evaluate_domain",
]
