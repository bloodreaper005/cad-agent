from __future__ import annotations

from math import ceil, tan

from .errors import GearSizingError


# ISO 54:1996 preferred module series. Series 1 is preferred; series 2 exists
# but the standard advises avoiding it where series 1 will serve.
ISO54_SERIES_1: tuple[float, ...] = (
    1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0,
    10.0, 12.0, 16.0, 20.0, 25.0, 32.0, 40.0, 50.0,
)
ISO54_SERIES_2: tuple[float, ...] = (
    1.125, 1.375, 1.75, 2.25, 2.75, 3.5, 4.5, 5.5, 7.0, 9.0,
    11.0, 14.0, 18.0, 22.0, 28.0, 36.0, 45.0,
)

MODULE_SERIES: dict[str, tuple[float, ...]] = {
    "iso54_series1": ISO54_SERIES_1,
    "iso54_series1_and_2": tuple(sorted(ISO54_SERIES_1 + ISO54_SERIES_2)),
}

# ISO 15 bearing bore diameters, the sizes a shaft seat is actually made to.
ISO15_BORE_SERIES: tuple[float, ...] = (
    10.0, 12.0, 15.0, 17.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0,
    55.0, 60.0, 65.0, 70.0, 75.0, 80.0, 85.0, 90.0, 95.0, 100.0,
    110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 180.0, 200.0,
)

# ISO 53 basic rack profile A, the reference the tooth form is generated from.
BASIC_RACK_ADDENDUM_FACTOR = 1.0
BASIC_RACK_DEDENDUM_FACTOR = 1.25
BASIC_RACK_TIP_RADIUS_FACTOR = 0.38

# Guard against a float artefact pushing an exact integer up a whole step.
_CEIL_EPSILON = 1e-9


def involute(angle_rad: float) -> float:
    """inv(a) = tan(a) - a, the involute function."""
    return tan(angle_rad) - angle_rad


def ceil_to_step(value: float, step: float) -> float:
    """Round up to a multiple of `step`, tolerating exact-hit float error."""
    if step <= 0:
        raise GearSizingError("input.non_positive", "step must be positive")
    return ceil(value / step - _CEIL_EPSILON) * step


def select_from_series(value: float, series: tuple[float, ...]) -> float:
    """Return the smallest series entry at or above `value`."""
    for candidate in series:
        if candidate >= value - _CEIL_EPSILON:
            return candidate
    raise GearSizingError(
        "series.exhausted",
        f"no series entry at or above {value:.4f}",
        {"value": value, "series_max": series[-1] if series else None},
    )


def module_series(name: str) -> tuple[float, ...]:
    try:
        return MODULE_SERIES[name]
    except KeyError:
        raise GearSizingError(
            "input.unknown_option",
            f"unknown module series {name!r}",
            {"valid": sorted(MODULE_SERIES)},
        ) from None


__all__ = [
    "BASIC_RACK_ADDENDUM_FACTOR",
    "BASIC_RACK_DEDENDUM_FACTOR",
    "BASIC_RACK_TIP_RADIUS_FACTOR",
    "ISO15_BORE_SERIES",
    "ISO54_SERIES_1",
    "ISO54_SERIES_2",
    "MODULE_SERIES",
    "ceil_to_step",
    "involute",
    "module_series",
    "select_from_series",
]
