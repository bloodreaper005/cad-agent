from __future__ import annotations

from dataclasses import dataclass
from math import sqrt


@dataclass(frozen=True, slots=True)
class StrengthResult:
    stress_mpa: float
    allowable_mpa: float
    life_factor: float
    cycles: float
    safety_factor: float


def load_cycles(speed_rpm: float, life_hours: float, contacts_per_rev: int = 1) -> float:
    return 60.0 * speed_rpm * life_hours * contacts_per_rev


def bending_stress_mpa(
    *,
    tangential_force_n: float,
    face_width_mm: float,
    module_mm: float,
    form_factor: float,
    stress_correction: float,
    overload: float,
    dynamic: float,
    size: float,
    load_distribution: float,
    rim: float,
) -> float:
    """AGMA 2101-D04 eq. 1, evaluated through the ISO 6336-3 form factors."""
    return (
        tangential_force_n
        * overload
        * dynamic
        * size
        * load_distribution
        * rim
        / (face_width_mm * module_mm)
        * form_factor
        * stress_correction
    )


def contact_stress_mpa(
    *,
    tangential_force_n: float,
    face_width_mm: float,
    pinion_pitch_mm: float,
    elastic_coefficient: float,
    geometry_factor: float,
    overload: float,
    dynamic: float,
    size: float,
    load_distribution: float,
    surface_condition: float = 1.0,
) -> float:
    """AGMA 2101-D04 eq. 2. Both members see the same contact stress."""
    return elastic_coefficient * sqrt(
        tangential_force_n
        * overload
        * dynamic
        * size
        * load_distribution
        / (pinion_pitch_mm * face_width_mm)
        * surface_condition
        / geometry_factor
    )


def rate(
    *,
    stress_mpa: float,
    allowable_stress_number_mpa: float,
    life_factor: float,
    cycles: float,
    temperature: float,
    reliability: float,
    hardness_ratio: float = 1.0,
) -> StrengthResult:
    allowable = (
        allowable_stress_number_mpa * life_factor * hardness_ratio
        / (temperature * reliability)
    )
    return StrengthResult(
        stress_mpa=stress_mpa,
        allowable_mpa=allowable,
        life_factor=life_factor,
        cycles=cycles,
        safety_factor=allowable / stress_mpa,
    )


def contact_requirement(safety_factor: float, basis: str) -> float:
    """The contact safety factor a design must reach.

    Hertzian stress goes as the square root of load, so a stress-basis factor
    is not the same risk as a bending factor of the same number. On a load
    basis the requirement is sqrt(SF); on a stress basis it is SF, which is
    the more conservative reading and the default.
    """
    if basis == "load":
        return sqrt(safety_factor)
    return safety_factor


__all__ = [
    "StrengthResult",
    "bending_stress_mpa",
    "contact_requirement",
    "contact_stress_mpa",
    "load_cycles",
    "rate",
]
