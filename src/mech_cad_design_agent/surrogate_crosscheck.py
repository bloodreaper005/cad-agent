from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .mechanics.errors import MechanicsError
from .mechanics.stress import (
    axial_stress,
    bending_stress,
    round_section_inertia_mm4,
    round_section_polar_inertia_mm4,
    torsional_shear_stress,
)
from .surrogate_screening import SurrogateScreening


CROSSCHECK_SCHEMA = "SurrogateCrosscheck/v1"

CrosscheckStatus = Literal[
    "interval_contains", "interval_excludes", "not_applicable"
]

DEFAULT_QUANTITY = "von_mises_peak"
_COMPARABLE_UNITS = "MPa"

_SQRT_3 = math.sqrt(3.0)


@dataclass(frozen=True)
class CrosscheckResult:
    """How a screening interval stands against the closed-form answer.

    The closed-form value is the anchor and is never adjusted to agree with the
    surrogate. When the interval excludes it, the surrogate is miscalibrated on
    this case and the result says so.
    """

    status: CrosscheckStatus
    quantity: str
    reason: str | None = None
    criterion: str | None = None
    closed_form_mpa: float | None = None
    lower: float | None = None
    upper: float | None = None
    relative_position: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CROSSCHECK_SCHEMA,
            "status": self.status,
            "quantity": self.quantity,
            "reason": self.reason,
            "criterion": self.criterion,
            "closed_form_mpa": self.closed_form_mpa,
            "interval": (
                None
                if self.lower is None or self.upper is None
                else {"lower": self.lower, "upper": self.upper}
            ),
            "relative_position": self.relative_position,
        }


def _not_applicable(quantity: str, reason: str) -> CrosscheckResult:
    return CrosscheckResult(
        status="not_applicable", quantity=quantity, reason=reason
    )


def _closed_form_mpa(load_case: Mapping[str, object]) -> tuple[float, str] | None:
    """Reduce a load case to a closed-form von Mises value, or decline.

    Only the elementary cases Shigley chapter 3 covers in closed form are
    handled. Anything else returns None, because an anchor that had to be
    guessed at would not be an anchor.
    """
    kind = load_case.get("kind")
    try:
        if kind == "axial":
            sigma = axial_stress(
                float(load_case["force_n"]),  # type: ignore[arg-type]
                float(load_case["area_mm2"]),  # type: ignore[arg-type]
            )
            return abs(sigma), "Shigley eq. 3-22 axial, von Mises = |sigma|"
        if kind == "round_cantilever_bending":
            diameter = float(load_case["diameter_mm"])  # type: ignore[arg-type]
            moment = float(load_case["force_n"]) * float(  # type: ignore[arg-type]
                load_case["length_mm"]  # type: ignore[arg-type]
            )
            sigma = bending_stress(
                moment, diameter / 2.0, round_section_inertia_mm4(diameter)
            )
            return abs(sigma), "Shigley eq. 3-24 bending, von Mises = |sigma|"
        if kind == "round_torsion":
            diameter = float(load_case["diameter_mm"])  # type: ignore[arg-type]
            tau = torsional_shear_stress(
                float(load_case["torque_nmm"]),  # type: ignore[arg-type]
                diameter / 2.0,
                round_section_polar_inertia_mm4(diameter),
            )
            return abs(tau) * _SQRT_3, "Shigley eq. 3-37 torsion, von Mises = sqrt(3) tau"
    except (KeyError, TypeError, ValueError, MechanicsError):
        return None
    return None


def crosscheck_screening(
    screening: SurrogateScreening,
    load_case: Mapping[str, object],
    *,
    quantity: str = DEFAULT_QUANTITY,
) -> CrosscheckResult:
    """Test whether the screening interval contains the closed-form answer.

    `not_applicable` is the honest default. A comparison is attempted only when
    the load case reduces to a case `mechanics` computes exactly and the
    prediction is stated in the same units; otherwise the result declines and
    names why, rather than forcing a number out of a case it does not cover.
    """
    if not isinstance(load_case, Mapping):
        raise ValueError("load case must be an object")

    prediction = next(
        (item for item in screening.predictions if item["quantity"] == quantity),
        None,
    )
    if prediction is None:
        return _not_applicable(quantity, "quantity_not_predicted")
    if prediction["units"] != _COMPARABLE_UNITS:
        return _not_applicable(quantity, "units_not_comparable")

    anchor = _closed_form_mpa(load_case)
    if anchor is None:
        return _not_applicable(quantity, "load_case_has_no_closed_form")
    closed_form, criterion = anchor

    lower = float(prediction["lower"])  # type: ignore[arg-type]
    upper = float(prediction["upper"])  # type: ignore[arg-type]
    width = upper - lower
    relative_position = (
        (closed_form - lower) / width if width > 0.0 else None
    )
    contains = lower <= closed_form <= upper

    return CrosscheckResult(
        status="interval_contains" if contains else "interval_excludes",
        quantity=quantity,
        reason=None,
        criterion=criterion,
        closed_form_mpa=closed_form,
        lower=lower,
        upper=upper,
        relative_position=relative_position,
    )


__all__ = [
    "CROSSCHECK_SCHEMA",
    "DEFAULT_QUANTITY",
    "CrosscheckResult",
    "CrosscheckStatus",
    "crosscheck_screening",
]
