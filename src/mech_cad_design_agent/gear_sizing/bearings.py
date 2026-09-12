from __future__ import annotations

from dataclasses import dataclass

from ..mechanics.bearings import BEARING_FAMILIES
from .errors import GearSizingError


# ISO 281 load exponent and equivalent-load coefficients by bearing family,
# derived from the single copy in mechanics.bearings rather than repeated here.
# For deep-groove ball bearings e and Y are functions of the axial load over
# the static rating, which is unknown until a bearing is chosen. The values
# there are mid-range placeholders; the output is a required capacity, never a
# selection. A spur mesh carries no axial load, so they do not bind here.
BEARING_TYPES: dict[str, tuple[float, float, float, float]] = {
    name: (values["exponent"], values["x2"], values["y2"], values["e"])
    for name, values in BEARING_FAMILIES.items()
}

# ISO 281 life adjustment for reliability above 90 percent.
RELIABILITY_ADJUSTMENT: dict[float, float] = {
    0.90: 1.00,
    0.95: 0.62,
    0.96: 0.53,
    0.97: 0.44,
    0.98: 0.33,
    0.99: 0.21,
}


@dataclass(frozen=True, slots=True)
class BearingResult:
    bearing_type: str
    radial_load_n: float
    axial_load_n: float
    equivalent_load_n: float
    life_exponent: float
    life_adjustment: float
    required_dynamic_capacity_n: float
    life_hours: float


def equivalent_load_n(
    *, radial_n: float, axial_n: float, bearing_type: str
) -> float:
    try:
        _, x_factor, y_factor, threshold = BEARING_TYPES[bearing_type]
    except KeyError:
        raise GearSizingError(
            "input.unknown_option",
            f"unknown bearing type {bearing_type!r}",
            {"valid": sorted(BEARING_TYPES)},
        ) from None
    if radial_n <= 0.0:
        return axial_n * y_factor
    if axial_n / radial_n <= threshold:
        return radial_n
    return x_factor * radial_n + y_factor * axial_n


def required_dynamic_capacity(
    *,
    radial_n: float,
    axial_n: float,
    speed_rpm: float,
    life_hours: float,
    bearing_type: str = "deep_groove_ball",
    reliability: float = 0.90,
) -> BearingResult:
    """ISO 281 basic rating life, solved for the capacity a bearing must have."""
    exponent, _, _, _ = BEARING_TYPES[bearing_type]
    equivalent = equivalent_load_n(
        radial_n=radial_n, axial_n=axial_n, bearing_type=bearing_type
    )
    closest = min(RELIABILITY_ADJUSTMENT, key=lambda key: abs(key - reliability))
    adjustment = RELIABILITY_ADJUSTMENT[closest]
    revolutions = 60.0 * speed_rpm * life_hours / (adjustment * 1.0e6)
    capacity = equivalent * revolutions ** (1.0 / exponent)
    return BearingResult(
        bearing_type=bearing_type,
        radial_load_n=radial_n,
        axial_load_n=axial_n,
        equivalent_load_n=equivalent,
        life_exponent=exponent,
        life_adjustment=adjustment,
        required_dynamic_capacity_n=capacity,
        life_hours=life_hours,
    )


__all__ = [
    "BEARING_TYPES",
    "RELIABILITY_ADJUSTMENT",
    "BearingResult",
    "equivalent_load_n",
    "required_dynamic_capacity",
]
