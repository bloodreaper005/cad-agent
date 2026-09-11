from __future__ import annotations

from typing import Any, Mapping


_PROPERTY_TOLERANCE = 1e-6
_DIAMETER_TOLERANCE_MM = 0.05


def build_gear_validation_spec(
    sizing: Mapping[str, Any],
    *,
    pinion_object: str = "Pinion",
    gear_object: str = "Gear",
    interference_limit_mm3: float = 1.0,
) -> dict[str, Any]:
    """Turn a gear-drive sizing result into a `freecad-model-validation` spec.

    The spec checks the built model against the numbers the sizing engine
    computed, not only against its own bounding box: a model built at the
    wrong module or the wrong centre distance fails a mandatory check here
    even if its own geometry is internally consistent. This is what lets a
    drifted gear build reach the correction ledger the same way any other
    validation failure does.
    """
    if sizing.get("status") != "sized":
        raise ValueError(
            f"cannot build a validation spec from a {sizing.get('status')!r} "
            "sizing result; only a sized drive has geometry to check"
        )

    geometry = sizing["derived"]["geometry"]
    teeth = sizing["derived"]["teeth"]
    shaft = sizing["derived"]["shaft"]

    module_mm = float(geometry["module_mm"])
    face_width_mm = float(geometry["face_width_mm"])
    centre_distance_mm = float(geometry["centre_distance_mm"])

    return {
        "required_objects": [pinion_object, gear_object],
        "dimensions": [
            {
                "object": pinion_object,
                "metric": "property:Module",
                "expected": module_mm,
                "tolerance": _PROPERTY_TOLERANCE,
            },
            {
                "object": pinion_object,
                "metric": "property:Teeth",
                "expected": float(teeth["pinion"]),
                "tolerance": _PROPERTY_TOLERANCE,
            },
            {
                "object": gear_object,
                "metric": "property:Teeth",
                "expected": float(teeth["gear"]),
                "tolerance": _PROPERTY_TOLERANCE,
            },
            {
                "object": pinion_object,
                "metric": "property:FaceWidthMM",
                "expected": face_width_mm,
                "tolerance": _PROPERTY_TOLERANCE,
            },
            {
                "object": pinion_object,
                "metric": "bbox_z",
                "expected": face_width_mm,
                "tolerance": _DIAMETER_TOLERANCE_MM,
            },
            {
                "object": gear_object,
                "metric": "property:CentreDistanceMM",
                "expected": centre_distance_mm,
                "tolerance": _PROPERTY_TOLERANCE,
            },
            {
                "object": gear_object,
                "metric": "placement_x",
                "expected": centre_distance_mm,
                "tolerance": _DIAMETER_TOLERANCE_MM,
            },
            {
                "object": pinion_object,
                "metric": "property:BoreMM",
                "expected": float(shaft["selected_diameter_mm"]),
                "tolerance": _PROPERTY_TOLERANCE,
            },
        ],
        "interference_pairs": [
            {
                "a": pinion_object,
                "b": gear_object,
                "max_common_volume_mm3": interference_limit_mm3,
            }
        ],
    }


__all__ = ["build_gear_validation_spec"]
