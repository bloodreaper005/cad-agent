"""Rolling-contact bearings.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapter 11.

A rolling bearing does not have a life, it has a life distribution, and the
whole chapter is about that distinction. Everything here returns a rated life or
a required capacity at a stated reliability, never a single number that hides
which reliability it belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf, log

from .errors import MechanicsError, require_non_negative, require_positive


# Equation 11-1 life exponent. Point contact in a ball bearing wears to the
# cube of load; line contact in a roller bearing is less load-sensitive.
LIFE_EXPONENTS: dict[str, float] = {
    "ball": 3.0,
    "roller": 10.0 / 3.0,
}

# Table 11-1 radial and thrust factors. The deep-groove e and Y depend on
# F_a/C_0, which is unknown until a specific bearing is chosen, so the values
# here are mid-range and a result computed with them is a required capacity and
# never a selection.
BEARING_FAMILIES: dict[str, dict[str, float]] = {
    "deep_groove_ball": {"exponent": 3.0, "x1": 1.0, "y1": 0.0, "x2": 0.56, "y2": 1.5, "e": 0.30},
    "angular_contact_ball": {"exponent": 3.0, "x1": 1.0, "y1": 0.0, "x2": 0.41, "y2": 0.87, "e": 1.14},
    "cylindrical_roller": {"exponent": 10.0 / 3.0, "x1": 1.0, "y1": 0.0, "x2": 1.0, "y2": 0.0, "e": inf},
    "tapered_roller": {"exponent": 10.0 / 3.0, "x1": 1.0, "y1": 0.0, "x2": 0.40, "y2": 1.60, "e": 0.37},
}

# Table 11-6, Weibull parameters for the two manufacturers the text tabulates.
# These shape the reliability correction and are the reason a 99 percent life is
# far shorter than a tenth of the 90 percent life.
WEIBULL_PARAMETERS: dict[str, tuple[float, float, float]] = {
    # name: (x_0 guaranteed life, theta characteristic, b shape)
    "manufacturer_1": (0.0, 4.48, 1.5),
    "manufacturer_2": (0.02, 4.459, 1.483),
}

# Table 11-5, application factor a_f for shock and duty.
APPLICATION_FACTORS: dict[str, float] = {
    "precision_gearing": 1.1,
    "commercial_gearing": 1.2,
    "poor_gearing": 1.5,
    "light_impact": 1.5,
    "moderate_impact": 2.0,
    "heavy_impact": 3.0,
    "uniform": 1.0,
}


def family(name: str) -> dict[str, float]:
    try:
        return dict(BEARING_FAMILIES[name])
    except KeyError:
        raise MechanicsError(
            "input.unknown_bearing",
            f"unknown bearing family {name!r}",
            {"supported": sorted(BEARING_FAMILIES)},
        ) from None


def equivalent_radial_load_n(
    *,
    radial_n: float,
    axial_n: float = 0.0,
    bearing_family: str = "deep_groove_ball",
    rotation_factor: float = 1.0,
) -> float:
    """Equivalent radial load P = X V F_r + Y F_a, Shigley eq. 11-12.

    The rotation factor V is 1.0 for a rotating inner ring and 1.2 for a
    rotating outer ring, which is a real and often forgotten penalty.
    """
    values = family(bearing_family)
    radial = require_non_negative(radial_n, "radial_n")
    axial = require_non_negative(axial_n, "axial_n")
    rotation = require_positive(rotation_factor, "rotation_factor")
    if radial <= 0.0:
        return values["y2"] * axial
    if axial / (rotation * radial) <= values["e"]:
        return values["x1"] * rotation * radial + values["y1"] * axial
    return values["x2"] * rotation * radial + values["y2"] * axial


def life_revolutions(
    *, dynamic_capacity_n: float, equivalent_load_n: float, bearing_family: str = "deep_groove_ball"
) -> float:
    """Basic rating life in revolutions, Shigley eq. 11-3.

    L_10 is the life ninety percent of a population exceeds, expressed here in
    revolutions rather than the millions the catalogues use.
    """
    values = family(bearing_family)
    capacity = require_positive(dynamic_capacity_n, "dynamic_capacity_n")
    load = require_positive(equivalent_load_n, "equivalent_load_n")
    return 1.0e6 * (capacity / load) ** values["exponent"]


def life_hours(
    *,
    dynamic_capacity_n: float,
    equivalent_load_n: float,
    speed_rpm: float,
    bearing_family: str = "deep_groove_ball",
) -> float:
    revolutions = life_revolutions(
        dynamic_capacity_n=dynamic_capacity_n,
        equivalent_load_n=equivalent_load_n,
        bearing_family=bearing_family,
    )
    return revolutions / (60.0 * require_positive(speed_rpm, "speed_rpm"))


def reliability_life_factor(
    reliability: float, *, parameters: str = "manufacturer_2"
) -> float:
    """Weibull life factor, Shigley eq. 11-18 rearranged.

    Returns the multiple of the rated L_10 life reached at the requested
    reliability, which is always at or below one for a reliability above 0.90.
    """
    value = float(reliability)
    if not 0.0 < value < 1.0:
        raise MechanicsError(
            "input.out_of_range",
            "reliability must lie strictly between zero and one",
            {"reliability": value},
        )
    try:
        x0, theta, b = WEIBULL_PARAMETERS[parameters]
    except KeyError:
        raise MechanicsError(
            "input.unknown_parameters",
            f"unknown Weibull parameter set {parameters!r}",
            {"supported": sorted(WEIBULL_PARAMETERS)},
        ) from None
    return x0 + (theta - x0) * (-log(value)) ** (1.0 / b)


@dataclass(frozen=True)
class BearingRating:
    bearing_family: str
    equivalent_load_n: float
    application_factor: float
    reliability: float
    life_factor: float
    required_dynamic_capacity_n: float
    design_life_hours: float
    speed_rpm: float

    def as_dict(self) -> dict[str, float | str]:
        return {
            "bearing_family": self.bearing_family,
            "equivalent_load_n": self.equivalent_load_n,
            "application_factor": self.application_factor,
            "reliability": self.reliability,
            "weibull_life_factor": self.life_factor,
            "required_dynamic_capacity_n": self.required_dynamic_capacity_n,
            "design_life_hours": self.design_life_hours,
            "speed_rpm": self.speed_rpm,
        }


def required_dynamic_capacity_n(
    *,
    radial_n: float,
    axial_n: float = 0.0,
    speed_rpm: float,
    design_life_hours: float,
    bearing_family: str = "deep_groove_ball",
    reliability: float = 0.90,
    application: str = "uniform",
    rotation_factor: float = 1.0,
    weibull_parameters: str = "manufacturer_2",
) -> BearingRating:
    """Catalogue capacity a bearing must have, Shigley eq. 11-9 and 11-19.

    The result is a requirement, not a selection. Choosing an actual bearing
    fixes C_0, which fixes e and Y, which changes the equivalent load this was
    computed from, so a real selection iterates once against a catalogue.
    """
    values = family(bearing_family)
    speed = require_positive(speed_rpm, "speed_rpm")
    hours = require_positive(design_life_hours, "design_life_hours")
    try:
        application_factor = APPLICATION_FACTORS[application]
    except KeyError:
        raise MechanicsError(
            "input.unknown_application",
            f"unknown application {application!r}",
            {"supported": sorted(APPLICATION_FACTORS)},
        ) from None

    equivalent = equivalent_radial_load_n(
        radial_n=radial_n,
        axial_n=axial_n,
        bearing_family=bearing_family,
        rotation_factor=rotation_factor,
    )
    if equivalent <= 0.0:
        raise MechanicsError(
            "input.incomplete", "the bearing carries no load", {}
        )

    # Design life expressed in millions of revolutions, the unit a catalogue
    # rating is defined in.
    millions = 60.0 * speed * hours / 1.0e6
    life_factor = (
        1.0
        if abs(reliability - 0.90) < 1e-12
        else reliability_life_factor(reliability, parameters=weibull_parameters)
    )
    if abs(reliability - 0.90) < 1e-12:
        adjusted = millions
    else:
        reference = reliability_life_factor(0.90, parameters=weibull_parameters)
        adjusted = millions * reference / life_factor

    capacity = application_factor * equivalent * adjusted ** (1.0 / values["exponent"])
    return BearingRating(
        bearing_family=bearing_family,
        equivalent_load_n=equivalent,
        application_factor=application_factor,
        reliability=float(reliability),
        life_factor=life_factor,
        required_dynamic_capacity_n=capacity,
        design_life_hours=hours,
        speed_rpm=speed,
    )


__all__ = [
    "APPLICATION_FACTORS",
    "BEARING_FAMILIES",
    "LIFE_EXPONENTS",
    "WEIBULL_PARAMETERS",
    "BearingRating",
    "equivalent_radial_load_n",
    "family",
    "life_hours",
    "life_revolutions",
    "reliability_life_factor",
    "required_dynamic_capacity_n",
]
