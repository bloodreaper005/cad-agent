from __future__ import annotations

from math import log, sqrt

from .errors import GearSizingError


# --------------------------------------------------------------------------
# K_o, overload. ANSI/AGMA 6010-F97 service-factor practice, not a normative
# 2101 value. Rows are the driving machine, columns the driven-load character.
# --------------------------------------------------------------------------
OVERLOAD_FACTORS: dict[str, dict[str, float]] = {
    "electric_motor": {"uniform": 1.00, "light": 1.25, "moderate": 1.50, "heavy": 1.75},
    "multi_cylinder_engine": {"uniform": 1.25, "light": 1.50, "moderate": 1.75, "heavy": 2.00},
    "single_cylinder_engine": {"uniform": 1.50, "light": 1.75, "moderate": 2.00, "heavy": 2.25},
}

# K_Hma coefficients, AGMA 2101-D04 empirical method, face width in mm.
MESH_ALIGNMENT_CLASSES: dict[str, tuple[float, float, float]] = {
    "open": (0.2470, 6.570e-4, -1.186e-7),
    "commercial": (0.1270, 6.220e-4, -1.441e-7),
    "precision": (0.0675, 5.040e-4, -1.435e-7),
    "extra_precision": (0.00380, 4.016e-4, -1.274e-7),
}


def overload_factor(duty: str, driver: str = "electric_motor") -> float:
    try:
        row = OVERLOAD_FACTORS[driver]
    except KeyError:
        raise GearSizingError(
            "input.unknown_option",
            f"unknown driver {driver!r}",
            {"valid": sorted(OVERLOAD_FACTORS)},
        ) from None
    try:
        return row[duty]
    except KeyError:
        raise GearSizingError(
            "input.unknown_option",
            f"unknown duty {duty!r}",
            {"valid": sorted(row)},
        ) from None


def dynamic_factor(pitch_velocity_ms: float, quality_grade: int) -> float:
    """K_v, AGMA 2101-D04 eq. 27-28."""
    if not 6 <= quality_grade <= 11:
        raise GearSizingError(
            "input.out_of_range",
            "transmission accuracy grade Qv must be between 6 and 11",
            {"quality_grade": quality_grade},
        )
    b = 0.25 * (12.0 - quality_grade) ** (2.0 / 3.0)
    a = 50.0 + 56.0 * (1.0 - b)
    return ((a + sqrt(200.0 * pitch_velocity_ms)) / a) ** b


def max_pitch_velocity(quality_grade: int) -> float:
    """The velocity above which the K_v relation is no longer valid, m/s."""
    b = 0.25 * (12.0 - quality_grade) ** (2.0 / 3.0)
    a = 50.0 + 56.0 * (1.0 - b)
    return (a + (quality_grade - 3)) ** 2 / 200.0


def size_factor(module_mm: float, surface_hardened: bool) -> float:
    """K_s as the reciprocal of the ISO 6336 size factor Y_X.

    Exactly 1.0 below module 5, which keeps the common case hand-checkable.
    """
    if surface_hardened:
        if module_mm <= 5.0:
            y_x = 1.0
        elif module_mm <= 25.0:
            y_x = 1.05 - 0.010 * module_mm
        else:
            y_x = 0.80
    else:
        if module_mm <= 5.0:
            y_x = 1.0
        elif module_mm <= 30.0:
            y_x = 1.03 - 0.006 * module_mm
        else:
            y_x = 0.85
    return 1.0 / y_x


def load_distribution_factor(
    *,
    face_width_mm: float,
    pitch_diameter_mm: float,
    alignment_class: str = "commercial",
    crowned: bool = False,
    centred: bool = True,
    adjusted_at_assembly: bool = False,
) -> tuple[float, float, float]:
    """K_H by the AGMA 2101-D04 empirical method.

    Returns (K_H, K_Hpf, K_Hma) so the pinion-proportion and mesh-alignment
    contributions stay visible in the evidence rather than collapsing into one
    opaque number.
    """
    try:
        a, b_coeff, c_coeff = MESH_ALIGNMENT_CLASSES[alignment_class]
    except KeyError:
        raise GearSizingError(
            "input.unknown_option",
            f"unknown mesh alignment class {alignment_class!r}",
            {"valid": sorted(MESH_ALIGNMENT_CLASSES)},
        ) from None

    ratio = max(face_width_mm / (10.0 * pitch_diameter_mm), 0.05)
    if face_width_mm <= 25.0:
        pinion_proportion = ratio - 0.025
    elif face_width_mm <= 432.0:
        pinion_proportion = ratio - 0.0375 + 0.000492 * face_width_mm
    else:
        pinion_proportion = (
            ratio - 0.1109 + 0.000815 * face_width_mm - 3.53e-7 * face_width_mm**2
        )

    mesh_alignment = a + b_coeff * face_width_mm + c_coeff * face_width_mm**2
    modifier = 0.8 if crowned else 1.0
    pinion_position = 1.0 if centred else 1.1
    correction = 0.8 if adjusted_at_assembly else 1.0
    factor = 1.0 + modifier * (
        pinion_proportion * pinion_position + mesh_alignment * correction
    )
    return factor, pinion_proportion, mesh_alignment


def rim_thickness_factor(
    rim_thickness_mm: float | None, module_mm: float
) -> float:
    """K_B, AGMA 2101-D04 eq. 30. A solid blank gives 1.0."""
    if rim_thickness_mm is None:
        return 1.0
    whole_depth = 2.25 * module_mm
    backup_ratio = rim_thickness_mm / whole_depth
    if backup_ratio >= 1.2:
        return 1.0
    if backup_ratio <= 0:
        raise GearSizingError(
            "input.non_positive", "rim thickness must be positive"
        )
    return 1.6 * log(2.242 / backup_ratio)


# --------------------------------------------------------------------------
# Stress-cycle factors. Both families are anchored by a self-check the test
# suite asserts: every Y_N short-life curve meets the long-life curve at
# 1.0396 at 3e6 cycles, and both Z_N branches equal 1.000 at 1e7 cycles.
# --------------------------------------------------------------------------
_BENDING_SHORT_LIFE: dict[str, tuple[float, float]] = {
    "carburised": (6.1514, -0.1192),
    "through_250HB": (4.9404, -0.1045),
    "through_160HB": (3.5170, -0.0817),
}
_BENDING_LONG_LIFE = (1.6831, -0.0323)


def bending_stress_cycle_factor(cycles: float, life_curve: str) -> float:
    """Y_N, AGMA 2101-D04 Fig. 17."""
    if cycles <= 0:
        raise GearSizingError("input.non_positive", "cycle count must be positive")
    if cycles >= 3.0e6:
        coefficient, exponent = _BENDING_LONG_LIFE
        return coefficient * cycles**exponent
    try:
        coefficient, exponent = _BENDING_SHORT_LIFE[life_curve]
    except KeyError:
        raise GearSizingError(
            "input.unknown_option",
            f"no bending life curve for {life_curve!r}",
            {"valid": sorted(_BENDING_SHORT_LIFE)},
        ) from None
    return coefficient * cycles**exponent


def contact_stress_cycle_factor(cycles: float) -> float:
    """Z_N, AGMA 2101-D04 Fig. 18, steel excluding nitrided."""
    if cycles <= 0:
        raise GearSizingError("input.non_positive", "cycle count must be positive")
    if cycles < 1.0e7:
        return 2.466 * cycles**-0.056
    return 1.4488 * cycles**-0.023


def reliability_factor(reliability: float) -> float:
    """K_R (Y_Z), AGMA 2101-D04 Table 10."""
    if not 0.5 <= reliability <= 0.9999:
        raise GearSizingError(
            "input.out_of_range",
            "reliability must be between 0.50 and 0.9999",
            {"reliability": reliability},
        )
    if reliability < 0.99:
        return 0.658 - 0.0759 * log(1.0 - reliability)
    return 0.500 - 0.109 * log(1.0 - reliability)


def temperature_factor(oil_temperature_c: float) -> float:
    """K_T (Y_theta). Unity up to 120 C."""
    if oil_temperature_c <= 120.0:
        return 1.0
    return (273.0 + oil_temperature_c) / 393.0


def hardness_ratio_factor(
    pinion_brinell: float, gear_brinell: float, ratio: float, both_through_hardened: bool
) -> float:
    """Z_W. Applies to the gear only, and only for through-hardened pairs."""
    if not both_through_hardened:
        return 1.0
    hardness_ratio = pinion_brinell / gear_brinell
    if hardness_ratio < 1.2:
        coefficient = 0.0
    elif hardness_ratio <= 1.7:
        coefficient = 8.98e-3 * hardness_ratio - 8.29e-3
    else:
        coefficient = 0.00698
    return 1.0 + coefficient * (ratio - 1.0)


__all__ = [
    "MESH_ALIGNMENT_CLASSES",
    "OVERLOAD_FACTORS",
    "bending_stress_cycle_factor",
    "contact_stress_cycle_factor",
    "dynamic_factor",
    "hardness_ratio_factor",
    "load_distribution_factor",
    "max_pitch_velocity",
    "overload_factor",
    "reliability_factor",
    "rim_thickness_factor",
    "size_factor",
    "temperature_factor",
]
