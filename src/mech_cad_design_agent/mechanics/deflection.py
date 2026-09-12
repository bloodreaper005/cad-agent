"""Deflection and stiffness.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapter 4.

Beam cases follow the sign convention of table A-9: downward load positive,
downward deflection returned positive. A deflection is reported as a magnitude
in millimetres so that a stiffness check reads the same way a stress check does.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import pi, sqrt

from .errors import MechanicsError, require_non_negative, require_positive


# Table 4-2, end-condition constant C for P_cr = C pi^2 E I / l^2. The
# theoretical fixed-free and fixed-pinned values are given alongside the
# conservative values the text recommends for real columns, because a built-in
# end is never as rigid in practice as it is on paper.
END_CONDITIONS: dict[str, float] = {
    "pinned_pinned": 1.0,
    "fixed_free": 0.25,
    "fixed_pinned": 2.0,
    "fixed_fixed": 4.0,
}
CONSERVATIVE_END_CONDITIONS: dict[str, float] = {
    "pinned_pinned": 1.0,
    "fixed_free": 0.25,
    "fixed_pinned": 1.2,
    "fixed_fixed": 1.2,
}


def spring_rate_n_per_mm(force_n: float, deflection_mm: float) -> float:
    """Spring rate k = F / y, Shigley eq. 4-1."""
    return force_n / require_positive(deflection_mm, "deflection_mm")


def axial_stiffness_n_per_mm(
    area_mm2: float, length_mm: float, elastic_modulus_mpa: float
) -> float:
    """Stiffness of a bar in tension or compression, Shigley eq. 4-4."""
    return (
        require_positive(area_mm2, "area_mm2")
        * require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
        / require_positive(length_mm, "length_mm")
    )


def torsional_stiffness_nmm_per_rad(
    polar_inertia_mm4: float, length_mm: float, shear_modulus_mpa: float
) -> float:
    """Torsional stiffness k = G J / l, Shigley eq. 4-5."""
    return (
        require_positive(polar_inertia_mm4, "polar_inertia_mm4")
        * require_positive(shear_modulus_mpa, "shear_modulus_mpa")
        / require_positive(length_mm, "length_mm")
    )


def series_stiffness(*rates: float) -> float:
    """Combine springs in series, Shigley eq. 4-6."""
    if not rates:
        raise MechanicsError("input.incomplete", "at least one rate is required", {})
    total = 0.0
    for index, rate in enumerate(rates):
        total += 1.0 / require_positive(rate, f"rate[{index}]")
    return 1.0 / total


def parallel_stiffness(*rates: float) -> float:
    """Combine springs in parallel, Shigley eq. 4-7."""
    if not rates:
        raise MechanicsError("input.incomplete", "at least one rate is required", {})
    return sum(require_positive(rate, f"rate[{index}]") for index, rate in enumerate(rates))


# --- Standard beam cases, table A-9 ----------------------------------------


def cantilever_end_load_deflection_mm(
    *, force_n: float, length_mm: float, elastic_modulus_mpa: float, inertia_mm4: float
) -> float:
    """Cantilever with a concentrated end load, table A-9 case 1: F l^3 / (3 E I)."""
    length = require_positive(length_mm, "length_mm")
    return abs(force_n) * length**3 / (
        3.0
        * require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
        * require_positive(inertia_mm4, "inertia_mm4")
    )


def cantilever_uniform_load_deflection_mm(
    *,
    load_n_per_mm: float,
    length_mm: float,
    elastic_modulus_mpa: float,
    inertia_mm4: float,
) -> float:
    """Cantilever under a uniform load, table A-9 case 2: w l^4 / (8 E I)."""
    length = require_positive(length_mm, "length_mm")
    return abs(load_n_per_mm) * length**4 / (
        8.0
        * require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
        * require_positive(inertia_mm4, "inertia_mm4")
    )


def simply_supported_center_load_deflection_mm(
    *, force_n: float, length_mm: float, elastic_modulus_mpa: float, inertia_mm4: float
) -> float:
    """Simple beam, central load, table A-9 case 5: F l^3 / (48 E I)."""
    length = require_positive(length_mm, "length_mm")
    return abs(force_n) * length**3 / (
        48.0
        * require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
        * require_positive(inertia_mm4, "inertia_mm4")
    )


def simply_supported_offset_load_deflection_mm(
    *,
    force_n: float,
    length_mm: float,
    distance_from_left_mm: float,
    elastic_modulus_mpa: float,
    inertia_mm4: float,
) -> float:
    """Simple beam, intermediate load, maximum deflection from table A-9 case 6.

    The maximum does not occur under the load. It is located in the longer
    segment at sqrt((l^2 - b^2)/3) from the near support, which is what this
    returns rather than the deflection at the load point.
    """
    length = require_positive(length_mm, "length_mm")
    a = require_positive(distance_from_left_mm, "distance_from_left_mm")
    if a >= length:
        raise MechanicsError(
            "input.out_of_range",
            "the load must lie between the supports",
            {"length_mm": length, "distance_from_left_mm": a},
        )
    b = length - a
    modulus = require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
    inertia = require_positive(inertia_mm4, "inertia_mm4")
    force = abs(force_n)
    # Work in the longer segment, where the maximum always falls.
    far = max(a, b)
    near = min(a, b)
    return (
        force * near * (length**2 - near**2) ** 1.5
        / (9.0 * sqrt(3.0) * modulus * inertia * length)
    )


def simply_supported_uniform_load_deflection_mm(
    *,
    load_n_per_mm: float,
    length_mm: float,
    elastic_modulus_mpa: float,
    inertia_mm4: float,
) -> float:
    """Simple beam under a uniform load, table A-9 case 7: 5 w l^4 / (384 E I)."""
    length = require_positive(length_mm, "length_mm")
    return 5.0 * abs(load_n_per_mm) * length**4 / (
        384.0
        * require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
        * require_positive(inertia_mm4, "inertia_mm4")
    )


# --- Columns ---------------------------------------------------------------


def radius_of_gyration_mm(inertia_mm4: float, area_mm2: float) -> float:
    """k = sqrt(I / A), Shigley eq. 4-43."""
    return sqrt(
        require_positive(inertia_mm4, "inertia_mm4")
        / require_positive(area_mm2, "area_mm2")
    )


def slenderness_ratio(length_mm: float, radius_of_gyration: float) -> float:
    return require_positive(length_mm, "length_mm") / require_positive(
        radius_of_gyration, "radius_of_gyration"
    )


def transition_slenderness(
    yield_strength_mpa: float,
    elastic_modulus_mpa: float,
    *,
    end_condition: str = "pinned_pinned",
    conservative: bool = True,
) -> float:
    """Slenderness at which Euler and Johnson meet, Shigley eq. 4-48.

    Below this the column is intermediate and Johnson governs; above it Euler
    governs. The two curves are tangent here, so a column rated by the wrong one
    is not merely inaccurate, it is unconservative on the Euler side.
    """
    constant = _end_condition_constant(end_condition, conservative)
    return sqrt(
        2.0
        * pi**2
        * constant
        * require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
        / require_positive(yield_strength_mpa, "yield_strength_mpa")
    )


def euler_critical_load_n(
    *,
    area_mm2: float,
    slenderness: float,
    elastic_modulus_mpa: float,
    end_condition: str = "pinned_pinned",
    conservative: bool = True,
) -> float:
    """Euler buckling load, Shigley eq. 4-44."""
    constant = _end_condition_constant(end_condition, conservative)
    return (
        constant
        * pi**2
        * require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
        * require_positive(area_mm2, "area_mm2")
        / require_positive(slenderness, "slenderness") ** 2
    )


def johnson_critical_load_n(
    *,
    area_mm2: float,
    slenderness: float,
    yield_strength_mpa: float,
    elastic_modulus_mpa: float,
    end_condition: str = "pinned_pinned",
    conservative: bool = True,
) -> float:
    """Johnson parabolic formula for intermediate columns, Shigley eq. 4-46."""
    constant = _end_condition_constant(end_condition, conservative)
    yield_strength = require_positive(yield_strength_mpa, "yield_strength_mpa")
    modulus = require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
    ratio = require_positive(slenderness, "slenderness")
    stress = yield_strength - (1.0 / (constant * modulus)) * (
        yield_strength * ratio / (2.0 * pi)
    ) ** 2
    return max(stress, 0.0) * require_positive(area_mm2, "area_mm2")


@dataclass(frozen=True)
class ColumnResult:
    formula: str
    critical_load_n: float
    slenderness: float
    transition_slenderness: float
    factor_of_safety: float

    @property
    def safe(self) -> bool:
        return self.factor_of_safety >= 1.0


def evaluate_column(
    *,
    applied_load_n: float,
    area_mm2: float,
    inertia_mm4: float,
    length_mm: float,
    yield_strength_mpa: float,
    elastic_modulus_mpa: float,
    end_condition: str = "pinned_pinned",
    conservative: bool = True,
) -> ColumnResult:
    """Rate a column, choosing Euler or Johnson by its slenderness.

    Selecting the formula is not a refinement. Applying Euler to an
    intermediate column overpredicts its capacity, which is the direction that
    matters.
    """
    radius = radius_of_gyration_mm(inertia_mm4, area_mm2)
    ratio = slenderness_ratio(length_mm, radius)
    transition = transition_slenderness(
        yield_strength_mpa,
        elastic_modulus_mpa,
        end_condition=end_condition,
        conservative=conservative,
    )
    if ratio > transition:
        formula = "euler"
        critical = euler_critical_load_n(
            area_mm2=area_mm2,
            slenderness=ratio,
            elastic_modulus_mpa=elastic_modulus_mpa,
            end_condition=end_condition,
            conservative=conservative,
        )
    else:
        formula = "johnson"
        critical = johnson_critical_load_n(
            area_mm2=area_mm2,
            slenderness=ratio,
            yield_strength_mpa=yield_strength_mpa,
            elastic_modulus_mpa=elastic_modulus_mpa,
            end_condition=end_condition,
            conservative=conservative,
        )
    load = require_non_negative(applied_load_n, "applied_load_n")
    factor = float("inf") if load <= 0.0 else critical / load
    return ColumnResult(
        formula=formula,
        critical_load_n=critical,
        slenderness=ratio,
        transition_slenderness=transition,
        factor_of_safety=factor,
    )


def _end_condition_constant(end_condition: str, conservative: bool) -> float:
    table = CONSERVATIVE_END_CONDITIONS if conservative else END_CONDITIONS
    try:
        return table[end_condition]
    except KeyError:
        raise MechanicsError(
            "input.unknown_end_condition",
            f"unknown column end condition {end_condition!r}",
            {"supported": sorted(table)},
        ) from None


__all__ = [
    "CONSERVATIVE_END_CONDITIONS",
    "END_CONDITIONS",
    "ColumnResult",
    "axial_stiffness_n_per_mm",
    "cantilever_end_load_deflection_mm",
    "cantilever_uniform_load_deflection_mm",
    "euler_critical_load_n",
    "evaluate_column",
    "johnson_critical_load_n",
    "parallel_stiffness",
    "radius_of_gyration_mm",
    "series_stiffness",
    "simply_supported_center_load_deflection_mm",
    "simply_supported_offset_load_deflection_mm",
    "simply_supported_uniform_load_deflection_mm",
    "slenderness_ratio",
    "spring_rate_n_per_mm",
    "torsional_stiffness_nmm_per_rad",
    "transition_slenderness",
]
