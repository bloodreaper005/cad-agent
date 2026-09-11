from __future__ import annotations

from dataclasses import dataclass
from math import cos, floor, gcd, pi, sin, sqrt

from .errors import GearSizingError
from .standards import (
    BASIC_RACK_ADDENDUM_FACTOR,
    BASIC_RACK_DEDENDUM_FACTOR,
    ceil_to_step,
)


@dataclass(frozen=True, slots=True)
class ToothCounts:
    pinion: int
    gear: int
    ratio: float
    target_ratio: float
    ratio_error: float
    hunting: bool
    undercut_floor: int


@dataclass(frozen=True, slots=True)
class MeshGeometry:
    module_mm: float
    face_width_mm: float
    pressure_angle_rad: float
    pinion_pitch_mm: float
    gear_pitch_mm: float
    pinion_tip_mm: float
    gear_tip_mm: float
    pinion_root_mm: float
    gear_root_mm: float
    pinion_base_mm: float
    gear_base_mm: float
    centre_distance_mm: float
    transverse_contact_ratio: float


def undercut_floor(pressure_angle_rad: float) -> int:
    """Fewest teeth a rack-generated spur gear can carry without undercut."""
    return floor(2.0 * BASIC_RACK_ADDENDUM_FACTOR / sin(pressure_angle_rad) ** 2)


def select_teeth(
    *,
    target_ratio: float,
    pressure_angle_rad: float,
    min_pinion_teeth: int = 17,
    ratio_tolerance: float = 0.03,
    search_span: int = 6,
    prefer_hunting: bool = True,
    forced_pinion_teeth: int | None = None,
    forced_gear_teeth: int | None = None,
) -> tuple[ToothCounts, list[str]]:
    """Choose tooth counts, preferring a hunting (coprime) pair.

    A non-hunting pair such as 17/51 puts every pinion tooth against the same
    three gear teeth for the life of the drive. Trading about one percent of
    ratio for 17/52 is the choice a gear engineer makes, so coprimality is
    taken before ratio error. Both the error and the trade are reported.
    """
    floor_teeth = undercut_floor(pressure_angle_rad)
    warnings: list[str] = []

    if forced_pinion_teeth is not None:
        if forced_pinion_teeth < floor_teeth:
            raise GearSizingError(
                "teeth.undercut",
                f"{forced_pinion_teeth} pinion teeth undercuts below the "
                f"floor of {floor_teeth} for this pressure angle",
                {"floor": floor_teeth, "requested": forced_pinion_teeth},
            )
        pinion = forced_pinion_teeth
        gear = forced_gear_teeth or round(target_ratio * pinion)
        return _counts(pinion, gear, target_ratio, floor_teeth), warnings

    start = max(floor_teeth, min_pinion_teeth)
    offsets = (0, 1, -1, 2, -2, 3, -3)

    for hunting_required in (True, False) if prefer_hunting else (False,):
        for pinion in range(start, start + search_span + 1):
            for offset in offsets:
                gear = round(target_ratio * pinion) + offset
                if gear < pinion:
                    continue
                error = abs(gear / pinion - target_ratio) / target_ratio
                if error > ratio_tolerance:
                    continue
                if hunting_required and gcd(pinion, gear) != 1:
                    continue
                if not hunting_required and gcd(pinion, gear) != 1:
                    warnings.append(
                        f"tooth counts {pinion}/{gear} share a common factor, so "
                        "the same teeth meet every revolution; wear will not "
                        "distribute evenly"
                    )
                return _counts(pinion, gear, target_ratio, floor_teeth), warnings

    raise GearSizingError(
        "ratio.tolerance",
        f"no tooth pair within {ratio_tolerance:.1%} of a {target_ratio:.4f} ratio",
        {"target_ratio": target_ratio, "searched_from": start, "span": search_span},
    )


def _counts(
    pinion: int, gear: int, target_ratio: float, floor_teeth: int
) -> ToothCounts:
    ratio = gear / pinion
    return ToothCounts(
        pinion=pinion,
        gear=gear,
        ratio=ratio,
        target_ratio=target_ratio,
        ratio_error=(ratio - target_ratio) / target_ratio,
        hunting=gcd(pinion, gear) == 1,
        undercut_floor=floor_teeth,
    )


def build_mesh(
    *,
    module_mm: float,
    teeth: ToothCounts,
    pressure_angle_rad: float,
    face_width_ratio: float,
    face_width_step_mm: float = 1.0,
) -> MeshGeometry:
    """Standard spur geometry with no profile shift."""
    pinion_pitch = module_mm * teeth.pinion
    gear_pitch = module_mm * teeth.gear
    addendum = BASIC_RACK_ADDENDUM_FACTOR * module_mm
    dedendum = BASIC_RACK_DEDENDUM_FACTOR * module_mm
    pinion_tip = pinion_pitch + 2.0 * addendum
    gear_tip = gear_pitch + 2.0 * addendum
    pinion_base = pinion_pitch * cos(pressure_angle_rad)
    gear_base = gear_pitch * cos(pressure_angle_rad)
    centre_distance = 0.5 * (pinion_pitch + gear_pitch)
    face_width = ceil_to_step(face_width_ratio * pinion_pitch, face_width_step_mm)

    path = (
        0.5 * sqrt(max(pinion_tip**2 - pinion_base**2, 0.0))
        + 0.5 * sqrt(max(gear_tip**2 - gear_base**2, 0.0))
        - centre_distance * sin(pressure_angle_rad)
    )
    contact_ratio = path / (pi * module_mm * cos(pressure_angle_rad))

    return MeshGeometry(
        module_mm=module_mm,
        face_width_mm=face_width,
        pressure_angle_rad=pressure_angle_rad,
        pinion_pitch_mm=pinion_pitch,
        gear_pitch_mm=gear_pitch,
        pinion_tip_mm=pinion_tip,
        gear_tip_mm=gear_tip,
        pinion_root_mm=pinion_pitch - 2.0 * dedendum,
        gear_root_mm=gear_pitch - 2.0 * dedendum,
        pinion_base_mm=pinion_base,
        gear_base_mm=gear_base,
        centre_distance_mm=centre_distance,
        transverse_contact_ratio=contact_ratio,
    )


__all__ = [
    "MeshGeometry",
    "ToothCounts",
    "build_mesh",
    "select_teeth",
    "undercut_floor",
]
