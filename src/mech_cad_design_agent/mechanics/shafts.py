"""Shafts and shaft components.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapter 7.

The four distortion-energy shaft criteria of equations 7-8 through 7-12, each
available both as a diameter for a required factor of safety and as a factor of
safety for a given diameter. The pair matters: sizing gives a diameter that is
then rounded up to a stock or bearing-bore size, and the factor actually
achieved at that rounded size is not the one that was asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf, pi, sqrt

from .errors import MechanicsError, require_non_negative, require_positive
from .fatigue import langer_static_factor

SHAFT_CRITERIA = ("goodman", "gerber", "asme_elliptic", "soderberg")


@dataclass(frozen=True)
class ShaftLoading:
    """The four moment and torque components a shaft section carries.

    Rotating shafts are the common case and put the bending entirely in the
    alternating term with the torque steady, but the general form is carried so
    that a non-rotating or reversing shaft is expressible.
    """

    alternating_moment_nmm: float = 0.0
    midrange_moment_nmm: float = 0.0
    alternating_torque_nmm: float = 0.0
    midrange_torque_nmm: float = 0.0
    bending_kf: float = 1.0
    torsion_kfs: float = 1.0

    def von_mises_amplitudes_mpa(self, diameter_mm: float) -> tuple[float, float]:
        """Alternating and midrange von Mises stress, Shigley eq. 7-5 and 7-6."""
        diameter = require_positive(diameter_mm, "diameter_mm")
        section = pi * diameter**3 / 32.0
        polar_section = 2.0 * section
        alternating = sqrt(
            (self.bending_kf * self.alternating_moment_nmm / section) ** 2
            + 3.0
            * (self.torsion_kfs * self.alternating_torque_nmm / polar_section) ** 2
        )
        midrange = sqrt(
            (self.bending_kf * self.midrange_moment_nmm / section) ** 2
            + 3.0 * (self.torsion_kfs * self.midrange_torque_nmm / polar_section) ** 2
        )
        return (alternating, midrange)


@dataclass(frozen=True)
class ShaftResult:
    criterion: str
    diameter_mm: float
    fatigue_factor: float
    yield_factor: float
    alternating_mpa: float
    midrange_mpa: float

    @property
    def governing_factor(self) -> float:
        return min(self.fatigue_factor, self.yield_factor)

    @property
    def safe(self) -> bool:
        return self.governing_factor >= 1.0

    def as_dict(self) -> dict[str, float | str | bool]:
        return {
            "criterion": self.criterion,
            "diameter_mm": self.diameter_mm,
            "fatigue_factor": self.fatigue_factor,
            "yield_factor": self.yield_factor,
            "alternating_mpa": self.alternating_mpa,
            "midrange_mpa": self.midrange_mpa,
            "governing_factor": self.governing_factor,
            "safe": self.safe,
        }


def _terms(loading: ShaftLoading) -> tuple[float, float]:
    """The two bracketed root terms shared by every shaft criterion."""
    alternating = sqrt(
        4.0 * (loading.bending_kf * loading.alternating_moment_nmm) ** 2
        + 3.0 * (loading.torsion_kfs * loading.alternating_torque_nmm) ** 2
    )
    midrange = sqrt(
        4.0 * (loading.bending_kf * loading.midrange_moment_nmm) ** 2
        + 3.0 * (loading.torsion_kfs * loading.midrange_torque_nmm) ** 2
    )
    return (alternating, midrange)


def diameter_mm(
    *,
    loading: ShaftLoading,
    endurance_limit_mpa: float,
    ultimate_tensile_mpa: float,
    yield_strength_mpa: float,
    safety_factor: float,
    criterion: str = "goodman",
) -> float:
    """Solve one DE criterion for the required diameter.

    Goodman is eq. 7-8, Gerber eq. 7-10, ASME-elliptic eq. 7-12 and Soderberg
    eq. 7-14. Goodman is the usual design choice because it is the conservative
    one and it inverts in closed form.
    """
    endurance = require_positive(endurance_limit_mpa, "endurance_limit_mpa")
    ultimate = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    yield_strength = require_positive(yield_strength_mpa, "yield_strength_mpa")
    factor = require_positive(safety_factor, "safety_factor")
    alternating, midrange = _terms(loading)
    if alternating <= 0.0 and midrange <= 0.0:
        raise MechanicsError(
            "input.incomplete",
            "the shaft carries no load, so no diameter is implied",
            {},
        )

    if criterion == "goodman":
        bracket = alternating / endurance + midrange / ultimate
        return (16.0 * factor * bracket / pi) ** (1.0 / 3.0)
    if criterion == "soderberg":
        bracket = alternating / endurance + midrange / yield_strength
        return (16.0 * factor * bracket / pi) ** (1.0 / 3.0)
    if criterion == "asme_elliptic":
        # Eq. 7-12 already carries the 4 and 3 weights inside its root, and the
        # shared _terms form carries exactly those, so the criterion reduces to
        # the plain sum of squares of the two normalized terms.
        bracket = sqrt(
            (alternating / endurance) ** 2 + (midrange / yield_strength) ** 2
        )
        return (16.0 * factor * bracket / pi) ** (1.0 / 3.0)
    if criterion == "gerber":
        if midrange <= 0.0:
            bracket = alternating / endurance
            return (16.0 * factor * bracket / pi) ** (1.0 / 3.0)
        a = alternating
        b = midrange
        inner = 1.0 + sqrt(1.0 + (2.0 * b * endurance / (a * ultimate)) ** 2)
        return (
            8.0 * factor * a / (pi * endurance) * inner
        ) ** (1.0 / 3.0)
    raise MechanicsError(
        "input.unknown_criterion",
        f"unknown shaft criterion {criterion!r}",
        {"supported": list(SHAFT_CRITERIA)},
    )


def evaluate(
    *,
    loading: ShaftLoading,
    diameter_mm: float,
    endurance_limit_mpa: float,
    ultimate_tensile_mpa: float,
    yield_strength_mpa: float,
    criterion: str = "goodman",
) -> ShaftResult:
    """Rate a shaft of a known diameter, Shigley eq. 7-7 through 7-15.

    Use this after rounding a solved diameter up to a stock size, because the
    factor of safety at the rounded size is the one the design actually has.
    """
    diameter = require_positive(diameter_mm, "diameter_mm")
    endurance = require_positive(endurance_limit_mpa, "endurance_limit_mpa")
    ultimate = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    yield_strength = require_positive(yield_strength_mpa, "yield_strength_mpa")
    alternating, midrange = loading.von_mises_amplitudes_mpa(diameter)

    if alternating <= 0.0 and midrange <= 0.0:
        fatigue_factor = inf
    elif criterion == "goodman":
        fatigue_factor = 1.0 / (alternating / endurance + midrange / ultimate)
    elif criterion == "soderberg":
        fatigue_factor = 1.0 / (alternating / endurance + midrange / yield_strength)
    elif criterion == "asme_elliptic":
        fatigue_factor = 1.0 / sqrt(
            (alternating / endurance) ** 2 + (midrange / yield_strength) ** 2
        )
    elif criterion == "gerber":
        if midrange <= 0.0:
            fatigue_factor = endurance / alternating
        else:
            fatigue_factor = (
                0.5
                * (ultimate / midrange) ** 2
                * (alternating / endurance)
                * (
                    -1.0
                    + sqrt(
                        1.0 + (2.0 * midrange * endurance / (ultimate * alternating)) ** 2
                    )
                )
            )
    else:
        raise MechanicsError(
            "input.unknown_criterion",
            f"unknown shaft criterion {criterion!r}",
            {"supported": list(SHAFT_CRITERIA)},
        )

    return ShaftResult(
        criterion=criterion,
        diameter_mm=diameter,
        fatigue_factor=fatigue_factor,
        yield_factor=langer_static_factor(alternating, midrange, yield_strength),
        alternating_mpa=alternating,
        midrange_mpa=midrange,
    )


def rotating_shaft(
    *,
    bending_moment_nmm: float,
    steady_torque_nmm: float,
    bending_kf: float = 1.0,
    torsion_kfs: float = 1.0,
) -> ShaftLoading:
    """The common case: a rotating shaft under a steady transverse load.

    Rotation turns a steady bending moment into completely reversed bending at
    the surface, so the moment is entirely alternating while the torque, which
    does not reverse with rotation, is entirely steady.
    """
    return ShaftLoading(
        alternating_moment_nmm=require_non_negative(
            abs(bending_moment_nmm), "bending_moment_nmm"
        ),
        midrange_moment_nmm=0.0,
        alternating_torque_nmm=0.0,
        midrange_torque_nmm=require_non_negative(
            abs(steady_torque_nmm), "steady_torque_nmm"
        ),
        bending_kf=require_positive(bending_kf, "bending_kf"),
        torsion_kfs=require_positive(torsion_kfs, "torsion_kfs"),
    )


__all__ = [
    "SHAFT_CRITERIA",
    "ShaftLoading",
    "ShaftResult",
    "diameter_mm",
    "evaluate",
    "rotating_shaft",
]
