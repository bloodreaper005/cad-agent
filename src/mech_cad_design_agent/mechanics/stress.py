"""Load and stress analysis.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapter 3.

Units are consistent throughout the package: millimetres, newtons, megapascals,
newton-millimetres. A stress state is carried as the six Cartesian components so
that the failure theories in `static_failure` and `fatigue` never have to guess
what a caller meant by "the stress".
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, isclose, pi, sqrt

from .errors import MechanicsError, require_positive


@dataclass(frozen=True)
class StressState:
    """A general three-dimensional stress state at one point, in MPa."""

    sigma_x: float = 0.0
    sigma_y: float = 0.0
    sigma_z: float = 0.0
    tau_xy: float = 0.0
    tau_yz: float = 0.0
    tau_zx: float = 0.0

    @property
    def principal_stresses(self) -> tuple[float, float, float]:
        """Return the principal stresses ordered sigma_1 >= sigma_2 >= sigma_3."""
        return principal_stresses(self)

    @property
    def von_mises(self) -> float:
        return von_mises_stress(self)

    @property
    def max_shear(self) -> float:
        """Maximum shear stress, half the difference of the extreme principals."""
        one, _, three = self.principal_stresses
        return (one - three) / 2.0


def principal_stresses(state: StressState) -> tuple[float, float, float]:
    """Solve the characteristic equation of the stress tensor.

    Shigley eq. 3-15. The cubic is solved trigonometrically rather than
    numerically so that a plane-stress state returns its exact closed-form
    roots and a hydrostatic state does not depend on iteration tolerance.
    """
    sx, sy, sz = state.sigma_x, state.sigma_y, state.sigma_z
    txy, tyz, tzx = state.tau_xy, state.tau_yz, state.tau_zx

    # Stress invariants, Shigley eq. 3-15.
    i1 = sx + sy + sz
    i2 = sx * sy + sx * sz + sy * sz - txy**2 - tyz**2 - tzx**2
    i3 = (
        sx * sy * sz
        + 2.0 * txy * tyz * tzx
        - sx * tyz**2
        - sy * tzx**2
        - sz * txy**2
    )

    # Depressed cubic s^3 - i1 s^2 + i2 s - i3 = 0 via the substitution
    # s = p + i1/3, giving p^3 + b p + c = 0.
    b = i2 - i1**2 / 3.0
    c = -(2.0 * i1**3 / 27.0 - i1 * i2 / 3.0 + i3)

    if isclose(b, 0.0, abs_tol=1e-12) and isclose(c, 0.0, abs_tol=1e-12):
        root = i1 / 3.0
        return (root, root, root)

    # Every root of a real symmetric tensor is real, so the discriminant of the
    # depressed cubic cannot be positive beyond rounding. Clamping keeps the
    # arccos in domain for a near-degenerate state such as pure hydrostatic.
    radius = 2.0 * sqrt(max(-b / 3.0, 0.0))
    if isclose(radius, 0.0, abs_tol=1e-15):
        root = i1 / 3.0
        return (root, root, root)
    cos_argument = (3.0 * c) / (b * radius) if b != 0.0 else 0.0
    cos_argument = max(-1.0, min(1.0, cos_argument))
    phi = atan2(sqrt(max(1.0 - cos_argument**2, 0.0)), cos_argument) / 3.0

    roots = [
        radius * cos(phi) + i1 / 3.0,
        radius * cos(phi - 2.0 * pi / 3.0) + i1 / 3.0,
        radius * cos(phi - 4.0 * pi / 3.0) + i1 / 3.0,
    ]

    # The trigonometric form loses about half its significant digits near a
    # repeated root, and a repeated root is the ordinary case here: uniaxial
    # tension, pure shear and every plane-stress state have one. Deflating by
    # the best-conditioned root, which is the one largest in magnitude, and
    # solving the remaining quadratic in closed form restores full precision.
    # For uniaxial tension this returns exactly (sigma, 0, 0) rather than
    # (sigma, 3e-7, -3e-7).
    pivot = max(roots, key=abs)
    linear = -i1 + pivot                      # synthetic division, s^2 coefficient
    constant = i2 + pivot * linear            # ... and the constant term
    discriminant = linear * linear - 4.0 * constant
    if discriminant >= 0.0:
        root_disc = sqrt(discriminant)
        other = [(-linear + root_disc) / 2.0, (-linear - root_disc) / 2.0]
        resolved = [pivot, *other]
    else:
        resolved = roots
    resolved.sort(reverse=True)
    return (resolved[0], resolved[1], resolved[2])


def plane_principal_stresses(
    sigma_x: float, sigma_y: float, tau_xy: float
) -> tuple[float, float]:
    """Return the two in-plane principal stresses, Shigley eq. 3-13.

    The third principal stress of a plane-stress state is zero and must still be
    carried into any failure theory, so prefer `principal_stresses` on a full
    `StressState` unless the in-plane pair is what is actually wanted.
    """
    mean = (sigma_x + sigma_y) / 2.0
    radius = sqrt(((sigma_x - sigma_y) / 2.0) ** 2 + tau_xy**2)
    return (mean + radius, mean - radius)


def max_in_plane_shear(sigma_x: float, sigma_y: float, tau_xy: float) -> float:
    """Maximum in-plane shear stress, Shigley eq. 3-14."""
    return sqrt(((sigma_x - sigma_y) / 2.0) ** 2 + tau_xy**2)


def principal_angle_deg(sigma_x: float, sigma_y: float, tau_xy: float) -> float:
    """Angle from the x axis to the first principal direction, Shigley eq. 3-12."""
    return 0.5 * atan2(2.0 * tau_xy, sigma_x - sigma_y) * 180.0 / pi


def von_mises_stress(state: StressState) -> float:
    """Von Mises effective stress, Shigley eq. 3-14 / 5-12.

    Written from the Cartesian components rather than the principal stresses so
    that it stays exact for a state whose cubic is ill-conditioned.
    """
    sx, sy, sz = state.sigma_x, state.sigma_y, state.sigma_z
    txy, tyz, tzx = state.tau_xy, state.tau_yz, state.tau_zx
    return sqrt(
        0.5
        * (
            (sx - sy) ** 2
            + (sy - sz) ** 2
            + (sz - sx) ** 2
            + 6.0 * (txy**2 + tyz**2 + tzx**2)
        )
    )


def plane_von_mises(sigma_x: float, sigma_y: float, tau_xy: float) -> float:
    """Von Mises stress for plane stress, Shigley eq. 5-15."""
    return sqrt(sigma_x**2 - sigma_x * sigma_y + sigma_y**2 + 3.0 * tau_xy**2)


# --- Elementary load cases -------------------------------------------------


def axial_stress(force_n: float, area_mm2: float) -> float:
    """Normal stress from a centric axial load, Shigley eq. 3-22."""
    return force_n / require_positive(area_mm2, "area_mm2")


def bending_stress(moment_nmm: float, distance_mm: float, inertia_mm4: float) -> float:
    """Flexural stress sigma = M c / I, Shigley eq. 3-24."""
    return moment_nmm * distance_mm / require_positive(inertia_mm4, "inertia_mm4")


def torsional_shear_stress(
    torque_nmm: float, radius_mm: float, polar_inertia_mm4: float
) -> float:
    """Torsional shear stress tau = T r / J, Shigley eq. 3-37."""
    return (
        torque_nmm
        * radius_mm
        / require_positive(polar_inertia_mm4, "polar_inertia_mm4")
    )


def transverse_shear_stress(
    shear_n: float, first_moment_mm3: float, inertia_mm4: float, width_mm: float
) -> float:
    """Transverse shear stress tau = V Q / (I b), Shigley eq. 3-31."""
    return (
        shear_n
        * first_moment_mm3
        / (
            require_positive(inertia_mm4, "inertia_mm4")
            * require_positive(width_mm, "width_mm")
        )
    )


def max_transverse_shear_rectangle(shear_n: float, area_mm2: float) -> float:
    """Peak transverse shear in a rectangular section, 3V/2A. Shigley table 3-2."""
    return 1.5 * shear_n / require_positive(area_mm2, "area_mm2")


def max_transverse_shear_circle(shear_n: float, area_mm2: float) -> float:
    """Peak transverse shear in a solid round section, 4V/3A. Shigley table 3-2."""
    return 4.0 * shear_n / (3.0 * require_positive(area_mm2, "area_mm2"))


# --- Section properties ----------------------------------------------------


def round_section_inertia_mm4(diameter_mm: float) -> float:
    """Second moment of area of a solid round section, pi d^4 / 64."""
    return pi * require_positive(diameter_mm, "diameter_mm") ** 4 / 64.0


def round_section_polar_inertia_mm4(diameter_mm: float) -> float:
    """Polar second moment of a solid round section, pi d^4 / 32."""
    return pi * require_positive(diameter_mm, "diameter_mm") ** 4 / 32.0


def hollow_round_inertia_mm4(outer_mm: float, inner_mm: float) -> float:
    outer = require_positive(outer_mm, "outer_mm")
    inner = require_positive(inner_mm, "inner_mm")
    if inner >= outer:
        raise MechanicsError(
            "input.out_of_range",
            "inner diameter must be smaller than outer diameter",
            {"outer_mm": outer, "inner_mm": inner},
        )
    return pi * (outer**4 - inner**4) / 64.0


def rectangle_inertia_mm4(width_mm: float, height_mm: float) -> float:
    """Second moment about the centroidal axis parallel to the width, b h^3 / 12."""
    return (
        require_positive(width_mm, "width_mm")
        * require_positive(height_mm, "height_mm") ** 3
        / 12.0
    )


# --- Pressure vessels and interference fits --------------------------------


def thin_wall_cylinder_stresses(
    pressure_mpa: float, inner_diameter_mm: float, thickness_mm: float
) -> tuple[float, float]:
    """Tangential and longitudinal stress in a thin-walled cylinder.

    Shigley eq. 3-53 and 3-54, using the mean diameter for the tangential
    stress as the text does. Valid where the wall is under about one twentieth
    of the diameter; the caller is responsible for that judgement.
    """
    pressure = require_positive(pressure_mpa, "pressure_mpa")
    inner = require_positive(inner_diameter_mm, "inner_diameter_mm")
    thickness = require_positive(thickness_mm, "thickness_mm")
    tangential = pressure * (inner + thickness) / (2.0 * thickness)
    longitudinal = pressure * inner / (4.0 * thickness)
    return (tangential, longitudinal)


def thick_wall_cylinder_stresses(
    *,
    inner_pressure_mpa: float,
    outer_pressure_mpa: float,
    inner_radius_mm: float,
    outer_radius_mm: float,
    radius_mm: float,
) -> tuple[float, float]:
    """Lame tangential and radial stress in a thick-walled cylinder.

    Shigley eq. 3-49 and 3-50.
    """
    r_i = require_positive(inner_radius_mm, "inner_radius_mm")
    r_o = require_positive(outer_radius_mm, "outer_radius_mm")
    r = require_positive(radius_mm, "radius_mm")
    if not r_i <= r <= r_o:
        raise MechanicsError(
            "input.out_of_range",
            "evaluation radius must lie within the wall",
            {"inner_radius_mm": r_i, "outer_radius_mm": r_o, "radius_mm": r},
        )
    p_i, p_o = inner_pressure_mpa, outer_pressure_mpa
    denominator = r_o**2 - r_i**2
    common = (p_i - p_o) * r_i**2 * r_o**2 / (r**2 * denominator)
    base = (p_i * r_i**2 - p_o * r_o**2) / denominator
    tangential = base + common
    radial = base - common
    return (tangential, radial)


__all__ = [
    "StressState",
    "axial_stress",
    "bending_stress",
    "hollow_round_inertia_mm4",
    "max_in_plane_shear",
    "max_transverse_shear_circle",
    "max_transverse_shear_rectangle",
    "plane_principal_stresses",
    "plane_von_mises",
    "principal_angle_deg",
    "principal_stresses",
    "rectangle_inertia_mm4",
    "round_section_inertia_mm4",
    "round_section_polar_inertia_mm4",
    "thick_wall_cylinder_stresses",
    "thin_wall_cylinder_stresses",
    "torsional_shear_stress",
    "transverse_shear_stress",
    "von_mises_stress",
]
