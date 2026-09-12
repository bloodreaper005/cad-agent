from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, pi, sqrt
from typing import Any, Mapping

from .errors import GearSizingError


_QUALITY_NOTE = (
    "AGMA grade allowable stress numbers are conditional on the metallurgical "
    "quality controls of ANSI/AGMA 2101-D04 Annex A, including cleanliness, "
    "case depth, core hardness and microstructure. This engine cannot verify "
    "any of them; the grade is a declaration by the user."
)


@dataclass(frozen=True, slots=True)
class GearMaterial:
    """Allowable stress numbers and elastic constants for one gear member."""

    key: str
    designation: str
    treatment: str            # "through" | "surface"
    grade: str
    brinell: float
    bending_allowable_mpa: float      # sigma_FP, AGMA S_t
    contact_allowable_mpa: float      # sigma_HP, AGMA S_c
    elastic_modulus_mpa: float
    poisson_ratio: float
    life_curve: str           # selects the Y_N / Z_N family
    source: str
    life_curve_extrapolated: bool = False

    @property
    def quality_note(self) -> str:
        return _QUALITY_NOTE


@dataclass(frozen=True, slots=True)
class ShaftMaterial:
    key: str
    designation: str
    tensile_strength_mpa: float       # S_ut
    yield_strength_mpa: float         # S_y
    source: str


def through_hardened_allowables(brinell: float, grade: str) -> tuple[float, float]:
    """AGMA 2101-D04 hardness relations for through-hardened steel.

    Cross-checked against the US-unit originals: grade 1 gives 195/644 MPa at
    200 HB and grade 2 gives 324/960 MPa at 300 HB.
    """
    if brinell <= 0:
        raise GearSizingError("input.non_positive", "hardness must be positive")
    if grade == "1":
        return 0.533 * brinell + 88.3, 2.22 * brinell + 200.0
    if grade == "2":
        return 0.703 * brinell + 113.0, 2.41 * brinell + 237.0
    raise GearSizingError(
        "input.unknown_option",
        f"unknown through-hardened grade {grade!r}",
        {"valid": ["1", "2"]},
    )


# Carburised and case-hardened allowables by AGMA grade, MPa.
CARBURISED_ALLOWABLES: dict[str, tuple[float, float]] = {
    "1": (380.0, 1240.0),
    "2": (450.0, 1550.0),
    "3": (515.0, 1750.0),
}

_STEEL_E = 206800.0
_STEEL_NU = 0.30

# AGMA publishes cast-iron allowables in psi. They are declared here in their
# original units and converted, so the value in the table can be checked
# against the standard without reversing a rounding.
_PSI_TO_MPA = 0.00689475729


def _from_psi(bending_psi: float, contact_psi: float) -> tuple[float, float]:
    return bending_psi * _PSI_TO_MPA, contact_psi * _PSI_TO_MPA


CAST_IRON_ALLOWABLES_PSI: dict[str, tuple[float, float]] = {
    "grey_class_30": (8500.0, 50000.0),
    "grey_class_40": (13000.0, 65000.0),
    "ductile_80_55_06": (22000.0, 77000.0),
    "ductile_100_70_03": (27000.0, 92000.0),
}

# Only materials whose allowables were cross-checked against their US-unit
# originals are shipped. Every row below converts from a round published
# value, which is what makes it checkable against the standard.
#
# Nitrided steel is deliberately absent: its numbers could not be verified
# that way. Cast iron is present, but the AGMA stress-cycle curves are steel
# curves, so iron carries life_curve_extrapolated and the result says so.
# Anything else -- stainless, aluminium, bronze -- goes through
# custom_material() with allowables the user declares.
_GEAR_MATERIALS: tuple[GearMaterial, ...] = (
    GearMaterial(
        key="C45E_QT_200HB",
        designation="C45E / AISI 1045, through-hardened and tempered",
        treatment="through",
        grade="1",
        brinell=200.0,
        bending_allowable_mpa=through_hardened_allowables(200.0, "1")[0],
        contact_allowable_mpa=through_hardened_allowables(200.0, "1")[1],
        elastic_modulus_mpa=_STEEL_E,
        poisson_ratio=_STEEL_NU,
        life_curve="through_160HB",
        source="AGMA 2101-D04 grade 1 hardness relation",
    ),
    GearMaterial(
        key="42CrMo4_QT_300HB",
        designation="42CrMo4 / AISI 4140, through-hardened and tempered",
        treatment="through",
        grade="2",
        brinell=300.0,
        bending_allowable_mpa=through_hardened_allowables(300.0, "2")[0],
        contact_allowable_mpa=through_hardened_allowables(300.0, "2")[1],
        elastic_modulus_mpa=_STEEL_E,
        poisson_ratio=_STEEL_NU,
        life_curve="through_250HB",
        source="AGMA 2101-D04 grade 2 hardness relation",
    ),
    GearMaterial(
        key="16MnCr5_carburised_G1",
        designation="16MnCr5 case-carburised, 58-62 HRC",
        treatment="surface",
        grade="1",
        brinell=600.0,
        bending_allowable_mpa=CARBURISED_ALLOWABLES["1"][0],
        contact_allowable_mpa=CARBURISED_ALLOWABLES["1"][1],
        elastic_modulus_mpa=_STEEL_E,
        poisson_ratio=_STEEL_NU,
        life_curve="carburised",
        source="AGMA 2101-D04 carburised grade 1",
    ),
    GearMaterial(
        key="20MnCr5_carburised_G2",
        designation="20MnCr5 case-carburised, 58-62 HRC",
        treatment="surface",
        grade="2",
        brinell=600.0,
        bending_allowable_mpa=CARBURISED_ALLOWABLES["2"][0],
        contact_allowable_mpa=CARBURISED_ALLOWABLES["2"][1],
        elastic_modulus_mpa=_STEEL_E,
        poisson_ratio=_STEEL_NU,
        life_curve="carburised",
        source="AGMA 2101-D04 carburised grade 2",
    ),
    GearMaterial(
        key="EN-GJL-250_grey_iron",
        designation="EN-GJL-250 grey cast iron, ASTM A48 class 30 equivalent",
        treatment="through",
        grade="class 30",
        brinell=180.0,
        bending_allowable_mpa=_from_psi(*CAST_IRON_ALLOWABLES_PSI["grey_class_30"])[0],
        contact_allowable_mpa=_from_psi(*CAST_IRON_ALLOWABLES_PSI["grey_class_30"])[1],
        # Grey iron has no true elastic modulus; this is a secant value at low
        # stress and the figure varies with section and stress level.
        elastic_modulus_mpa=103000.0,
        poisson_ratio=0.26,
        life_curve="through_160HB",
        source="AGMA 2101-D04 cast iron, 8500 / 50000 psi",
        life_curve_extrapolated=True,
    ),
    GearMaterial(
        key="EN-GJS-600-3_ductile_iron",
        designation="EN-GJS-600-3 ductile iron, ASTM A536 80-55-06 equivalent",
        treatment="through",
        grade="80-55-06",
        brinell=190.0,
        bending_allowable_mpa=_from_psi(
            *CAST_IRON_ALLOWABLES_PSI["ductile_80_55_06"]
        )[0],
        contact_allowable_mpa=_from_psi(
            *CAST_IRON_ALLOWABLES_PSI["ductile_80_55_06"]
        )[1],
        elastic_modulus_mpa=174000.0,
        poisson_ratio=0.275,
        life_curve="through_160HB",
        source="AGMA 2101-D04 cast iron, 22000 / 77000 psi",
        life_curve_extrapolated=True,
    ),
)

GEAR_MATERIALS: dict[str, GearMaterial] = {item.key: item for item in _GEAR_MATERIALS}

_SHAFT_MATERIALS: tuple[ShaftMaterial, ...] = (
    ShaftMaterial("C45E_QT", "C45E quenched and tempered", 700.0, 490.0, "EN 10083-2"),
    ShaftMaterial("25CrMo4_QT", "25CrMo4 quenched and tempered", 900.0, 650.0, "EN 10083-3"),
    ShaftMaterial("42CrMo4_QT", "42CrMo4 quenched and tempered", 1000.0, 750.0, "EN 10083-3"),
    ShaftMaterial("34CrNiMo6_QT", "34CrNiMo6 quenched and tempered", 1100.0, 900.0, "EN 10083-3"),
)

SHAFT_MATERIALS: dict[str, ShaftMaterial] = {
    item.key: item for item in _SHAFT_MATERIALS
}


_REQUIRED_CUSTOM_FIELDS = (
    "designation",
    "bending_allowable_mpa",
    "contact_allowable_mpa",
    "elastic_modulus_mpa",
    "poisson_ratio",
    "brinell",
)

DECLARED_SOURCE_PREFIX = "declared by the user"


def custom_material(key: str, data: Mapping[str, Any]) -> GearMaterial:
    """Build a material from allowables the user supplies.

    AGMA 2101-D04 tabulates allowable stress numbers for through-hardened,
    carburised and nitrided steel and for cast iron. Anything outside that —
    stainless, aluminium, bronze, a proprietary alloy — has to come from the
    person doing the design, and the result says so rather than implying the
    standard backs it.
    """
    missing = [field for field in _REQUIRED_CUSTOM_FIELDS if field not in data]
    if missing:
        raise GearSizingError(
            "input.incomplete_material",
            f"custom material {key!r} is missing {', '.join(missing)}",
            {"required": list(_REQUIRED_CUSTOM_FIELDS), "missing": missing},
        )
    for field in (
        "bending_allowable_mpa",
        "contact_allowable_mpa",
        "elastic_modulus_mpa",
        "brinell",
    ):
        value = float(data[field])
        if not isfinite(value) or value <= 0:
            raise GearSizingError(
                "input.non_positive",
                f"custom material {key!r} has a non-positive or non-finite {field}",
                {field: data[field]},
            )
    poisson = float(data["poisson_ratio"])
    if not 0.0 < poisson < 0.5:
        raise GearSizingError(
            "input.out_of_range",
            f"custom material {key!r} has an implausible Poisson ratio",
            {"poisson_ratio": poisson},
        )

    treatment = str(data.get("treatment", "through"))
    if treatment not in {"through", "surface"}:
        raise GearSizingError(
            "input.unknown_option",
            f"custom material {key!r} treatment must be through or surface",
            {"valid": ["through", "surface"]},
        )
    life_curve = str(data.get("life_curve", "through_250HB"))
    note = str(data.get("source", "")).strip()

    return GearMaterial(
        key=key,
        designation=str(data["designation"]),
        treatment=treatment,
        grade=str(data.get("grade", "declared")),
        brinell=float(data["brinell"]),
        bending_allowable_mpa=float(data["bending_allowable_mpa"]),
        contact_allowable_mpa=float(data["contact_allowable_mpa"]),
        elastic_modulus_mpa=float(data["elastic_modulus_mpa"]),
        poisson_ratio=poisson,
        life_curve=life_curve,
        source=f"{DECLARED_SOURCE_PREFIX}; {note}" if note else DECLARED_SOURCE_PREFIX,
    )


def is_declared(material: GearMaterial) -> bool:
    """Whether the allowables came from the user rather than from AGMA."""
    return material.source.startswith(DECLARED_SOURCE_PREFIX)


def gear_material(
    key: str, custom: Mapping[str, Mapping[str, Any]] | None = None
) -> GearMaterial:
    if custom and key in custom:
        return custom_material(key, custom[key])
    try:
        return GEAR_MATERIALS[key]
    except KeyError:
        raise GearSizingError(
            "input.unknown_material",
            f"unknown gear material {key!r}",
            {
                "valid": sorted(GEAR_MATERIALS),
                "hint": (
                    "materials outside the AGMA tables, such as stainless or "
                    "aluminium, can be supplied with their own allowables"
                ),
            },
        ) from None


def shaft_material(key: str) -> ShaftMaterial:
    try:
        return SHAFT_MATERIALS[key]
    except KeyError:
        raise GearSizingError(
            "input.unknown_material",
            f"unknown shaft material {key!r}",
            {"valid": sorted(SHAFT_MATERIALS)},
        ) from None


def elastic_coefficient(pinion: GearMaterial, gear: GearMaterial) -> float:
    """Z_E in sqrt(MPa), AGMA 2101-D04 eq. 21.

    Computed from the elastic constants rather than taken as the tabulated
    191, which is the rounded US value: steel on steel gives 190.18.
    """
    compliance = (
        (1.0 - pinion.poisson_ratio**2) / pinion.elastic_modulus_mpa
        + (1.0 - gear.poisson_ratio**2) / gear.elastic_modulus_mpa
    )
    return sqrt(1.0 / (pi * compliance))


__all__ = [
    "CARBURISED_ALLOWABLES",
    "CAST_IRON_ALLOWABLES_PSI",
    "DECLARED_SOURCE_PREFIX",
    "GEAR_MATERIALS",
    "SHAFT_MATERIALS",
    "GearMaterial",
    "ShaftMaterial",
    "custom_material",
    "elastic_coefficient",
    "gear_material",
    "is_declared",
    "shaft_material",
    "through_hardened_allowables",
]
