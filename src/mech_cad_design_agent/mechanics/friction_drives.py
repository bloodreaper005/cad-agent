"""Clutches, brakes, couplings and flywheels, and flexible mechanical elements.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapters 16 and 17.

The two chapters share one relation, the belt equation F_1/F_2 = exp(f theta),
which governs a band brake and a flat belt alike, so they are implemented
together rather than duplicated. The uniform-wear and uniform-pressure clutch
models are both provided because they bracket the truth: a new clutch is closer
to uniform pressure and a worn one to uniform wear, and uniform wear gives the
lower capacity, which is why the text designs to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, inf, pi, radians, sin, sqrt

from .errors import MechanicsError, require_non_negative, require_positive


# --- Chapter 16, axial clutches and brakes ---------------------------------


@dataclass(frozen=True)
class ClutchResult:
    model: str
    axial_force_n: float
    torque_capacity_nmm: float
    maximum_pressure_mpa: float
    mean_radius_mm: float

    def as_dict(self) -> dict[str, float | str]:
        return {
            "model": self.model,
            "axial_force_n": self.axial_force_n,
            "torque_capacity_nmm": self.torque_capacity_nmm,
            "maximum_pressure_mpa": self.maximum_pressure_mpa,
            "mean_radius_mm": self.mean_radius_mm,
        }


def _annulus(outer_diameter_mm: float, inner_diameter_mm: float) -> tuple[float, float]:
    outer = require_positive(outer_diameter_mm, "outer_diameter_mm")
    inner = require_positive(inner_diameter_mm, "inner_diameter_mm")
    if inner >= outer:
        raise MechanicsError(
            "input.out_of_range",
            "the inner diameter must be smaller than the outer diameter",
            {"outer_diameter_mm": outer, "inner_diameter_mm": inner},
        )
    return (outer, inner)


def uniform_wear_clutch(
    *,
    outer_diameter_mm: float,
    inner_diameter_mm: float,
    maximum_pressure_mpa: float,
    friction_coefficient: float,
    surfaces: int = 1,
) -> ClutchResult:
    """Uniform-wear disk clutch, Shigley eq. 16-23 and 16-24.

    Wear is proportional to pressure times velocity, so a worn surface carries
    constant p r and the peak pressure sits at the inner radius. This model
    gives the lower torque of the two and is the one to design to.
    """
    outer, inner = _annulus(outer_diameter_mm, inner_diameter_mm)
    pressure = require_positive(maximum_pressure_mpa, "maximum_pressure_mpa")
    friction = require_positive(friction_coefficient, "friction_coefficient")
    count = require_positive(surfaces, "surfaces")
    force = pi * pressure * inner / 2.0 * (outer - inner)
    torque = count * friction * force * (outer + inner) / 4.0
    return ClutchResult(
        model="uniform_wear",
        axial_force_n=force,
        torque_capacity_nmm=torque,
        maximum_pressure_mpa=pressure,
        mean_radius_mm=(outer + inner) / 4.0,
    )


def uniform_pressure_clutch(
    *,
    outer_diameter_mm: float,
    inner_diameter_mm: float,
    maximum_pressure_mpa: float,
    friction_coefficient: float,
    surfaces: int = 1,
) -> ClutchResult:
    """Uniform-pressure disk clutch, Shigley eq. 16-26 and 16-27.

    Appropriate to a new, rigid, well-aligned clutch before any wear has
    redistributed the pressure.
    """
    outer, inner = _annulus(outer_diameter_mm, inner_diameter_mm)
    pressure = require_positive(maximum_pressure_mpa, "maximum_pressure_mpa")
    friction = require_positive(friction_coefficient, "friction_coefficient")
    count = require_positive(surfaces, "surfaces")
    force = pi * pressure / 4.0 * (outer**2 - inner**2)
    torque = (
        count
        * friction
        * force
        / 3.0
        * (outer**3 - inner**3)
        / (outer**2 - inner**2)
    )
    return ClutchResult(
        model="uniform_pressure",
        axial_force_n=force,
        torque_capacity_nmm=torque,
        maximum_pressure_mpa=pressure,
        mean_radius_mm=(outer**3 - inner**3) / (3.0 * (outer**2 - inner**2)),
    )


def optimum_inner_diameter_mm(outer_diameter_mm: float) -> float:
    """Inner diameter that maximises uniform-wear torque, Shigley eq. 16-25.

    d = D / sqrt(3). A narrower annulus has too little area and a wider one puts
    material where the radius is small, and the optimum falls out of setting the
    derivative of the torque to zero.
    """
    return require_positive(outer_diameter_mm, "outer_diameter_mm") / sqrt(3.0)


# --- Chapter 16, band brakes; chapter 17, belts ----------------------------


def belt_tension_ratio(
    *,
    friction_coefficient: float,
    wrap_angle_deg: float,
    groove_angle_deg: float | None = None,
) -> float:
    """F_1/F_2 = exp(f theta), Shigley eq. 16-2 and 17-7.

    A V belt wedges into its groove, which multiplies the effective friction by
    1/sin(alpha/2). That factor, not the rubber, is why a V belt carries several
    times the load of a flat belt on the same pulleys.
    """
    friction = require_non_negative(friction_coefficient, "friction_coefficient")
    wrap = radians(require_positive(wrap_angle_deg, "wrap_angle_deg"))
    if groove_angle_deg is None:
        effective = friction
    else:
        half = radians(require_positive(groove_angle_deg, "groove_angle_deg")) / 2.0
        if sin(half) <= 0.0:
            raise MechanicsError(
                "input.out_of_range",
                "groove angle must be between zero and one hundred eighty degrees",
                {"groove_angle_deg": groove_angle_deg},
            )
        effective = friction / sin(half)
    return exp(effective * wrap)


def band_brake_torque_nmm(
    *,
    tight_side_tension_n: float,
    friction_coefficient: float,
    wrap_angle_deg: float,
    drum_diameter_mm: float,
) -> float:
    """Band-brake torque, Shigley eq. 16-11."""
    ratio = belt_tension_ratio(
        friction_coefficient=friction_coefficient, wrap_angle_deg=wrap_angle_deg
    )
    tight = require_positive(tight_side_tension_n, "tight_side_tension_n")
    slack = tight / ratio
    return (tight - slack) * require_positive(drum_diameter_mm, "drum_diameter_mm") / 2.0


@dataclass(frozen=True)
class BeltDrive:
    wrap_angle_small_deg: float
    wrap_angle_large_deg: float
    belt_length_mm: float
    belt_speed_m_per_s: float
    centrifugal_tension_n: float
    tension_ratio: float
    tight_side_n: float
    slack_side_n: float
    transmitted_power_w: float

    def as_dict(self) -> dict[str, float]:
        return {
            "wrap_angle_small_deg": self.wrap_angle_small_deg,
            "wrap_angle_large_deg": self.wrap_angle_large_deg,
            "belt_length_mm": self.belt_length_mm,
            "belt_speed_m_per_s": self.belt_speed_m_per_s,
            "centrifugal_tension_n": self.centrifugal_tension_n,
            "tension_ratio": self.tension_ratio,
            "tight_side_n": self.tight_side_n,
            "slack_side_n": self.slack_side_n,
            "transmitted_power_w": self.transmitted_power_w,
        }


def open_belt_geometry(
    *,
    small_pulley_diameter_mm: float,
    large_pulley_diameter_mm: float,
    centre_distance_mm: float,
) -> tuple[float, float, float]:
    """Wrap angles and belt length for an open drive, Shigley eq. 17-1 and 17-2."""
    small = require_positive(small_pulley_diameter_mm, "small_pulley_diameter_mm")
    large = require_positive(large_pulley_diameter_mm, "large_pulley_diameter_mm")
    if large < small:
        small, large = large, small
    centre = require_positive(centre_distance_mm, "centre_distance_mm")
    if centre <= (large - small) / 2.0:
        raise MechanicsError(
            "input.out_of_range",
            "the pulleys overlap at this centre distance",
            {"centre_distance_mm": centre},
        )
    from math import asin, degrees

    half = (large - small) / (2.0 * centre)
    if abs(half) > 1.0:
        raise MechanicsError(
            "input.out_of_range",
            "no open belt can wrap these pulleys at this centre distance",
            {"centre_distance_mm": centre},
        )
    delta = asin(half)
    theta_small = pi - 2.0 * delta
    theta_large = pi + 2.0 * delta
    length = (
        sqrt(max(4.0 * centre**2 - (large - small) ** 2, 0.0))
        + (large * theta_large + small * theta_small) / 2.0
    )
    return (degrees(theta_small), degrees(theta_large), length)


def evaluate_belt_drive(
    *,
    power_w: float,
    small_pulley_diameter_mm: float,
    large_pulley_diameter_mm: float,
    centre_distance_mm: float,
    small_pulley_rpm: float,
    friction_coefficient: float = 0.30,
    mass_per_length_kg_per_m: float = 0.0,
    groove_angle_deg: float | None = None,
) -> BeltDrive:
    """Size the tensions a belt drive needs, Shigley eq. 17-8 through 17-12.

    Centrifugal tension is carried explicitly because it contributes nothing to
    the transmitted power while consuming belt strength, and it is what limits a
    fast drive.
    """
    power = require_positive(power_w, "power_w")
    small = require_positive(small_pulley_diameter_mm, "small_pulley_diameter_mm")
    rpm = require_positive(small_pulley_rpm, "small_pulley_rpm")
    wrap_small, wrap_large, length = open_belt_geometry(
        small_pulley_diameter_mm=small,
        large_pulley_diameter_mm=large_pulley_diameter_mm,
        centre_distance_mm=centre_distance_mm,
    )
    speed = pi * (small / 1000.0) * rpm / 60.0
    centrifugal = (
        require_non_negative(mass_per_length_kg_per_m, "mass_per_length_kg_per_m")
        * speed**2
    )
    # The smaller pulley has the smaller wrap and therefore governs slip.
    ratio = belt_tension_ratio(
        friction_coefficient=friction_coefficient,
        wrap_angle_deg=wrap_small,
        groove_angle_deg=groove_angle_deg,
    )
    if speed <= 0.0:
        raise MechanicsError("input.out_of_range", "the belt is not moving", {})
    difference = power / speed
    if ratio <= 1.0:
        raise MechanicsError(
            "input.out_of_range",
            "no tension ratio is available, so the belt cannot transmit power",
            {"tension_ratio": ratio},
        )
    slack = difference / (ratio - 1.0) + centrifugal
    tight = difference + slack
    return BeltDrive(
        wrap_angle_small_deg=wrap_small,
        wrap_angle_large_deg=wrap_large,
        belt_length_mm=length,
        belt_speed_m_per_s=speed,
        centrifugal_tension_n=centrifugal,
        tension_ratio=ratio,
        tight_side_n=tight,
        slack_side_n=slack,
        transmitted_power_w=power,
    )


# --- Chapter 16, flywheels --------------------------------------------------


def flywheel_inertia_kg_m2(
    *,
    energy_fluctuation_j: float,
    mean_speed_rad_per_s: float,
    coefficient_of_speed_fluctuation: float,
) -> float:
    """Required flywheel inertia, Shigley eq. 16-64.

    I = E / (C_s omega^2). The coefficient of speed fluctuation is a design
    choice, not a property: tightening it from 0.05 to 0.01 multiplies the
    required inertia by five.
    """
    energy = require_positive(energy_fluctuation_j, "energy_fluctuation_j")
    omega = require_positive(mean_speed_rad_per_s, "mean_speed_rad_per_s")
    coefficient = require_positive(
        coefficient_of_speed_fluctuation, "coefficient_of_speed_fluctuation"
    )
    return energy / (coefficient * omega**2)


def kinetic_energy_j(inertia_kg_m2: float, speed_rad_per_s: float) -> float:
    return (
        0.5
        * require_positive(inertia_kg_m2, "inertia_kg_m2")
        * require_positive(speed_rad_per_s, "speed_rad_per_s") ** 2
    )


def braking_temperature_rise_c(
    *, energy_j: float, mass_kg: float, specific_heat_j_per_kg_c: float = 500.0
) -> float:
    """Bulk temperature rise of a brake absorbing one stop, Shigley eq. 16-65.

    Assumes every joule lands in the stated mass and none is lost, which is the
    right assumption for a single stop and badly wrong for repeated ones.
    """
    return require_positive(energy_j, "energy_j") / (
        require_positive(mass_kg, "mass_kg")
        * require_positive(specific_heat_j_per_kg_c, "specific_heat_j_per_kg_c")
    )


__all__ = [
    "BeltDrive",
    "ClutchResult",
    "band_brake_torque_nmm",
    "belt_tension_ratio",
    "braking_temperature_rise_c",
    "evaluate_belt_drive",
    "flywheel_inertia_kg_m2",
    "kinetic_energy_j",
    "open_belt_geometry",
    "optimum_inner_diameter_mm",
    "uniform_pressure_clutch",
    "uniform_wear_clutch",
]
