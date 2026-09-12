"""Lubrication and journal bearings.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapter 12.

Scope, stated plainly. The design quantities of a hydrodynamic bearing, the
minimum film thickness, the attitude angle, the friction variable, the flow and
side-leakage ratios and the peak film pressure, are read in the text from the
Raimondi and Boyd charts of figures 12-16 through 12-24. Those charts are
numerical solutions of the Reynolds equation for finite bearings and cannot be
reproduced from their published form. This module therefore implements the
closed-form relations the chapter states as equations, and takes the chart
variables as arguments where they are needed rather than inventing fits for
them.

What that leaves is still most of what a first pass needs: the Petroff estimate,
the Sommerfeld number that indexes every chart, the viscosity and clearance
relations, and the power and temperature consequences.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import pi

from .errors import MechanicsError, require_non_negative, require_positive


def radial_clearance_mm(journal_diameter_mm: float, bearing_diameter_mm: float) -> float:
    """c = (D - d) / 2, the clearance every other quantity is scaled by."""
    journal = require_positive(journal_diameter_mm, "journal_diameter_mm")
    bearing = require_positive(bearing_diameter_mm, "bearing_diameter_mm")
    if bearing <= journal:
        raise MechanicsError(
            "input.out_of_range",
            "the bearing bore must exceed the journal diameter",
            {"journal_diameter_mm": journal, "bearing_diameter_mm": bearing},
        )
    return (bearing - journal) / 2.0


def unit_load_mpa(
    *, radial_load_n: float, journal_diameter_mm: float, length_mm: float
) -> float:
    """P = W / (l d), the projected-area pressure, Shigley eq. 12-7."""
    return require_positive(radial_load_n, "radial_load_n") / (
        require_positive(length_mm, "length_mm")
        * require_positive(journal_diameter_mm, "journal_diameter_mm")
    )


def sommerfeld_number(
    *,
    journal_radius_mm: float,
    radial_clearance_mm: float,
    viscosity_pa_s: float,
    speed_rev_per_s: float,
    unit_load_mpa: float,
) -> float:
    """The bearing characteristic number S, Shigley eq. 12-7.

    S = (r/c)^2 * mu * N / P. Every Raimondi and Boyd chart is plotted against
    it, so it is the single quantity that indexes a bearing's behaviour.
    Viscosity is in pascal-seconds and the unit load in megapascals, so the
    ratio carries a factor of one million.
    """
    radius = require_positive(journal_radius_mm, "journal_radius_mm")
    clearance = require_positive(radial_clearance_mm, "radial_clearance_mm")
    viscosity = require_positive(viscosity_pa_s, "viscosity_pa_s")
    speed = require_positive(speed_rev_per_s, "speed_rev_per_s")
    load = require_positive(unit_load_mpa, "unit_load_mpa")
    return (radius / clearance) ** 2 * viscosity * speed / (load * 1.0e6)


def petroff_friction_coefficient(
    *,
    journal_radius_mm: float,
    radial_clearance_mm: float,
    viscosity_pa_s: float,
    speed_rev_per_s: float,
    unit_load_mpa: float,
) -> float:
    """Petroff's equation, Shigley eq. 12-6.

    f = 2 pi^2 (mu N / P)(r/c). Exact only for a concentric, lightly loaded
    journal, but it gives a quick and honest estimate of friction and it is
    where the bearing characteristic number came from.
    """
    radius = require_positive(journal_radius_mm, "journal_radius_mm")
    clearance = require_positive(radial_clearance_mm, "radial_clearance_mm")
    viscosity = require_positive(viscosity_pa_s, "viscosity_pa_s")
    speed = require_positive(speed_rev_per_s, "speed_rev_per_s")
    load = require_positive(unit_load_mpa, "unit_load_mpa")
    return 2.0 * pi**2 * viscosity * speed / (load * 1.0e6) * (radius / clearance)


def friction_torque_nmm(
    *, friction_coefficient: float, radial_load_n: float, journal_radius_mm: float
) -> float:
    return (
        require_non_negative(friction_coefficient, "friction_coefficient")
        * require_positive(radial_load_n, "radial_load_n")
        * require_positive(journal_radius_mm, "journal_radius_mm")
    )


def friction_power_w(
    *,
    friction_coefficient: float,
    radial_load_n: float,
    journal_radius_mm: float,
    speed_rev_per_s: float,
) -> float:
    """Power lost to friction, which is the heat the bearing must reject."""
    torque = friction_torque_nmm(
        friction_coefficient=friction_coefficient,
        radial_load_n=radial_load_n,
        journal_radius_mm=journal_radius_mm,
    )
    omega = 2.0 * pi * require_positive(speed_rev_per_s, "speed_rev_per_s")
    return torque * omega / 1000.0


def minimum_film_thickness_mm(
    *, radial_clearance_mm: float, eccentricity_ratio: float
) -> float:
    """h_0 = c (1 - epsilon), Shigley eq. 12-8.

    The eccentricity ratio is read from figure 12-16 against the Sommerfeld
    number; it is an argument here for the reason given in the module docstring.
    """
    clearance = require_positive(radial_clearance_mm, "radial_clearance_mm")
    ratio = require_non_negative(eccentricity_ratio, "eccentricity_ratio")
    if ratio >= 1.0:
        raise MechanicsError(
            "input.out_of_range",
            "an eccentricity ratio of one means metal-to-metal contact",
            {"eccentricity_ratio": ratio},
        )
    return clearance * (1.0 - ratio)


@dataclass(frozen=True)
class JournalBearingEstimate:
    unit_load_mpa: float
    sommerfeld_number: float
    radial_clearance_mm: float
    petroff_friction_coefficient: float
    friction_power_w: float
    length_to_diameter_ratio: float
    chart_variables_required: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "unit_load_mpa": self.unit_load_mpa,
            "sommerfeld_number": self.sommerfeld_number,
            "radial_clearance_mm": self.radial_clearance_mm,
            "petroff_friction_coefficient": self.petroff_friction_coefficient,
            "friction_power_w": self.friction_power_w,
            "length_to_diameter_ratio": self.length_to_diameter_ratio,
            "chart_variables_required": list(self.chart_variables_required),
        }


def estimate(
    *,
    radial_load_n: float,
    journal_diameter_mm: float,
    bearing_diameter_mm: float,
    length_mm: float,
    viscosity_pa_s: float,
    speed_rev_per_s: float,
) -> JournalBearingEstimate:
    """A first-pass hydrodynamic bearing estimate.

    Returns the quantities that follow in closed form, and names the chart
    variables a full Raimondi and Boyd solution would still require, so that the
    boundary between what was computed and what was not is explicit in the
    result rather than left to the reader.
    """
    clearance = radial_clearance_mm(journal_diameter_mm, bearing_diameter_mm)
    load = unit_load_mpa(
        radial_load_n=radial_load_n,
        journal_diameter_mm=journal_diameter_mm,
        length_mm=length_mm,
    )
    radius = journal_diameter_mm / 2.0
    common = dict(
        journal_radius_mm=radius,
        radial_clearance_mm=clearance,
        viscosity_pa_s=viscosity_pa_s,
        speed_rev_per_s=speed_rev_per_s,
        unit_load_mpa=load,
    )
    friction = petroff_friction_coefficient(**common)
    return JournalBearingEstimate(
        unit_load_mpa=load,
        sommerfeld_number=sommerfeld_number(**common),
        radial_clearance_mm=clearance,
        petroff_friction_coefficient=friction,
        friction_power_w=friction_power_w(
            friction_coefficient=friction,
            radial_load_n=radial_load_n,
            journal_radius_mm=radius,
            speed_rev_per_s=speed_rev_per_s,
        ),
        length_to_diameter_ratio=length_mm / journal_diameter_mm,
        chart_variables_required=(
            "minimum_film_thickness_variable (fig 12-16)",
            "coefficient_of_friction_variable (fig 12-18)",
            "flow_variable (fig 12-19)",
            "side_flow_ratio (fig 12-20)",
            "maximum_film_pressure_ratio (fig 12-21)",
        ),
    )


__all__ = [
    "JournalBearingEstimate",
    "estimate",
    "friction_power_w",
    "friction_torque_nmm",
    "minimum_film_thickness_mm",
    "petroff_friction_coefficient",
    "radial_clearance_mm",
    "sommerfeld_number",
    "unit_load_mpa",
]
