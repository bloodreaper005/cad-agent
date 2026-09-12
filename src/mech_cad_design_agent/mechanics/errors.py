from __future__ import annotations

from typing import Any, Mapping


class MechanicsError(ValueError):
    """Reject a calculation request that violates the input contract.

    Mirrors GearSizingError deliberately. A design that fails its check is not
    an error and never raises: it returns a result carrying the factor of safety
    it achieved, because a failing margin is the answer, not a fault. This is
    raised only when the request itself cannot be evaluated, such as a negative
    diameter or a material with no ultimate strength.
    """

    def __init__(
        self, code: str, message: str, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = dict(detail or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "MechanicsError/v1",
            "status": "blocked",
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }


def require_positive(value: object, label: str) -> float:
    number = _require_finite(value, label)
    if number <= 0.0:
        raise MechanicsError(
            "input.non_positive", f"{label} must be positive", {label: value}
        )
    return number


def require_non_negative(value: object, label: str) -> float:
    number = _require_finite(value, label)
    if number < 0.0:
        raise MechanicsError(
            "input.negative", f"{label} must not be negative", {label: value}
        )
    return number


def _require_finite(value: object, label: str) -> float:
    from math import isfinite

    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise MechanicsError(
            "input.not_a_number", f"{label} must be a number", {label: repr(value)}
        ) from None
    if not isfinite(number):
        raise MechanicsError(
            "input.not_finite", f"{label} must be finite", {label: repr(value)}
        )
    return number


__all__ = ["MechanicsError", "require_positive", "require_non_negative"]
