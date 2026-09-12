"""Screws, fasteners, and the design of nonpermanent joints.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapter 8.

This is the calculation half of the fastener gate the model validator already
enforces geometrically. The validator proves a bolt is installed correctly: on
axis, in a matching hole, seated, and not interfering. Nothing here changes that
and nothing there proves the joint will hold. The two are complementary.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, inf, pi

from .errors import MechanicsError, require_non_negative, require_positive


# Table 8-11, SAE-equivalent proof strengths for metric property classes, MPa.
METRIC_PROPERTY_CLASSES: dict[str, dict[str, float]] = {
    "4.6": {"proof_mpa": 225.0, "yield_mpa": 240.0, "ultimate_mpa": 400.0},
    "4.8": {"proof_mpa": 310.0, "yield_mpa": 340.0, "ultimate_mpa": 420.0},
    "5.8": {"proof_mpa": 380.0, "yield_mpa": 420.0, "ultimate_mpa": 520.0},
    "8.8": {"proof_mpa": 600.0, "yield_mpa": 660.0, "ultimate_mpa": 830.0},
    "9.8": {"proof_mpa": 650.0, "yield_mpa": 720.0, "ultimate_mpa": 900.0},
    "10.9": {"proof_mpa": 830.0, "yield_mpa": 940.0, "ultimate_mpa": 1040.0},
    "12.9": {"proof_mpa": 970.0, "yield_mpa": 1100.0, "ultimate_mpa": 1220.0},
}

# Table 8-1, coarse-pitch metric thread series: nominal diameter to pitch in mm
# and tensile-stress area in mm^2.
COARSE_THREAD_SERIES: dict[float, tuple[float, float]] = {
    1.6: (0.35, 1.27),
    2.0: (0.40, 2.07),
    2.5: (0.45, 3.39),
    3.0: (0.50, 5.03),
    4.0: (0.70, 8.78),
    5.0: (0.80, 14.2),
    6.0: (1.00, 20.1),
    8.0: (1.25, 36.6),
    10.0: (1.50, 58.0),
    12.0: (1.75, 84.3),
    14.0: (2.00, 115.0),
    16.0: (2.00, 157.0),
    20.0: (2.50, 245.0),
    24.0: (3.00, 353.0),
    30.0: (3.50, 561.0),
    36.0: (4.00, 817.0),
}

# Table 8-8, Wileman member-stiffness constants for the exponential fit.
WILEMAN_CONSTANTS: dict[str, tuple[float, float]] = {
    "steel": (0.78715, 0.62873),
    "aluminium": (0.79670, 0.63816),
    "aluminum": (0.79670, 0.63816),
    "copper": (0.79568, 0.63553),
    "grey_cast_iron": (0.77871, 0.61616),
}

# Section 8-7. A reused connection is preloaded to 0.75 of proof load, a
# permanent one to 0.90.
PRELOAD_FRACTIONS: dict[str, float] = {"reused": 0.75, "permanent": 0.90}

# Equation 8-27, the torque coefficient for a non-lubricated steel fastener.
DEFAULT_TORQUE_COEFFICIENT = 0.20


def tensile_stress_area_mm2(nominal_diameter_mm: float, pitch_mm: float) -> float:
    """Tensile-stress area A_t, the basis of table 8-1.

    A_t = (pi/4)(d - 0.9382 p)^2, the area at the mean of the pitch and minor
    diameters, which is the section a threaded fastener actually breaks at.
    """
    diameter = require_positive(nominal_diameter_mm, "nominal_diameter_mm")
    pitch = require_positive(pitch_mm, "pitch_mm")
    return pi / 4.0 * (diameter - 0.9382 * pitch) ** 2


def coarse_thread(nominal_diameter_mm: float) -> tuple[float, float]:
    """Return (pitch_mm, tensile_stress_area_mm2) from the coarse series."""
    diameter = require_positive(nominal_diameter_mm, "nominal_diameter_mm")
    for size, (pitch, area) in COARSE_THREAD_SERIES.items():
        if abs(size - diameter) < 1e-9:
            return (pitch, area)
    raise MechanicsError(
        "input.unknown_thread",
        f"M{diameter:g} is not in the coarse-pitch series",
        {"supported": sorted(COARSE_THREAD_SERIES)},
    )


def property_class(name: str) -> dict[str, float]:
    try:
        return dict(METRIC_PROPERTY_CLASSES[name])
    except KeyError:
        raise MechanicsError(
            "input.unknown_property_class",
            f"unknown metric property class {name!r}",
            {"supported": sorted(METRIC_PROPERTY_CLASSES)},
        ) from None


def bolt_stiffness_n_per_mm(
    *,
    nominal_diameter_mm: float,
    tensile_stress_area_mm2: float,
    shank_length_mm: float,
    threaded_length_in_grip_mm: float,
    elastic_modulus_mpa: float = 207000.0,
) -> float:
    """Bolt stiffness k_b, Shigley eq. 8-17.

    The bolt is two springs in series: the unthreaded shank at its full area and
    the threaded portion within the grip at its tensile-stress area.
    """
    diameter = require_positive(nominal_diameter_mm, "nominal_diameter_mm")
    at = require_positive(tensile_stress_area_mm2, "tensile_stress_area_mm2")
    ld = require_non_negative(shank_length_mm, "shank_length_mm")
    lt = require_non_negative(threaded_length_in_grip_mm, "threaded_length_in_grip_mm")
    modulus = require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
    if ld <= 0.0 and lt <= 0.0:
        raise MechanicsError(
            "input.out_of_range",
            "the bolt must have some length within the grip",
            {"shank_length_mm": ld, "threaded_length_in_grip_mm": lt},
        )
    ad = pi / 4.0 * diameter**2
    return ad * at * modulus / (ad * lt + at * ld)


def member_stiffness_n_per_mm(
    *,
    nominal_diameter_mm: float,
    grip_mm: float,
    elastic_modulus_mpa: float = 207000.0,
    member_material: str = "steel",
) -> float:
    """Member stiffness k_m by the Wileman fit, Shigley eq. 8-22 and table 8-8.

    Valid for a joint of one material with the usual frustum geometry. A joint
    of two different materials must be built from `frustum_stiffness` members in
    series instead.
    """
    diameter = require_positive(nominal_diameter_mm, "nominal_diameter_mm")
    grip = require_positive(grip_mm, "grip_mm")
    modulus = require_positive(elastic_modulus_mpa, "elastic_modulus_mpa")
    try:
        a, b = WILEMAN_CONSTANTS[member_material]
    except KeyError:
        raise MechanicsError(
            "input.unknown_material",
            f"no Wileman constants for {member_material!r}",
            {"supported": sorted(WILEMAN_CONSTANTS)},
        ) from None
    return a * modulus * diameter * exp(b * diameter / grip)


def joint_constant(
    bolt_stiffness: float, member_stiffness: float
) -> float:
    """Stiffness constant C = k_b / (k_b + k_m), Shigley eq. 8-19.

    The fraction of an external load that reaches the bolt. A stiff member and a
    compliant bolt give a small C, which is what makes a preloaded joint
    fatigue-tolerant.
    """
    kb = require_positive(bolt_stiffness, "bolt_stiffness")
    km = require_positive(member_stiffness, "member_stiffness")
    return kb / (kb + km)


def proof_load_n(tensile_stress_area_mm2: float, proof_strength_mpa: float) -> float:
    """Proof load F_p = A_t S_p, Shigley eq. 8-30."""
    return require_positive(
        tensile_stress_area_mm2, "tensile_stress_area_mm2"
    ) * require_positive(proof_strength_mpa, "proof_strength_mpa")


def recommended_preload_n(
    proof_load: float, *, connection: str = "reused"
) -> float:
    """Recommended preload F_i, Shigley eq. 8-31."""
    try:
        fraction = PRELOAD_FRACTIONS[connection]
    except KeyError:
        raise MechanicsError(
            "input.unknown_connection",
            f"connection must be one of {sorted(PRELOAD_FRACTIONS)}",
            {"connection": connection},
        ) from None
    return fraction * require_positive(proof_load, "proof_load")


def tightening_torque_nmm(
    preload_n: float,
    nominal_diameter_mm: float,
    *,
    torque_coefficient: float = DEFAULT_TORQUE_COEFFICIENT,
) -> float:
    """Tightening torque T = K F_i d, Shigley eq. 8-27."""
    return (
        require_positive(torque_coefficient, "torque_coefficient")
        * require_positive(preload_n, "preload_n")
        * require_positive(nominal_diameter_mm, "nominal_diameter_mm")
    )


@dataclass(frozen=True)
class BoltedJointResult:
    """Every margin a statically loaded tension joint is rated on."""

    joint_constant: float
    preload_n: float
    proof_load_n: float
    bolt_stiffness_n_per_mm: float
    member_stiffness_n_per_mm: float
    bolt_load_n: float
    member_load_n: float
    yielding_factor: float
    load_factor: float
    separation_factor: float

    @property
    def governing_factor(self) -> float:
        return min(self.yielding_factor, self.load_factor, self.separation_factor)

    @property
    def safe(self) -> bool:
        return self.governing_factor >= 1.0

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "joint_constant_C": self.joint_constant,
            "preload_n": self.preload_n,
            "proof_load_n": self.proof_load_n,
            "bolt_stiffness_n_per_mm": self.bolt_stiffness_n_per_mm,
            "member_stiffness_n_per_mm": self.member_stiffness_n_per_mm,
            "bolt_load_n": self.bolt_load_n,
            "member_load_n": self.member_load_n,
            "yielding_factor_np": self.yielding_factor,
            "load_factor_nL": self.load_factor,
            "separation_factor_n0": self.separation_factor,
            "governing_factor": self.governing_factor,
            "safe": self.safe,
        }


def evaluate_tension_joint(
    *,
    external_load_per_bolt_n: float,
    tensile_stress_area_mm2: float,
    proof_strength_mpa: float,
    bolt_stiffness_n_per_mm: float,
    member_stiffness_n_per_mm: float,
    preload_n: float | None = None,
    connection: str = "reused",
) -> BoltedJointResult:
    """Rate a statically loaded bolted tension joint.

    Returns the three margins the text requires together, because a joint can
    pass one and fail another: yielding (eq. 8-28), overload (eq. 8-29), and
    joint separation (eq. 8-30). Separation is the one that is easy to forget
    and the one that destroys the fatigue argument when it is missed.
    """
    load = require_non_negative(external_load_per_bolt_n, "external_load_per_bolt_n")
    at = require_positive(tensile_stress_area_mm2, "tensile_stress_area_mm2")
    proof_strength = require_positive(proof_strength_mpa, "proof_strength_mpa")
    kb = require_positive(bolt_stiffness_n_per_mm, "bolt_stiffness_n_per_mm")
    km = require_positive(member_stiffness_n_per_mm, "member_stiffness_n_per_mm")

    fp = proof_load_n(at, proof_strength)
    fi = (
        require_positive(preload_n, "preload_n")
        if preload_n is not None
        else recommended_preload_n(fp, connection=connection)
    )
    if fi >= fp:
        raise MechanicsError(
            "input.out_of_range",
            "preload must be below the proof load",
            {"preload_n": fi, "proof_load_n": fp},
        )
    c = joint_constant(kb, km)

    bolt_load = c * load + fi
    member_load = (1.0 - c) * load - fi

    yielding = fp / bolt_load if bolt_load > 0.0 else inf
    overload = (fp - fi) / (c * load) if load > 0.0 else inf
    separation = fi / (load * (1.0 - c)) if load > 0.0 and c < 1.0 else inf

    return BoltedJointResult(
        joint_constant=c,
        preload_n=fi,
        proof_load_n=fp,
        bolt_stiffness_n_per_mm=kb,
        member_stiffness_n_per_mm=km,
        bolt_load_n=bolt_load,
        member_load_n=member_load,
        yielding_factor=yielding,
        load_factor=overload,
        separation_factor=separation,
    )


def fatigue_stresses_mpa(
    *,
    maximum_external_load_n: float,
    minimum_external_load_n: float,
    joint_constant: float,
    preload_n: float,
    tensile_stress_area_mm2: float,
) -> tuple[float, float]:
    """Alternating and midrange bolt stress, Shigley eq. 8-35 and 8-36.

    Feed these to `fatigue.evaluate_fatigue` with the bolt's own endurance
    limit. The preload appears in the midrange stress and not the alternating
    stress, which is exactly why preload buys fatigue life.
    """
    at = require_positive(tensile_stress_area_mm2, "tensile_stress_area_mm2")
    c = require_positive(joint_constant, "joint_constant")
    fi = require_non_negative(preload_n, "preload_n")
    high = float(maximum_external_load_n)
    low = float(minimum_external_load_n)
    if low > high:
        high, low = low, high
    alternating = c * (high - low) / (2.0 * at)
    midrange = c * (high + low) / (2.0 * at) + fi / at
    return (alternating, midrange)


__all__ = [
    "COARSE_THREAD_SERIES",
    "DEFAULT_TORQUE_COEFFICIENT",
    "METRIC_PROPERTY_CLASSES",
    "PRELOAD_FRACTIONS",
    "WILEMAN_CONSTANTS",
    "BoltedJointResult",
    "bolt_stiffness_n_per_mm",
    "coarse_thread",
    "evaluate_tension_joint",
    "fatigue_stresses_mpa",
    "joint_constant",
    "member_stiffness_n_per_mm",
    "property_class",
    "proof_load_n",
    "recommended_preload_n",
    "tensile_stress_area_mm2",
    "tightening_torque_nmm",
]
