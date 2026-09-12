"""Gears: general geometry, spur and helical, bevel and worm.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapters 13 to 15.

Chapter 13 is kinematics and geometry for every gear type; chapter 14 rates spur
and helical teeth; chapter 15 covers bevel and worm gearing. The AGMA rating
factors for spur drives, verified against published ISO 6336-3 chart values,
live in the `gear_sizing` package and are not duplicated here. What this module
adds is the geometry, the kinematics and the force resolution for the gear types
that package does not handle, along with the general forms of the rating
equations.

Angles are radians at the interface and degrees only where a helix or pressure
angle is named in the usual engineering way; every such argument says which.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan, atan2, cos, degrees, inf, pi, radians, sin, sqrt, tan

from .errors import MechanicsError, require_non_negative, require_positive


# Shigley eq. 13-11 and 13-12 use the addendum constant k: 1.0 for full-depth
# teeth, 0.8 for stub teeth.
FULL_DEPTH_ADDENDUM_CONSTANT = 1.0
STUB_ADDENDUM_CONSTANT = 0.8


# --- Chapter 13, nomenclature and kinematics -------------------------------


def pitch_diameter_mm(module_mm: float, teeth: int) -> float:
    """d = m N, Shigley eq. 13-1 in metric form."""
    return require_positive(module_mm, "module_mm") * _require_teeth(teeth)


def circular_pitch_mm(module_mm: float) -> float:
    """p = pi m, Shigley eq. 13-3."""
    return pi * require_positive(module_mm, "module_mm")


def module_from_diametral_pitch(diametral_pitch_per_inch: float) -> float:
    """m = 25.4 / P, the bridge between the two conventions the text uses."""
    return 25.4 / require_positive(diametral_pitch_per_inch, "diametral_pitch_per_inch")


def gear_ratio(pinion_teeth: int, gear_teeth: int) -> float:
    """Speed ratio, Shigley eq. 13-5."""
    return _require_teeth(gear_teeth) / _require_teeth(pinion_teeth)


def train_value(driving_teeth: tuple[int, ...], driven_teeth: tuple[int, ...]) -> float:
    """Train value e, Shigley eq. 13-6.

    The product of driving tooth counts over the product of driven ones. An idler
    appears in both products and cancels, which is why it changes direction
    without changing ratio.
    """
    if not driving_teeth or not driven_teeth:
        raise MechanicsError(
            "input.incomplete", "a train needs both driving and driven gears", {}
        )
    numerator = 1.0
    for count in driving_teeth:
        numerator *= _require_teeth(count)
    denominator = 1.0
    for count in driven_teeth:
        denominator *= _require_teeth(count)
    return numerator / denominator


def planetary_train_value(
    *, first_rpm: float, last_rpm: float, arm_rpm: float
) -> float:
    """e = (n_L - n_A) / (n_F - n_A), Shigley eq. 13-30.

    The relation that makes an epicyclic train tractable: velocities are taken
    relative to the arm, which reduces the train to an ordinary one.
    """
    denominator = first_rpm - arm_rpm
    if abs(denominator) < 1e-12:
        raise MechanicsError(
            "input.out_of_range",
            "the first gear turns with the arm, so the train value is undefined",
            {"first_rpm": first_rpm, "arm_rpm": arm_rpm},
        )
    return (last_rpm - arm_rpm) / denominator


def contact_ratio(
    *,
    module_mm: float,
    pinion_teeth: int,
    gear_teeth: int,
    pressure_angle_deg: float = 20.0,
    addendum_constant: float = FULL_DEPTH_ADDENDUM_CONSTANT,
) -> float:
    """Contact ratio m_c, Shigley eq. 13-7.

    Below 1.2 the text warns the mesh is unreliable, since manufacturing and
    mounting error can drop an instant of the cycle below a single tooth pair in
    contact.
    """
    module = require_positive(module_mm, "module_mm")
    phi = radians(require_positive(pressure_angle_deg, "pressure_angle_deg"))
    pinion = _require_teeth(pinion_teeth)
    gear = _require_teeth(gear_teeth)
    addendum = addendum_constant * module

    r_p = module * pinion / 2.0
    r_g = module * gear / 2.0
    rb_p = r_p * cos(phi)
    rb_g = r_g * cos(phi)
    centre = r_p + r_g

    length = (
        sqrt(max((r_p + addendum) ** 2 - rb_p**2, 0.0))
        + sqrt(max((r_g + addendum) ** 2 - rb_g**2, 0.0))
        - centre * sin(phi)
    )
    return length / (pi * module * cos(phi))


def smallest_pinion_with_rack(
    *,
    pressure_angle_deg: float = 20.0,
    addendum_constant: float = FULL_DEPTH_ADDENDUM_CONSTANT,
) -> float:
    """Fewest teeth a pinion may have against a rack, Shigley eq. 13-13.

    N_P = 2k / sin^2 phi. At twenty degrees full depth this is 17.1, which is the
    origin of the familiar eighteen-tooth rule.
    """
    phi = radians(require_positive(pressure_angle_deg, "pressure_angle_deg"))
    return 2.0 * addendum_constant / sin(phi) ** 2


def smallest_pinion_without_interference(
    *,
    gear_ratio_m: float,
    pressure_angle_deg: float = 20.0,
    addendum_constant: float = FULL_DEPTH_ADDENDUM_CONSTANT,
) -> float:
    """Fewest teeth for a pinion meshing a gear of ratio m, Shigley eq. 13-11."""
    ratio = require_positive(gear_ratio_m, "gear_ratio_m")
    phi = radians(require_positive(pressure_angle_deg, "pressure_angle_deg"))
    k = addendum_constant
    sin_squared = sin(phi) ** 2
    return (
        2.0
        * k
        * (ratio + sqrt(ratio**2 + (1.0 + 2.0 * ratio) * sin_squared))
        / ((1.0 + 2.0 * ratio) * sin_squared)
    )


def largest_gear_without_interference(
    *,
    pinion_teeth: int,
    pressure_angle_deg: float = 20.0,
    addendum_constant: float = FULL_DEPTH_ADDENDUM_CONSTANT,
) -> float:
    """Largest gear a given pinion may drive, Shigley eq. 13-12.

    Returns infinity when the pinion is large enough to mesh a rack, which is the
    limiting case of an infinitely large gear.
    """
    pinion = _require_teeth(pinion_teeth)
    phi = radians(require_positive(pressure_angle_deg, "pressure_angle_deg"))
    k = addendum_constant
    sin_squared = sin(phi) ** 2
    denominator = 4.0 * k - 2.0 * pinion * sin_squared
    if denominator <= 0.0:
        return inf
    return (pinion**2 * sin_squared - 4.0 * k**2) / denominator


# --- Chapter 13, helical relations -----------------------------------------


@dataclass(frozen=True)
class HelicalGeometry:
    """The transverse and normal planes of a helical gear.

    A helical gear is cut in the normal plane and meshes in the transverse
    plane, and nearly every error in helical work is a quantity taken from the
    wrong one.
    """

    normal_module_mm: float
    transverse_module_mm: float
    normal_pressure_angle_deg: float
    transverse_pressure_angle_deg: float
    helix_angle_deg: float
    axial_pitch_mm: float
    virtual_teeth: float
    pitch_diameter_mm: float


def helical_geometry(
    *,
    normal_module_mm: float,
    teeth: int,
    helix_angle_deg: float,
    normal_pressure_angle_deg: float = 20.0,
) -> HelicalGeometry:
    """Resolve a helical gear between its normal and transverse planes.

    Shigley eq. 13-18 through 13-19: m_t = m_n / cos psi, tan phi_t = tan phi_n /
    cos psi, and the virtual tooth count N / cos^3 psi that sets the tooth form.
    """
    normal_module = require_positive(normal_module_mm, "normal_module_mm")
    count = _require_teeth(teeth)
    psi = radians(require_non_negative(helix_angle_deg, "helix_angle_deg"))
    if psi >= pi / 2.0:
        raise MechanicsError(
            "input.out_of_range",
            "helix angle must be below ninety degrees",
            {"helix_angle_deg": helix_angle_deg},
        )
    phi_n = radians(require_positive(normal_pressure_angle_deg, "normal_pressure_angle_deg"))
    transverse_module = normal_module / cos(psi)
    phi_t = atan(tan(phi_n) / cos(psi))
    diameter = transverse_module * count
    axial_pitch = (
        inf if abs(tan(psi)) < 1e-15 else pi * transverse_module / tan(psi)
    )
    return HelicalGeometry(
        normal_module_mm=normal_module,
        transverse_module_mm=transverse_module,
        normal_pressure_angle_deg=normal_pressure_angle_deg,
        transverse_pressure_angle_deg=degrees(phi_t),
        helix_angle_deg=float(helix_angle_deg),
        axial_pitch_mm=axial_pitch,
        virtual_teeth=count / cos(psi) ** 3,
        pitch_diameter_mm=diameter,
    )


# --- Chapter 13, tooth forces ----------------------------------------------


@dataclass(frozen=True)
class ToothForces:
    """Force resolution at the mesh, in newtons."""

    tangential_n: float
    radial_n: float
    axial_n: float
    resultant_n: float

    def as_dict(self) -> dict[str, float]:
        return {
            "tangential_n": self.tangential_n,
            "radial_n": self.radial_n,
            "axial_n": self.axial_n,
            "resultant_n": self.resultant_n,
        }


def tangential_force_n(torque_nmm: float, pitch_diameter_mm: float) -> float:
    """W_t = 2 T / d."""
    return 2.0 * abs(torque_nmm) / require_positive(
        pitch_diameter_mm, "pitch_diameter_mm"
    )


def spur_forces(
    *, torque_nmm: float, pitch_diameter_mm: float, pressure_angle_deg: float = 20.0
) -> ToothForces:
    """Spur mesh forces, Shigley eq. 13-35. A spur mesh carries no thrust."""
    phi = radians(require_positive(pressure_angle_deg, "pressure_angle_deg"))
    tangential = tangential_force_n(torque_nmm, pitch_diameter_mm)
    radial = tangential * tan(phi)
    return ToothForces(
        tangential_n=tangential,
        radial_n=radial,
        axial_n=0.0,
        resultant_n=tangential / cos(phi),
    )


def helical_forces(
    *,
    torque_nmm: float,
    pitch_diameter_mm: float,
    helix_angle_deg: float,
    normal_pressure_angle_deg: float = 20.0,
) -> ToothForces:
    """Helical mesh forces, Shigley eq. 13-40.

    The thrust W_t tan psi is the price of the helix and is what a helical
    drive's bearings must be chosen for; a spur drive has none.
    """
    psi = radians(require_non_negative(helix_angle_deg, "helix_angle_deg"))
    phi_n = radians(
        require_positive(normal_pressure_angle_deg, "normal_pressure_angle_deg")
    )
    tangential = tangential_force_n(torque_nmm, pitch_diameter_mm)
    resultant = tangential / (cos(phi_n) * cos(psi))
    return ToothForces(
        tangential_n=tangential,
        radial_n=resultant * sin(phi_n),
        axial_n=tangential * tan(psi),
        resultant_n=resultant,
    )


def bevel_forces(
    *,
    torque_nmm: float,
    mean_pitch_diameter_mm: float,
    pitch_angle_deg: float,
    pressure_angle_deg: float = 20.0,
) -> ToothForces:
    """Straight bevel forces at the mean radius, Shigley eq. 13-36 and 13-37.

    The radial and thrust components exchange roles between the pinion and the
    gear because their pitch angles are complementary, so each member must be
    resolved at its own pitch angle.
    """
    gamma = radians(require_positive(pitch_angle_deg, "pitch_angle_deg"))
    phi = radians(require_positive(pressure_angle_deg, "pressure_angle_deg"))
    tangential = tangential_force_n(torque_nmm, mean_pitch_diameter_mm)
    radial = tangential * tan(phi) * cos(gamma)
    axial = tangential * tan(phi) * sin(gamma)
    return ToothForces(
        tangential_n=tangential,
        radial_n=radial,
        axial_n=axial,
        resultant_n=sqrt(tangential**2 + radial**2 + axial**2),
    )


def bevel_pitch_angles_deg(pinion_teeth: int, gear_teeth: int) -> tuple[float, float]:
    """Pitch angles of a ninety-degree bevel pair, Shigley eq. 13-14 and 13-15."""
    pinion = _require_teeth(pinion_teeth)
    gear = _require_teeth(gear_teeth)
    return (degrees(atan2(pinion, gear)), degrees(atan2(gear, pinion)))


# --- Chapter 15, worm gearing ----------------------------------------------


@dataclass(frozen=True)
class WormGeometry:
    lead_mm: float
    lead_angle_deg: float
    worm_pitch_diameter_mm: float
    gear_pitch_diameter_mm: float
    centre_distance_mm: float
    ratio: float


def worm_geometry(
    *,
    axial_module_mm: float,
    worm_starts: int,
    gear_teeth: int,
    worm_pitch_diameter_mm: float,
) -> WormGeometry:
    """Worm and wheel geometry, Shigley eq. 13-27 and 13-28.

    The lead angle governs everything that matters about a worm drive: its
    ratio, its efficiency, and whether it back-drives.
    """
    module = require_positive(axial_module_mm, "axial_module_mm")
    starts = _require_teeth(worm_starts)
    teeth = _require_teeth(gear_teeth)
    worm_diameter = require_positive(
        worm_pitch_diameter_mm, "worm_pitch_diameter_mm"
    )
    lead = pi * module * starts
    lead_angle = atan(lead / (pi * worm_diameter))
    gear_diameter = module * teeth
    return WormGeometry(
        lead_mm=lead,
        lead_angle_deg=degrees(lead_angle),
        worm_pitch_diameter_mm=worm_diameter,
        gear_pitch_diameter_mm=gear_diameter,
        centre_distance_mm=(worm_diameter + gear_diameter) / 2.0,
        ratio=teeth / starts,
    )


def worm_efficiency(
    *,
    lead_angle_deg: float,
    friction_coefficient: float,
    normal_pressure_angle_deg: float = 20.0,
) -> float:
    """Worm-gear efficiency, Shigley eq. 13-46.

    e = (cos phi_n - f tan lambda) / (cos phi_n + f cot lambda). Efficiency rises
    steeply with lead angle, which is why a single-start worm is both the
    highest-ratio and the least efficient choice.
    """
    lam = radians(require_positive(lead_angle_deg, "lead_angle_deg"))
    friction = require_non_negative(friction_coefficient, "friction_coefficient")
    phi_n = radians(
        require_positive(normal_pressure_angle_deg, "normal_pressure_angle_deg")
    )
    numerator = cos(phi_n) - friction * tan(lam)
    denominator = cos(phi_n) + friction / tan(lam)
    if denominator <= 0.0:
        return 0.0
    return max(numerator / denominator, 0.0)


def worm_self_locking(
    *, lead_angle_deg: float, friction_coefficient: float
) -> bool:
    """Whether the drive resists back-driving.

    A worm is self-locking when tan lambda is below the friction coefficient.
    The text is explicit that this must never be treated as a brake, because the
    static coefficient falls under vibration and the drive can release.
    """
    lam = radians(require_positive(lead_angle_deg, "lead_angle_deg"))
    return tan(lam) < require_non_negative(
        friction_coefficient, "friction_coefficient"
    )


def worm_forces(
    *,
    worm_torque_nmm: float,
    worm_pitch_diameter_mm: float,
    lead_angle_deg: float,
    friction_coefficient: float,
    normal_pressure_angle_deg: float = 20.0,
) -> tuple[ToothForces, ToothForces]:
    """Worm and wheel force components, Shigley eq. 13-41 through 13-43.

    Returns the force on the worm and the force on the wheel. Friction is not a
    correction here: at a small lead angle it carries most of the load, which is
    why a worm drive runs hot.
    """
    lam = radians(require_positive(lead_angle_deg, "lead_angle_deg"))
    friction = require_non_negative(friction_coefficient, "friction_coefficient")
    phi_n = radians(
        require_positive(normal_pressure_angle_deg, "normal_pressure_angle_deg")
    )
    worm_tangential = tangential_force_n(worm_torque_nmm, worm_pitch_diameter_mm)

    # W^x on the worm is its tangential (driving) force.
    resultant = worm_tangential / (cos(phi_n) * sin(lam) + friction * cos(lam))
    worm_axial = resultant * (cos(phi_n) * cos(lam) - friction * sin(lam))
    radial = resultant * sin(phi_n)

    on_worm = ToothForces(
        tangential_n=worm_tangential,
        radial_n=radial,
        axial_n=worm_axial,
        resultant_n=resultant,
    )
    # The wheel sees the worm's axial force as its tangential force and vice
    # versa, which is the whole mechanism of the ninety-degree crossed axes.
    on_wheel = ToothForces(
        tangential_n=worm_axial,
        radial_n=radial,
        axial_n=worm_tangential,
        resultant_n=resultant,
    )
    return (on_worm, on_wheel)


# --- Chapter 14, rating equations in general form --------------------------


def lewis_bending_stress_mpa(
    *,
    tangential_force_n: float,
    face_width_mm: float,
    module_mm: float,
    lewis_form_factor: float,
) -> float:
    """The Lewis equation, Shigley eq. 14-2 in metric form.

    sigma = W_t / (b m Y). Superseded by the AGMA method for rating, but it is
    the relation the AGMA bending equation is still recognisably built on, and
    it is exact for a static tooth treated as a cantilever.
    """
    return abs(tangential_force_n) / (
        require_positive(face_width_mm, "face_width_mm")
        * require_positive(module_mm, "module_mm")
        * require_positive(lewis_form_factor, "lewis_form_factor")
    )


def agma_bending_stress_mpa(
    *,
    tangential_force_n: float,
    face_width_mm: float,
    transverse_module_mm: float,
    geometry_factor_j: float,
    overload_factor: float = 1.0,
    dynamic_factor: float = 1.0,
    size_factor: float = 1.0,
    load_distribution_factor: float = 1.0,
    rim_thickness_factor: float = 1.0,
) -> float:
    """AGMA bending stress, Shigley eq. 14-15 in SI form.

    The factors are taken as arguments rather than computed here because their
    verified spur implementations already exist in `gear_sizing.factors`.
    """
    return (
        abs(tangential_force_n)
        * require_positive(overload_factor, "overload_factor")
        * require_positive(dynamic_factor, "dynamic_factor")
        * require_positive(size_factor, "size_factor")
        / (
            require_positive(face_width_mm, "face_width_mm")
            * require_positive(transverse_module_mm, "transverse_module_mm")
        )
        * require_positive(load_distribution_factor, "load_distribution_factor")
        * require_positive(rim_thickness_factor, "rim_thickness_factor")
        / require_positive(geometry_factor_j, "geometry_factor_j")
    )


def agma_contact_stress_mpa(
    *,
    tangential_force_n: float,
    face_width_mm: float,
    pinion_pitch_diameter_mm: float,
    geometry_factor_i: float,
    elastic_coefficient: float,
    overload_factor: float = 1.0,
    dynamic_factor: float = 1.0,
    size_factor: float = 1.0,
    load_distribution_factor: float = 1.0,
    surface_condition_factor: float = 1.0,
) -> float:
    """AGMA contact stress, Shigley eq. 14-16 in SI form."""
    return require_positive(elastic_coefficient, "elastic_coefficient") * sqrt(
        abs(tangential_force_n)
        * require_positive(overload_factor, "overload_factor")
        * require_positive(dynamic_factor, "dynamic_factor")
        * require_positive(size_factor, "size_factor")
        * require_positive(load_distribution_factor, "load_distribution_factor")
        * require_positive(surface_condition_factor, "surface_condition_factor")
        / (
            require_positive(
                pinion_pitch_diameter_mm, "pinion_pitch_diameter_mm"
            )
            * require_positive(face_width_mm, "face_width_mm")
            * require_positive(geometry_factor_i, "geometry_factor_i")
        )
    )


def external_geometry_factor_i(
    *, gear_ratio_m: float, transverse_pressure_angle_deg: float = 20.0
) -> float:
    """Surface-strength geometry factor I for external gears, Shigley eq. 14-23.

    Exact for spur teeth, where the load-sharing ratio is unity.
    """
    ratio = require_positive(gear_ratio_m, "gear_ratio_m")
    phi = radians(
        require_positive(
            transverse_pressure_angle_deg, "transverse_pressure_angle_deg"
        )
    )
    return cos(phi) * sin(phi) / 2.0 * ratio / (ratio + 1.0)


def _require_teeth(value: object) -> float:
    number = require_positive(value, "teeth")
    if abs(number - round(number)) > 1e-9:
        raise MechanicsError(
            "input.not_an_integer",
            "a tooth count must be a whole number",
            {"teeth": value},
        )
    return float(round(number))


__all__ = [
    "FULL_DEPTH_ADDENDUM_CONSTANT",
    "STUB_ADDENDUM_CONSTANT",
    "HelicalGeometry",
    "ToothForces",
    "WormGeometry",
    "agma_bending_stress_mpa",
    "agma_contact_stress_mpa",
    "bevel_forces",
    "bevel_pitch_angles_deg",
    "circular_pitch_mm",
    "contact_ratio",
    "external_geometry_factor_i",
    "gear_ratio",
    "helical_forces",
    "helical_geometry",
    "largest_gear_without_interference",
    "lewis_bending_stress_mpa",
    "module_from_diametral_pitch",
    "pitch_diameter_mm",
    "planetary_train_value",
    "smallest_pinion_with_rack",
    "smallest_pinion_without_interference",
    "spur_forces",
    "tangential_force_n",
    "train_value",
    "worm_efficiency",
    "worm_forces",
    "worm_geometry",
    "worm_self_locking",
]
