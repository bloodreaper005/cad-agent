"""Model a spur pinion and gear from a gear-drive sizing result.

argv: <model.FCStd> <sizing.json>

`sizing.json` is the JSON document produced by `gear_sizing.to_json()`. Every
value the geometry depends on is stamped onto the objects as a FreeCAD
property, so the validation spec generated from the same sizing result can
check the built model against the numbers it was supposed to carry, not only
against its own bounding box.

Standard involute teeth, ISO 53 profile A (h_aP*=1.0, h_fP*=1.25,
rho_fP*=0.38), zero profile shift, matching the spur geometry the sizing
engine assumes. A tooth space, not a tooth, must sit on the centre line facing
the mating gear: for an odd tooth count that is phase 0; for an even count it
is one half tooth pitch. Getting this backwards produces a model that looks
plausible and interferes by hundreds of cubic millimetres at the correct
centre distance.
"""

from __future__ import annotations

import json
import sys
from math import acos, cos, pi, sin, sqrt, tan

import FreeCAD as App
import Part


ADDENDUM_FACTOR = 1.0
DEDENDUM_FACTOR = 1.25
POINTS_PER_FLANK = 14
BACKLASH_MM = 0.20   # total circumferential backlash, split across both flanks


def _involute_angle(angle: float) -> float:
    return tan(angle) - angle


def _tooth_half_angle(
    radius: float, base_radius: float, teeth: int, module_mm: float,
    pressure_angle_rad: float,
) -> float:
    if radius <= base_radius:
        radius = base_radius
    local = acos(min(1.0, base_radius / radius))
    pitch_radius = module_mm * teeth / 2.0
    return (
        pi / (2 * teeth)
        + _involute_angle(pressure_angle_rad)
        - _involute_angle(local)
        - BACKLASH_MM / (2.0 * pitch_radius)
    )


def _profile(
    teeth: int, module_mm: float, pressure_angle_rad: float, phase: float
) -> list:
    pitch = module_mm * teeth / 2.0
    base = pitch * cos(pressure_angle_rad)
    tip = pitch + ADDENDUM_FACTOR * module_mm
    root = pitch - DEDENDUM_FACTOR * module_mm
    start = max(base, root)

    radii = [
        start + (tip - start) * index / (POINTS_PER_FLANK - 1)
        for index in range(POINTS_PER_FLANK)
    ]
    points = []
    for index in range(teeth):
        centre = phase + index * 2.0 * pi / teeth
        half_at_start = _tooth_half_angle(
            start, base, teeth, module_mm, pressure_angle_rad
        )
        if root < base:
            points.append((root, centre - half_at_start))
        for radius in radii:
            half = _tooth_half_angle(radius, base, teeth, module_mm, pressure_angle_rad)
            points.append((radius, centre - half))
        for radius in reversed(radii):
            half = _tooth_half_angle(radius, base, teeth, module_mm, pressure_angle_rad)
            points.append((radius, centre + half))
        if root < base:
            points.append((root, centre + half_at_start))
            next_centre = phase + (index + 1) * 2.0 * pi / teeth
            points.append((root, next_centre - half_at_start))

    return [App.Vector(r * cos(a), r * sin(a), 0) for r, a in points]


def _gear_solid(
    teeth: int,
    module_mm: float,
    pressure_angle_rad: float,
    face_width_mm: float,
    bore_mm: float,
    phase: float,
):
    points = _profile(teeth, module_mm, pressure_angle_rad, phase)
    wire = Part.makePolygon(points + [points[0]])
    solid = Part.Face(wire).extrude(App.Vector(0, 0, face_width_mm))
    if bore_mm > 0:
        bore = Part.makeCylinder(
            bore_mm / 2.0, face_width_mm + 2.0, App.Vector(0, 0, -1)
        )
        solid = solid.cut(bore)
    return solid


def _mesh_phase(teeth: int) -> float:
    """The phase that puts a tooth space, not a tooth, facing the pinion."""
    return 0.0 if teeth % 2 else pi / teeth


def _stamp(obj, values: dict) -> None:
    for name, value in values.items():
        obj.addProperty("App::PropertyFloat", name, "GearSizing")
        setattr(obj, name, float(value))


def build(model_path: str, sizing_path: str) -> None:
    sizing = json.loads(open(sizing_path, "r", encoding="utf-8").read())
    if sizing.get("status") != "sized":
        raise ValueError(
            f"sizing result status is {sizing.get('status')!r}, not 'sized'"
        )

    teeth = sizing["derived"]["teeth"]
    geometry = sizing["derived"]["geometry"]
    shaft = sizing["derived"]["shaft"]

    module_mm = float(geometry["module_mm"])
    pressure_angle_rad = float(geometry["pressure_angle_rad"])
    face_width_mm = float(geometry["face_width_mm"])
    centre_distance_mm = float(geometry["centre_distance_mm"])
    pinion_teeth = int(teeth["pinion"])
    gear_teeth = int(teeth["gear"])
    bore_mm = float(shaft["selected_diameter_mm"])

    doc = App.openDocument(model_path)
    for obj in list(doc.Objects):
        doc.removeObject(obj.Name)
    doc.recompute()

    pinion = doc.addObject("Part::Feature", "Pinion")
    pinion.Shape = _gear_solid(
        pinion_teeth, module_mm, pressure_angle_rad, face_width_mm, bore_mm, 0.0
    )
    _stamp(
        pinion,
        {
            "Module": module_mm,
            "Teeth": pinion_teeth,
            "FaceWidthMM": face_width_mm,
            "PitchDiameterMM": geometry["pinion_pitch_mm"],
            "BoreMM": bore_mm,
        },
    )

    gear = doc.addObject("Part::Feature", "Gear")
    gear.Shape = _gear_solid(
        gear_teeth,
        module_mm,
        pressure_angle_rad,
        face_width_mm,
        bore_mm,
        _mesh_phase(gear_teeth),
    )
    gear.Placement.Base = App.Vector(centre_distance_mm, 0, 0)
    _stamp(
        gear,
        {
            "Module": module_mm,
            "Teeth": gear_teeth,
            "FaceWidthMM": face_width_mm,
            "PitchDiameterMM": geometry["gear_pitch_mm"],
            "CentreDistanceMM": centre_distance_mm,
        },
    )

    doc.recompute()
    doc.save()
    App.closeDocument(doc.Name)


if __name__ == "__main__":
    build(sys.argv[-2], sys.argv[-1])
