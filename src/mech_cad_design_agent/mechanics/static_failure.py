"""Failure theories for static loading.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapter 5.

Every function returns a factor of safety. A factor below one is a prediction of
failure and is returned, never raised: the margin is the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf

from .errors import MechanicsError, require_positive
from .stress import StressState, principal_stresses, von_mises_stress


DUCTILE_THEORIES = ("distortion_energy", "maximum_shear", "ductile_coulomb_mohr")
BRITTLE_THEORIES = ("maximum_normal", "brittle_coulomb_mohr", "modified_mohr")


@dataclass(frozen=True)
class StaticResult:
    theory: str
    factor_of_safety: float
    effective_stress_mpa: float
    principal_stresses_mpa: tuple[float, float, float]

    @property
    def safe(self) -> bool:
        return self.factor_of_safety >= 1.0


def _ratio(strength: float, effective: float) -> float:
    """Guard the division so a zero stress state reports infinite margin."""
    if effective <= 0.0:
        return inf
    return strength / effective


def distortion_energy(state: StressState, yield_strength_mpa: float) -> StaticResult:
    """Von Mises / distortion-energy theory, Shigley eq. 5-19.

    The preferred theory for ductile materials with equal tensile and
    compressive yield strengths.
    """
    strength = require_positive(yield_strength_mpa, "yield_strength_mpa")
    effective = von_mises_stress(state)
    return StaticResult(
        theory="distortion_energy",
        factor_of_safety=_ratio(strength, effective),
        effective_stress_mpa=effective,
        principal_stresses_mpa=principal_stresses(state),
    )


def maximum_shear(state: StressState, yield_strength_mpa: float) -> StaticResult:
    """Maximum-shear-stress (Tresca) theory, Shigley eq. 5-3.

    Always at or below the distortion-energy prediction, so it is the
    conservative choice of the two.
    """
    strength = require_positive(yield_strength_mpa, "yield_strength_mpa")
    one, two, three = principal_stresses(state)
    effective = one - three
    return StaticResult(
        theory="maximum_shear",
        factor_of_safety=_ratio(strength, effective),
        effective_stress_mpa=effective,
        principal_stresses_mpa=(one, two, three),
    )


def ductile_coulomb_mohr(
    state: StressState,
    tensile_yield_mpa: float,
    compressive_yield_mpa: float,
) -> StaticResult:
    """Ductile Coulomb-Mohr theory, Shigley eq. 5-26.

    For ductile materials whose compressive yield strength differs from their
    tensile yield strength. Reduces to maximum-shear when the two are equal.
    """
    tensile = require_positive(tensile_yield_mpa, "tensile_yield_mpa")
    compressive = require_positive(compressive_yield_mpa, "compressive_yield_mpa")
    one, two, three = principal_stresses(state)
    inverse = one / tensile - three / compressive
    factor = inf if inverse <= 0.0 else 1.0 / inverse
    return StaticResult(
        theory="ductile_coulomb_mohr",
        factor_of_safety=factor,
        effective_stress_mpa=(0.0 if inverse <= 0.0 else tensile * inverse),
        principal_stresses_mpa=(one, two, three),
    )


def maximum_normal(
    state: StressState,
    ultimate_tensile_mpa: float,
    ultimate_compressive_mpa: float,
) -> StaticResult:
    """Maximum-normal-stress theory, Shigley eq. 5-28.

    Brittle materials only, and known to be unreliable in the fourth quadrant
    where one principal stress is tensile and the other compressive. Shigley
    recommends the modified-Mohr theory there instead.
    """
    tensile = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    compressive = require_positive(ultimate_compressive_mpa, "ultimate_compressive_mpa")
    one, two, three = principal_stresses(state)
    candidates = []
    if one > 0.0:
        candidates.append(tensile / one)
    if three < 0.0:
        candidates.append(compressive / -three)
    factor = min(candidates) if candidates else inf
    return StaticResult(
        theory="maximum_normal",
        factor_of_safety=factor,
        effective_stress_mpa=max(one, -three, 0.0),
        principal_stresses_mpa=(one, two, three),
    )


def brittle_coulomb_mohr(
    state: StressState,
    ultimate_tensile_mpa: float,
    ultimate_compressive_mpa: float,
) -> StaticResult:
    """Brittle Coulomb-Mohr theory, Shigley eq. 5-31."""
    tensile = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    compressive = require_positive(ultimate_compressive_mpa, "ultimate_compressive_mpa")
    one, two, three = principal_stresses(state)
    if one >= 0.0 and three >= 0.0:
        factor = _ratio(tensile, one)
    elif one <= 0.0 and three <= 0.0:
        factor = _ratio(compressive, -three)
    else:
        inverse = one / tensile - three / compressive
        factor = inf if inverse <= 0.0 else 1.0 / inverse
    return StaticResult(
        theory="brittle_coulomb_mohr",
        factor_of_safety=factor,
        effective_stress_mpa=max(one, -three, 0.0),
        principal_stresses_mpa=(one, two, three),
    )


def modified_mohr(
    state: StressState,
    ultimate_tensile_mpa: float,
    ultimate_compressive_mpa: float,
) -> StaticResult:
    """Modified-Mohr theory, Shigley eq. 5-32.

    The theory Shigley recommends for brittle materials, because it follows the
    fourth-quadrant test data that both maximum-normal and Coulomb-Mohr miss.
    """
    tensile = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    compressive = require_positive(ultimate_compressive_mpa, "ultimate_compressive_mpa")
    one, two, three = principal_stresses(state)
    if one >= 0.0 and three >= 0.0:
        factor = _ratio(tensile, one)
    elif one <= 0.0 and three <= 0.0:
        factor = _ratio(compressive, -three)
    elif one >= 0.0 >= three and abs(three) <= one:
        # Fourth quadrant, |sigma_3 / sigma_1| <= 1: maximum-normal governs.
        factor = _ratio(tensile, one)
    else:
        inverse = (compressive - tensile) * one / (compressive * tensile) - three / compressive
        factor = inf if inverse <= 0.0 else 1.0 / inverse
    return StaticResult(
        theory="modified_mohr",
        factor_of_safety=factor,
        effective_stress_mpa=max(one, -three, 0.0),
        principal_stresses_mpa=(one, two, three),
    )


def evaluate(
    state: StressState,
    *,
    theory: str,
    yield_strength_mpa: float | None = None,
    compressive_yield_mpa: float | None = None,
    ultimate_tensile_mpa: float | None = None,
    ultimate_compressive_mpa: float | None = None,
) -> StaticResult:
    """Dispatch to one named theory, checking it has the strengths it needs."""
    if theory == "distortion_energy":
        return distortion_energy(state, _need(yield_strength_mpa, "yield_strength_mpa"))
    if theory == "maximum_shear":
        return maximum_shear(state, _need(yield_strength_mpa, "yield_strength_mpa"))
    if theory == "ductile_coulomb_mohr":
        return ductile_coulomb_mohr(
            state,
            _need(yield_strength_mpa, "yield_strength_mpa"),
            _need(compressive_yield_mpa, "compressive_yield_mpa"),
        )
    if theory in BRITTLE_THEORIES:
        tensile = _need(ultimate_tensile_mpa, "ultimate_tensile_mpa")
        compressive = _need(ultimate_compressive_mpa, "ultimate_compressive_mpa")
        if theory == "maximum_normal":
            return maximum_normal(state, tensile, compressive)
        if theory == "brittle_coulomb_mohr":
            return brittle_coulomb_mohr(state, tensile, compressive)
        return modified_mohr(state, tensile, compressive)
    raise MechanicsError(
        "input.unknown_theory",
        f"unknown static failure theory {theory!r}",
        {"supported": list(DUCTILE_THEORIES + BRITTLE_THEORIES)},
    )


def _need(value: float | None, label: str) -> float:
    if value is None:
        raise MechanicsError(
            "input.incomplete", f"{label} is required for this theory", {label: None}
        )
    return value


__all__ = [
    "BRITTLE_THEORIES",
    "DUCTILE_THEORIES",
    "StaticResult",
    "brittle_coulomb_mohr",
    "distortion_energy",
    "ductile_coulomb_mohr",
    "evaluate",
    "maximum_normal",
    "maximum_shear",
    "modified_mohr",
]
