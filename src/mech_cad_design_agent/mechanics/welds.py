"""Welding, bonding, and the design of permanent joints.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapter 9.

Fillet welds are treated the way the chapter treats them: every stress is
referred to the throat, whatever the direction of loading, because that is the
plane a fillet weld actually fails on. The unit-property method of tables 9-1
and 9-2 is used, in which a weld group's second moment is computed for a throat
of unit width and then multiplied by the real throat.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf, pi, sqrt

from .errors import MechanicsError, require_positive

# The throat of an equal-leg fillet weld is its leg times cos 45 degrees.
THROAT_RATIO = 0.707

# Table 9-6, minimum electrode properties in MPa.
ELECTRODES: dict[str, dict[str, float]] = {
    "E60xx": {"ultimate_mpa": 427.0, "yield_mpa": 345.0},
    "E70xx": {"ultimate_mpa": 482.0, "yield_mpa": 393.0},
    "E80xx": {"ultimate_mpa": 551.0, "yield_mpa": 462.0},
    "E90xx": {"ultimate_mpa": 620.0, "yield_mpa": 531.0},
    "E100xx": {"ultimate_mpa": 689.0, "yield_mpa": 600.0},
    "E120xx": {"ultimate_mpa": 827.0, "yield_mpa": 738.0},
}

# Table 9-6, AISC allowable shear on the throat as a fraction of electrode
# tensile strength, with the matching factor of safety the code implies.
AISC_ALLOWABLE_SHEAR_FRACTION = 0.30
AISC_IMPLIED_SAFETY_FACTOR = 1.44


def electrode(name: str) -> dict[str, float]:
    try:
        return dict(ELECTRODES[name])
    except KeyError:
        raise MechanicsError(
            "input.unknown_electrode",
            f"unknown electrode {name!r}",
            {"supported": sorted(ELECTRODES)},
        ) from None


def throat_mm(leg_mm: float) -> float:
    """Effective throat of an equal-leg fillet weld, Shigley eq. 9-2."""
    return THROAT_RATIO * require_positive(leg_mm, "leg_mm")


def allowable_shear_mpa(electrode_name: str = "E70xx") -> float:
    """AISC allowable throat shear, table 9-6."""
    return AISC_ALLOWABLE_SHEAR_FRACTION * electrode(electrode_name)["ultimate_mpa"]


@dataclass(frozen=True)
class WeldGroup:
    """A fillet-weld group described by its unit properties.

    `unit_area_mm` is the weld length, and `unit_second_moment_mm3` is the second
    moment of the weld line about the axis of interest, both computed for a
    throat of unit width. Multiplying by the throat gives the real properties,
    which is exactly the table 9-2 method.
    """

    name: str
    unit_area_mm: float
    unit_second_moment_mm3: float
    extreme_fibre_mm: float
    unit_polar_second_moment_mm3: float = 0.0
    polar_radius_mm: float = 0.0

    def area_mm2(self, leg_mm: float) -> float:
        return throat_mm(leg_mm) * self.unit_area_mm

    def second_moment_mm4(self, leg_mm: float) -> float:
        return throat_mm(leg_mm) * self.unit_second_moment_mm3

    def polar_second_moment_mm4(self, leg_mm: float) -> float:
        return throat_mm(leg_mm) * self.unit_polar_second_moment_mm3


def parallel_fillets(length_mm: float, separation_mm: float) -> WeldGroup:
    """Two parallel fillet welds, table 9-2 pattern 2, bent about their centroid."""
    length = require_positive(length_mm, "length_mm")
    separation = require_positive(separation_mm, "separation_mm")
    unit_area = 2.0 * length
    unit_second_moment = length * separation**2 / 2.0
    polar = unit_area * (separation**2 + length**2) / 12.0
    return WeldGroup(
        name="two parallel fillets",
        unit_area_mm=unit_area,
        unit_second_moment_mm3=unit_second_moment,
        extreme_fibre_mm=separation / 2.0,
        unit_polar_second_moment_mm3=polar,
        polar_radius_mm=sqrt(separation**2 + length**2) / 2.0,
    )


def transverse_fillets(width_mm: float, separation_mm: float) -> WeldGroup:
    """Two transverse fillet welds, table 9-2 pattern 3."""
    width = require_positive(width_mm, "width_mm")
    separation = require_positive(separation_mm, "separation_mm")
    unit_area = 2.0 * width
    unit_second_moment = width**3 / 6.0
    return WeldGroup(
        name="two transverse fillets",
        unit_area_mm=unit_area,
        unit_second_moment_mm3=unit_second_moment,
        extreme_fibre_mm=width / 2.0,
        unit_polar_second_moment_mm3=unit_area * (width**2 + separation**2) / 12.0,
        polar_radius_mm=sqrt(width**2 + separation**2) / 2.0,
    )


def rectangular_box(width_mm: float, depth_mm: float) -> WeldGroup:
    """A fillet weld all round a rectangle, table 9-2 pattern 6."""
    b = require_positive(width_mm, "width_mm")
    d = require_positive(depth_mm, "depth_mm")
    unit_area = 2.0 * (b + d)
    unit_second_moment = d**2 * (3.0 * b + d) / 6.0
    polar = unit_area * (b**2 + d**2) / 12.0
    return WeldGroup(
        name="rectangular box",
        unit_area_mm=unit_area,
        unit_second_moment_mm3=unit_second_moment,
        extreme_fibre_mm=d / 2.0,
        unit_polar_second_moment_mm3=polar,
        polar_radius_mm=sqrt(b**2 + d**2) / 2.0,
    )


def circular_weld(diameter_mm: float) -> WeldGroup:
    """A fillet weld round a circular boss, table 9-2 pattern 8."""
    diameter = require_positive(diameter_mm, "diameter_mm")
    unit_area = pi * diameter
    unit_second_moment = pi * diameter**3 / 8.0
    return WeldGroup(
        name="circular",
        unit_area_mm=unit_area,
        unit_second_moment_mm3=unit_second_moment,
        extreme_fibre_mm=diameter / 2.0,
        unit_polar_second_moment_mm3=pi * diameter**3 / 4.0,
        polar_radius_mm=diameter / 2.0,
    )


@dataclass(frozen=True)
class WeldResult:
    primary_shear_mpa: float
    secondary_shear_mpa: float
    bending_shear_mpa: float
    resultant_shear_mpa: float
    allowable_shear_mpa: float
    factor_of_safety: float
    throat_mm: float

    @property
    def safe(self) -> bool:
        return self.factor_of_safety >= 1.0

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "primary_shear_mpa": self.primary_shear_mpa,
            "secondary_shear_mpa": self.secondary_shear_mpa,
            "bending_shear_mpa": self.bending_shear_mpa,
            "resultant_shear_mpa": self.resultant_shear_mpa,
            "allowable_shear_mpa": self.allowable_shear_mpa,
            "factor_of_safety": self.factor_of_safety,
            "throat_mm": self.throat_mm,
            "safe": self.safe,
        }


def evaluate_fillet_weld(
    *,
    group: WeldGroup,
    leg_mm: float,
    shear_force_n: float = 0.0,
    bending_moment_nmm: float = 0.0,
    torsional_moment_nmm: float = 0.0,
    electrode_name: str = "E70xx",
) -> WeldResult:
    """Combine the shear components on the throat, Shigley eq. 9-3 through 9-8.

    Primary shear is the direct load over the throat area; secondary shear comes
    from torsion about the group centroid; bending contributes a normal stress
    which the chapter converts to an equivalent throat shear. The three are
    combined as a vector sum, which is conservative because they do not in
    general peak at the same point of the weld.
    """
    leg = require_positive(leg_mm, "leg_mm")
    throat = throat_mm(leg)
    area = group.area_mm2(leg)
    if area <= 0.0:
        raise MechanicsError("input.out_of_range", "the weld group has no area", {})

    primary = abs(shear_force_n) / area

    secondary = 0.0
    if torsional_moment_nmm:
        polar = group.polar_second_moment_mm4(leg)
        if polar <= 0.0:
            raise MechanicsError(
                "input.incomplete",
                "this weld group has no polar second moment for torsion",
                {"group": group.name},
            )
        secondary = abs(torsional_moment_nmm) * group.polar_radius_mm / polar

    bending = 0.0
    if bending_moment_nmm:
        second_moment = group.second_moment_mm4(leg)
        if second_moment <= 0.0:
            raise MechanicsError(
                "input.incomplete",
                "this weld group has no second moment for bending",
                {"group": group.name},
            )
        bending = abs(bending_moment_nmm) * group.extreme_fibre_mm / second_moment

    resultant = sqrt((primary + bending) ** 2 + secondary**2)
    allowable = allowable_shear_mpa(electrode_name)
    return WeldResult(
        primary_shear_mpa=primary,
        secondary_shear_mpa=secondary,
        bending_shear_mpa=bending,
        resultant_shear_mpa=resultant,
        allowable_shear_mpa=allowable,
        factor_of_safety=(inf if resultant <= 0.0 else allowable / resultant),
        throat_mm=throat,
    )


__all__ = [
    "AISC_ALLOWABLE_SHEAR_FRACTION",
    "AISC_IMPLIED_SAFETY_FACTOR",
    "ELECTRODES",
    "THROAT_RATIO",
    "WeldGroup",
    "WeldResult",
    "allowable_shear_mpa",
    "circular_weld",
    "electrode",
    "evaluate_fillet_weld",
    "parallel_fillets",
    "rectangular_box",
    "throat_mm",
    "transverse_fillets",
]
