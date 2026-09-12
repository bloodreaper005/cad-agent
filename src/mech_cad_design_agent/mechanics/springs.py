"""Mechanical springs.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapter 10.

Helical compression springs. The wire-strength constants of table 10-4 are
recorded with the diameter range each fit was made over, because the relation
S_ut = A / d^m is a fit and not a law, and using it outside its range is the
common way a spring calculation goes quietly wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf, pi, sqrt

from .errors import MechanicsError, require_positive


@dataclass(frozen=True)
class WireMaterial:
    """A spring-wire strength fit, S_ut = A / d^m with d in millimetres."""

    name: str
    designation: str
    coefficient_a: float
    exponent_m: float
    minimum_diameter_mm: float
    maximum_diameter_mm: float
    shear_modulus_mpa: float
    elastic_modulus_mpa: float
    torsional_yield_fraction: float

    def ultimate_tensile_mpa(self, wire_diameter_mm: float) -> float:
        diameter = require_positive(wire_diameter_mm, "wire_diameter_mm")
        if not self.minimum_diameter_mm <= diameter <= self.maximum_diameter_mm:
            raise MechanicsError(
                "input.out_of_range",
                f"{self.name} strength is fitted only from "
                f"{self.minimum_diameter_mm} to {self.maximum_diameter_mm} mm",
                {"wire_diameter_mm": diameter},
            )
        return self.coefficient_a / diameter**self.exponent_m

    def torsional_yield_mpa(self, wire_diameter_mm: float) -> float:
        """S_sy as a fraction of S_ut, Shigley table 10-6."""
        return self.torsional_yield_fraction * self.ultimate_tensile_mpa(
            wire_diameter_mm
        )


# Table 10-4 with table 10-5 moduli. The torsional yield fractions are the
# table 10-6 values for springs that have been set removed.
WIRE_MATERIALS: dict[str, WireMaterial] = {
    "music_wire": WireMaterial(
        name="music wire",
        designation="A228",
        coefficient_a=2211.0,
        exponent_m=0.145,
        minimum_diameter_mm=0.10,
        maximum_diameter_mm=6.5,
        shear_modulus_mpa=81700.0,
        elastic_modulus_mpa=203400.0,
        torsional_yield_fraction=0.45,
    ),
    "hard_drawn": WireMaterial(
        name="hard-drawn wire",
        designation="A227",
        coefficient_a=1783.0,
        exponent_m=0.190,
        minimum_diameter_mm=0.70,
        maximum_diameter_mm=12.7,
        shear_modulus_mpa=81700.0,
        elastic_modulus_mpa=199900.0,
        torsional_yield_fraction=0.45,
    ),
    "oil_tempered": WireMaterial(
        name="oil-tempered wire",
        designation="A229",
        coefficient_a=1855.0,
        exponent_m=0.187,
        minimum_diameter_mm=0.50,
        maximum_diameter_mm=12.7,
        shear_modulus_mpa=77200.0,
        elastic_modulus_mpa=203400.0,
        torsional_yield_fraction=0.50,
    ),
    "chrome_vanadium": WireMaterial(
        name="chrome-vanadium wire",
        designation="A232",
        coefficient_a=2005.0,
        exponent_m=0.168,
        minimum_diameter_mm=0.80,
        maximum_diameter_mm=12.0,
        shear_modulus_mpa=77200.0,
        elastic_modulus_mpa=203400.0,
        torsional_yield_fraction=0.50,
    ),
    "chrome_silicon": WireMaterial(
        name="chrome-silicon wire",
        designation="A401",
        coefficient_a=1974.0,
        exponent_m=0.108,
        minimum_diameter_mm=1.6,
        maximum_diameter_mm=10.0,
        shear_modulus_mpa=77200.0,
        elastic_modulus_mpa=203400.0,
        torsional_yield_fraction=0.50,
    ),
}

END_CONDITIONS = ("plain", "plain_ground", "squared", "squared_ground")


def wire_material(name: str) -> WireMaterial:
    try:
        return WIRE_MATERIALS[name]
    except KeyError:
        raise MechanicsError(
            "input.unknown_material",
            f"unknown spring wire {name!r}",
            {"supported": sorted(WIRE_MATERIALS)},
        ) from None


def spring_index(mean_diameter_mm: float, wire_diameter_mm: float) -> float:
    """C = D / d, Shigley eq. 10-1.

    The text recommends 4 <= C <= 12: below four the wire is hard to form, and
    above twelve the spring tangles and buckles readily.
    """
    return require_positive(mean_diameter_mm, "mean_diameter_mm") / require_positive(
        wire_diameter_mm, "wire_diameter_mm"
    )


def bergstrasser_factor(index: float) -> float:
    """Direct-shear and curvature correction K_B, Shigley eq. 10-5."""
    c = require_positive(index, "index")
    if c <= 0.75:
        raise MechanicsError(
            "input.out_of_range", "spring index is too small to correct", {"index": c}
        )
    return (4.0 * c + 2.0) / (4.0 * c - 3.0)


def wahl_factor(index: float) -> float:
    """The older Wahl correction, Shigley eq. 10-6.

    Retained because published spring data is often reduced with it; within a
    percent of the Bergstrasser factor over the usable range of index.
    """
    c = require_positive(index, "index")
    return (4.0 * c - 1.0) / (4.0 * c - 4.0) + 0.615 / c


def shear_stress_mpa(
    *, force_n: float, mean_diameter_mm: float, wire_diameter_mm: float
) -> float:
    """Corrected torsional shear stress, Shigley eq. 10-7."""
    d = require_positive(wire_diameter_mm, "wire_diameter_mm")
    index = spring_index(mean_diameter_mm, d)
    return (
        bergstrasser_factor(index)
        * 8.0
        * abs(force_n)
        * require_positive(mean_diameter_mm, "mean_diameter_mm")
        / (pi * d**3)
    )


def spring_rate_n_per_mm(
    *,
    wire_diameter_mm: float,
    mean_diameter_mm: float,
    active_coils: float,
    shear_modulus_mpa: float,
) -> float:
    """k = d^4 G / (8 D^3 N_a), Shigley eq. 10-9."""
    d = require_positive(wire_diameter_mm, "wire_diameter_mm")
    return (
        d**4
        * require_positive(shear_modulus_mpa, "shear_modulus_mpa")
        / (
            8.0
            * require_positive(mean_diameter_mm, "mean_diameter_mm") ** 3
            * require_positive(active_coils, "active_coils")
        )
    )


def geometry(
    *,
    wire_diameter_mm: float,
    total_coils: float,
    pitch_mm: float,
    ends: str = "squared_ground",
) -> dict[str, float]:
    """Active coils, solid length and free length, Shigley table 10-1."""
    d = require_positive(wire_diameter_mm, "wire_diameter_mm")
    total = require_positive(total_coils, "total_coils")
    pitch = require_positive(pitch_mm, "pitch_mm")
    if ends == "plain":
        active, solid, free = total, d * (total + 1.0), pitch * total + d
    elif ends == "plain_ground":
        active, solid, free = total - 1.0, d * total, pitch * total
    elif ends == "squared":
        active = total - 2.0
        solid, free = d * (total + 1.0), pitch * active + 3.0 * d
    elif ends == "squared_ground":
        active = total - 2.0
        solid, free = d * total, pitch * active + 2.0 * d
    else:
        raise MechanicsError(
            "input.unknown_ends",
            f"unknown spring end condition {ends!r}",
            {"supported": list(END_CONDITIONS)},
        )
    if active <= 0.0:
        raise MechanicsError(
            "input.out_of_range",
            "the end treatment consumes every coil, leaving none active",
            {"total_coils": total, "ends": ends},
        )
    return {
        "active_coils": active,
        "solid_length_mm": solid,
        "free_length_mm": free,
    }


def absolute_stability_ratio(free_length_mm: float, mean_diameter_mm: float) -> float:
    """Slenderness of a spring, used for the buckling check of eq. 10-13."""
    return require_positive(free_length_mm, "free_length_mm") / require_positive(
        mean_diameter_mm, "mean_diameter_mm"
    )


def buckles_absolutely(
    free_length_mm: float, mean_diameter_mm: float, *, squared_ends_fixed: bool = True
) -> bool:
    """Absolute-stability criterion, Shigley eq. 10-13 and table 10-2.

    A spring with both ends squared and against parallel flat plates is stable
    for any load below L_0/D of about 5.26; one end free is far more restrictive
    at about 2.63.
    """
    limit = 5.26 if squared_ends_fixed else 2.63
    return absolute_stability_ratio(free_length_mm, mean_diameter_mm) > limit


@dataclass(frozen=True)
class SpringResult:
    wire_diameter_mm: float
    mean_diameter_mm: float
    spring_index: float
    active_coils: float
    rate_n_per_mm: float
    solid_length_mm: float
    free_length_mm: float
    shear_stress_at_load_mpa: float
    shear_stress_at_solid_mpa: float
    torsional_yield_mpa: float
    factor_of_safety_at_load: float
    factor_of_safety_at_solid: float
    index_within_recommended_range: bool
    may_buckle: bool

    @property
    def safe(self) -> bool:
        return (
            self.factor_of_safety_at_solid >= 1.0
            and self.index_within_recommended_range
            and not self.may_buckle
        )

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "wire_diameter_mm": self.wire_diameter_mm,
            "mean_diameter_mm": self.mean_diameter_mm,
            "spring_index_C": self.spring_index,
            "active_coils": self.active_coils,
            "rate_n_per_mm": self.rate_n_per_mm,
            "solid_length_mm": self.solid_length_mm,
            "free_length_mm": self.free_length_mm,
            "shear_stress_at_load_mpa": self.shear_stress_at_load_mpa,
            "shear_stress_at_solid_mpa": self.shear_stress_at_solid_mpa,
            "torsional_yield_mpa": self.torsional_yield_mpa,
            "factor_of_safety_at_load": self.factor_of_safety_at_load,
            "factor_of_safety_at_solid": self.factor_of_safety_at_solid,
            "index_within_recommended_range": self.index_within_recommended_range,
            "may_buckle": self.may_buckle,
            "safe": self.safe,
        }


def evaluate_compression_spring(
    *,
    force_n: float,
    wire_diameter_mm: float,
    mean_diameter_mm: float,
    total_coils: float,
    pitch_mm: float,
    material: str = "music_wire",
    ends: str = "squared_ground",
    squared_ends_fixed: bool = True,
) -> SpringResult:
    """Rate a helical compression spring at its working load and shut solid.

    Both are reported because they fail differently: the working load governs
    life, and the solid-length stress is what the spring sees if anything ever
    compresses it fully, which is the condition most often left unchecked.
    """
    wire = wire_material(material)
    d = require_positive(wire_diameter_mm, "wire_diameter_mm")
    mean = require_positive(mean_diameter_mm, "mean_diameter_mm")
    index = spring_index(mean, d)
    shape = geometry(
        wire_diameter_mm=d, total_coils=total_coils, pitch_mm=pitch_mm, ends=ends
    )
    rate = spring_rate_n_per_mm(
        wire_diameter_mm=d,
        mean_diameter_mm=mean,
        active_coils=shape["active_coils"],
        shear_modulus_mpa=wire.shear_modulus_mpa,
    )
    deflection_to_solid = shape["free_length_mm"] - shape["solid_length_mm"]
    force_at_solid = rate * max(deflection_to_solid, 0.0)

    at_load = shear_stress_mpa(
        force_n=force_n, mean_diameter_mm=mean, wire_diameter_mm=d
    )
    at_solid = shear_stress_mpa(
        force_n=force_at_solid, mean_diameter_mm=mean, wire_diameter_mm=d
    )
    allowable = wire.torsional_yield_mpa(d)

    return SpringResult(
        wire_diameter_mm=d,
        mean_diameter_mm=mean,
        spring_index=index,
        active_coils=shape["active_coils"],
        rate_n_per_mm=rate,
        solid_length_mm=shape["solid_length_mm"],
        free_length_mm=shape["free_length_mm"],
        shear_stress_at_load_mpa=at_load,
        shear_stress_at_solid_mpa=at_solid,
        torsional_yield_mpa=allowable,
        factor_of_safety_at_load=(inf if at_load <= 0.0 else allowable / at_load),
        factor_of_safety_at_solid=(inf if at_solid <= 0.0 else allowable / at_solid),
        index_within_recommended_range=4.0 <= index <= 12.0,
        may_buckle=buckles_absolutely(
            shape["free_length_mm"], mean, squared_ends_fixed=squared_ends_fixed
        ),
    )


__all__ = [
    "END_CONDITIONS",
    "WIRE_MATERIALS",
    "SpringResult",
    "WireMaterial",
    "absolute_stability_ratio",
    "bergstrasser_factor",
    "buckles_absolutely",
    "evaluate_compression_spring",
    "geometry",
    "shear_stress_mpa",
    "spring_index",
    "spring_rate_n_per_mm",
    "wahl_factor",
    "wire_material",
]
