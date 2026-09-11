from __future__ import annotations

from dataclasses import dataclass
from math import acos, cos, pi, sin, sqrt, tan

from .errors import GearSizingError
from .standards import (
    BASIC_RACK_ADDENDUM_FACTOR,
    BASIC_RACK_DEDENDUM_FACTOR,
    BASIC_RACK_TIP_RADIUS_FACTOR,
    involute,
)


_MAX_ITERATIONS = 200
_TOLERANCE = 1e-12


@dataclass(frozen=True, slots=True)
class ToothForm:
    """ISO 6336-3 Method B tooth form, normalised by the module."""

    teeth: int
    form_factor: float            # Y_F
    stress_correction: float      # Y_S
    notch_parameter: float        # q_s
    root_chord: float             # s_Fn / m
    root_fillet_radius: float     # rho_F / m
    bending_moment_arm: float     # h_Fe / m
    tangent_angle: float          # theta, rad
    agma_j_equivalent: float      # 1 / (Y_F * Y_S), comparable with AGMA charts


def _solve_tangent_angle(teeth: int, g_factor: float, h_factor: float) -> float:
    """Fixed point for theta = (2G/z)*tan(theta) - H, from theta0 = pi/6.

    The 30-degree tangent point locates the critical root section. The
    iteration is contractive here because 2G/z is small and negative for a
    standard rack, so it settles in well under 30 passes.
    """
    angle = pi / 6.0
    coefficient = 2.0 * g_factor / teeth
    for _ in range(_MAX_ITERATIONS):
        nxt = coefficient * tan(angle) - h_factor
        if abs(nxt - angle) < _TOLERANCE:
            return nxt
        angle = nxt
    raise GearSizingError(
        "toothform.no_convergence",
        f"root tangent angle did not converge for {teeth} teeth",
        {"teeth": teeth},
    )


def tooth_form(
    *,
    teeth: int,
    pressure_angle_rad: float,
    contact_ratio: float,
    tip_diameter_over_module: float,
    profile_shift: float = 0.0,
    dedendum_factor: float = BASIC_RACK_DEDENDUM_FACTOR,
    tip_radius_factor: float = BASIC_RACK_TIP_RADIUS_FACTOR,
    load_at_tip: bool = False,
) -> ToothForm:
    """Form and stress-correction factors by ISO 6336-3 Method B.

    Everything is normalised by the module, so the result depends only on tooth
    count, pressure angle, the basic rack and the load position. Method B loads
    at the outer point of single-pair contact; `load_at_tip` instead loads at
    the tip, which is the condition the published Y_Fa / Y_Sa charts tabulate.
    """
    if teeth < 12 or teeth > 400:
        raise GearSizingError(
            "toothform.virtual_teeth_range",
            f"tooth form is defined for 12 to 400 teeth, not {teeth}",
            {"teeth": teeth},
        )

    sin_alpha = sin(pressure_angle_rad)
    cos_alpha = cos(pressure_angle_rad)

    rack_e = (
        pi / 4.0
        - dedendum_factor * tan(pressure_angle_rad)
        - (1.0 - sin_alpha) * tip_radius_factor / cos_alpha
    )
    g_factor = tip_radius_factor - dedendum_factor + profile_shift
    h_factor = (2.0 / teeth) * (pi / 2.0 - rack_e) - pi / 3.0
    theta = _solve_tangent_angle(teeth, g_factor, h_factor)

    cos_theta = cos(theta)
    root_offset = g_factor / cos_theta - tip_radius_factor
    root_chord = teeth * sin(pi / 3.0 - theta) + sqrt(3.0) * root_offset

    denominator = cos_theta * (teeth * cos_theta**2 - 2.0 * g_factor)
    if denominator <= 0.0:
        raise GearSizingError(
            "teeth.undercut",
            f"root fillet is undefined for {teeth} teeth; the tooth is undercut",
            {"teeth": teeth},
        )
    root_fillet = tip_radius_factor + 2.0 * g_factor**2 / denominator

    base_over_module = teeth * cos_alpha
    if load_at_tip:
        load_diameter = tip_diameter_over_module
    else:
        base_pitch = pi * cos_alpha
        half_chord = sqrt(
            max((tip_diameter_over_module / 2.0) ** 2 - (base_over_module / 2.0) ** 2, 0.0)
        ) - (contact_ratio - 1.0) * base_pitch
        load_diameter = 2.0 * sqrt(half_chord**2 + (base_over_module / 2.0) ** 2)

    load_angle = acos(min(1.0, base_over_module / load_diameter))
    span = (
        (pi / 2.0 + 2.0 * profile_shift * tan(pressure_angle_rad)) / teeth
        + involute(pressure_angle_rad)
        - involute(load_angle)
    )
    load_direction = load_angle - span
    moment_arm = 0.5 * (
        (cos(span) - sin(span) * tan(load_direction)) * load_diameter
        - teeth * cos(pi / 3.0 - theta)
        - root_offset
    )

    form_factor = (
        6.0 * moment_arm * cos(load_direction) / (root_chord**2 * cos_alpha)
    )
    notch = root_chord / (2.0 * root_fillet)
    if not 1.0 <= notch < 8.0:
        raise GearSizingError(
            "toothform.qs_range",
            f"notch parameter {notch:.3f} is outside the 1 to 8 range the "
            "stress-correction fit is valid over",
            {"notch_parameter": notch, "teeth": teeth},
        )
    slenderness = root_chord / moment_arm
    stress_correction = (1.2 + 0.13 * slenderness) * notch ** (
        1.0 / (1.21 + 2.3 / slenderness)
    )

    return ToothForm(
        teeth=teeth,
        form_factor=form_factor,
        stress_correction=stress_correction,
        notch_parameter=notch,
        root_chord=root_chord,
        root_fillet_radius=root_fillet,
        bending_moment_arm=moment_arm,
        tangent_angle=theta,
        agma_j_equivalent=1.0 / (form_factor * stress_correction),
    )


def contact_geometry_factor(pressure_angle_rad: float, ratio: float) -> float:
    """Z_I for an external spur pair. Exact: the load-sharing ratio is unity."""
    return (
        cos(pressure_angle_rad)
        * sin(pressure_angle_rad)
        / 2.0
        * (ratio / (ratio + 1.0))
    )


__all__ = [
    "BASIC_RACK_ADDENDUM_FACTOR",
    "ToothForm",
    "contact_geometry_factor",
    "tooth_form",
]
