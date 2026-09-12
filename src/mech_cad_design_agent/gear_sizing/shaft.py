from __future__ import annotations

from dataclasses import dataclass
from math import pi, sqrt

from ..mechanics.fatigue import (
    RELIABILITY_FACTORS,
    endurance_limit_prime_mpa,
    size_factor,
    surface_factor,
)
from .errors import GearSizingError
from .materials import ShaftMaterial
from .standards import ISO15_BORE_SERIES, ceil_to_step, select_from_series


# ASME B106.1M fatigue factors by duty, midpoints of the published ranges.
SHOCK_FACTORS: dict[str, tuple[float, float]] = {
    "uniform": (1.5, 1.0),
    "light": (1.75, 1.25),
    "moderate": (2.0, 1.5),
    "heavy": (2.5, 2.0),
}

# Fatigue stress-concentration factors at the gear seat.
STRESS_CONCENTRATIONS: dict[str, tuple[float, float]] = {
    "profile_keyway": (2.14, 3.00),
    "sled_runner_keyway": (1.60, 1.60),
    "shoulder_fillet": (1.70, 1.50),
}

# Reliability knock-down on the endurance limit, Shigley table 6-5. The table
# itself now lives in mechanics.fatigue, which holds one copy for the whole
# project; this restriction records which of its entries a shaft request may
# select, since the sizing API resolves to the nearest tabulated reliability
# rather than refusing an untabulated one.
_RELIABILITY_ENDURANCE: dict[float, float] = {
    value: RELIABILITY_FACTORS[value] for value in (0.50, 0.90, 0.95, 0.99, 0.999)
}

_MAX_ITERATIONS = 100
_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class ShaftLoads:
    span_mm: float
    gear_position_mm: float
    reaction_a_n: float
    reaction_b_n: float
    bending_moment_nmm: float
    torque_nmm: float


@dataclass(frozen=True, slots=True)
class ShaftResult:
    loads: ShaftLoads
    allowable_shear_mpa: float
    endurance_limit_mpa: float
    asme_diameter_mm: float
    goodman_diameter_mm: float
    required_diameter_mm: float
    selected_diameter_mm: float
    max_bore_from_root_mm: float


def shaft_loads(
    *,
    tangential_force_n: float,
    radial_force_n: float,
    torque_nmm: float,
    span_mm: float,
    position_ratio: float,
) -> ShaftLoads:
    """Straddle-mounted shaft with the gear between two bearings.

    A spur mesh carries no axial force, so there is no thrust couple and the
    radial-plane moment diagram stays continuous across the gear.
    """
    if not 0.0 < position_ratio < 1.0:
        raise GearSizingError(
            "bearing.layout",
            "gear position ratio must lie strictly between the bearings",
            {"position_ratio": position_ratio},
        )
    position = position_ratio * span_mm
    far = span_mm - position

    reaction_a = sqrt(
        (tangential_force_n * far / span_mm) ** 2
        + (radial_force_n * far / span_mm) ** 2
    )
    reaction_b = sqrt(
        (tangential_force_n * position / span_mm) ** 2
        + (radial_force_n * position / span_mm) ** 2
    )
    lever = position * far / span_mm
    moment = sqrt((tangential_force_n * lever) ** 2 + (radial_force_n * lever) ** 2)

    return ShaftLoads(
        span_mm=span_mm,
        gear_position_mm=position,
        reaction_a_n=reaction_a,
        reaction_b_n=reaction_b,
        bending_moment_nmm=moment,
        torque_nmm=torque_nmm,
    )


def allowable_shear_mpa(material: ShaftMaterial, keyway_present: bool) -> float:
    base = min(0.30 * material.yield_strength_mpa, 0.18 * material.tensile_strength_mpa)
    return base * (0.75 if keyway_present else 1.0)


def asme_diameter_mm(
    *, moment_nmm: float, torque_nmm: float, shear_mpa: float, duty: str
) -> float:
    """ASME B106.1M maximum-shear relation for combined bending and torsion."""
    try:
        bending_factor, torsion_factor = SHOCK_FACTORS[duty]
    except KeyError:
        raise GearSizingError(
            "input.unknown_option",
            f"unknown duty {duty!r}",
            {"valid": sorted(SHOCK_FACTORS)},
        ) from None
    combined = sqrt(
        (bending_factor * moment_nmm) ** 2 + (torsion_factor * torque_nmm) ** 2
    )
    return (16.0 / (pi * shear_mpa) * combined) ** (1.0 / 3.0)


def endurance_limit_mpa(
    *, material: ShaftMaterial, diameter_mm: float, reliability: float
) -> float:
    tensile = material.tensile_strength_mpa
    base = endurance_limit_prime_mpa(tensile)
    surface = surface_factor("machined", tensile)
    size = size_factor(min(max(diameter_mm, 2.79), 254.0))
    closest = min(_RELIABILITY_ENDURANCE, key=lambda key: abs(key - reliability))
    return surface * size * _RELIABILITY_ENDURANCE[closest] * base


def goodman_diameter_mm(
    *,
    moment_nmm: float,
    torque_nmm: float,
    material: ShaftMaterial,
    safety_factor: float,
    reliability: float,
    concentration: str,
) -> tuple[float, float]:
    """Shigley DE-Goodman for reversed bending with steady torsion.

    The size factor depends on the diameter it is solving for, so this settles
    to a fixed point. Returns the diameter and the endurance limit reached.
    """
    try:
        bending_kf, torsion_kf = STRESS_CONCENTRATIONS[concentration]
    except KeyError:
        raise GearSizingError(
            "input.unknown_option",
            f"unknown stress concentration {concentration!r}",
            {"valid": sorted(STRESS_CONCENTRATIONS)},
        ) from None

    diameter = 20.0
    endurance = 0.0
    for _ in range(_MAX_ITERATIONS):
        endurance = endurance_limit_mpa(
            material=material, diameter_mm=diameter, reliability=reliability
        )
        bracket = (
            2.0 * bending_kf * moment_nmm / endurance
            + sqrt(3.0) * torsion_kf * torque_nmm / material.tensile_strength_mpa
        )
        nxt = (16.0 * safety_factor / pi * bracket) ** (1.0 / 3.0)
        if abs(nxt - diameter) < _TOLERANCE:
            diameter = nxt
            break
        diameter = nxt
    return diameter, endurance


def size_shaft(
    *,
    tangential_force_n: float,
    radial_force_n: float,
    torque_nmm: float,
    face_width_mm: float,
    root_diameter_mm: float,
    module_mm: float,
    material: ShaftMaterial,
    duty: str,
    safety_factor: float,
    reliability: float,
    position_ratio: float = 0.5,
    span_mm: float | None = None,
    span_shaft_factor: float = 3.0,
    keyway_present: bool = True,
    concentration: str = "profile_keyway",
) -> tuple[ShaftResult, list[str]]:
    """Size the pinion shaft, deriving the bearing span if none is supplied.

    Span, moment and diameter depend on one another. The loop runs on an
    unrounded span so it cannot fall into a rounding limit cycle, and the span
    is snapped to a 5 mm step once at the end.
    """
    warnings: list[str] = []
    shear = allowable_shear_mpa(material, keyway_present)
    fixed_span = span_mm is not None

    if fixed_span and span_mm is not None and span_mm <= face_width_mm:
        raise GearSizingError(
            "bearing.layout",
            "bearing span must exceed the face width",
            {"span_mm": span_mm, "face_width_mm": face_width_mm},
        )

    diameter = 20.0
    span = span_mm if fixed_span else face_width_mm + span_shaft_factor * diameter
    loads = None
    endurance = 0.0
    for _ in range(_MAX_ITERATIONS):
        assert span is not None
        loads = shaft_loads(
            tangential_force_n=tangential_force_n,
            radial_force_n=radial_force_n,
            torque_nmm=torque_nmm,
            span_mm=span,
            position_ratio=position_ratio,
        )
        by_asme = asme_diameter_mm(
            moment_nmm=loads.bending_moment_nmm,
            torque_nmm=torque_nmm,
            shear_mpa=shear,
            duty=duty,
        )
        by_goodman, endurance = goodman_diameter_mm(
            moment_nmm=loads.bending_moment_nmm,
            torque_nmm=torque_nmm,
            material=material,
            safety_factor=safety_factor,
            reliability=reliability,
            concentration=concentration,
        )
        required = max(by_asme, by_goodman)
        if abs(required - diameter) < _TOLERANCE:
            diameter = required
            break
        diameter = required
        if not fixed_span:
            span = face_width_mm + span_shaft_factor * diameter

    if not fixed_span:
        span = ceil_to_step(face_width_mm + span_shaft_factor * diameter, 5.0)
        warnings.append(
            f"bearing span of {span:.0f} mm is a derived proportion, not a "
            "housing layout; replace it with the real bearing centres before "
            "releasing geometry"
        )

    assert span is not None
    loads = shaft_loads(
        tangential_force_n=tangential_force_n,
        radial_force_n=radial_force_n,
        torque_nmm=torque_nmm,
        span_mm=span,
        position_ratio=position_ratio,
    )
    by_asme = asme_diameter_mm(
        moment_nmm=loads.bending_moment_nmm,
        torque_nmm=torque_nmm,
        shear_mpa=shear,
        duty=duty,
    )
    by_goodman, endurance = goodman_diameter_mm(
        moment_nmm=loads.bending_moment_nmm,
        torque_nmm=torque_nmm,
        material=material,
        safety_factor=safety_factor,
        reliability=reliability,
        concentration=concentration,
    )
    required = max(by_asme, by_goodman)
    selected = select_from_series(required, ISO15_BORE_SERIES)
    max_bore = root_diameter_mm - 5.0 * module_mm

    return (
        ShaftResult(
            loads=loads,
            allowable_shear_mpa=shear,
            endurance_limit_mpa=endurance,
            asme_diameter_mm=by_asme,
            goodman_diameter_mm=by_goodman,
            required_diameter_mm=required,
            selected_diameter_mm=selected,
            max_bore_from_root_mm=max_bore,
        ),
        warnings,
    )


__all__ = [
    "SHOCK_FACTORS",
    "STRESS_CONCENTRATIONS",
    "ShaftLoads",
    "ShaftResult",
    "allowable_shear_mpa",
    "asme_diameter_mm",
    "endurance_limit_mpa",
    "goodman_diameter_mm",
    "shaft_loads",
    "size_shaft",
]
