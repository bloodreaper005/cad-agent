from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite, pi, radians, tan

from . import factors as rating
from .bearings import BearingResult, required_dynamic_capacity
from .errors import GearSizingError
from .geometry import MeshGeometry, build_mesh, select_teeth
from .materials import (
    GearMaterial,
    elastic_coefficient,
    gear_material,
    is_declared,
    shaft_material,
)
from .shaft import size_shaft
from .standards import (
    BASIC_RACK_DEDENDUM_FACTOR,
    BASIC_RACK_TIP_RADIUS_FACTOR,
    module_series,
)
from .strength import (
    StrengthResult,
    bending_stress_mpa,
    contact_requirement,
    contact_stress_mpa,
    load_cycles,
    rate,
)
from .tooth_form import ToothForm, contact_geometry_factor, tooth_form


TORQUE_CONSTANT = 9549.296586  # 30000/pi, kW and rpm to N.m

LIMITATIONS: tuple[str, ...] = (
    "Preliminary sizing evidence. Not FEA, not a strength certification, not "
    "manufacturing release, and not standards compliance.",
    "Scuffing and flash temperature are not evaluated (AGMA 925, ISO/TR 13989).",
    "Micropitting is not evaluated (ISO/TR 15144). Wear is not evaluated.",
    "Thermal rating is not evaluated (AGMA 6006, ISO/TR 14179).",
    "Lubricant film thickness, backlash, tip relief and profile shift are not "
    "designed.",
    "Shaft deflection and the mesh misalignment it causes are not computed; "
    "the load distribution factor is empirical.",
    "The bearing result is a required dynamic capacity, not a catalogue "
    "selection.",
    "AGMA grade allowable stress numbers assume metallurgical quality controls "
    "this engine cannot verify.",
)

UNVERIFIED_COEFFICIENTS: tuple[str, ...] = (
    "Bending stress-cycle (Y_N) short-life coefficients are digitized fits; "
    "they satisfy the continuity self-check at 3e6 cycles but were not read "
    "from the source standard.",
    "Mesh-alignment (K_Hma) coefficients were converted from the inch table.",
    "Deep-groove ball equivalent-load coefficients are mid-range placeholders; "
    "a spur mesh carries no axial load, so they do not bind here.",
)


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    status: str
    mandatory: bool
    message: str
    actual: float | None = None
    expected: float | None = None


@dataclass(frozen=True, slots=True)
class Assumption:
    id: str
    value: object
    unit: str
    source: str
    why: str


@dataclass(frozen=True, slots=True)
class Iteration:
    module_mm: float
    face_width_mm: float
    pinion_bending_safety: float
    gear_bending_safety: float
    contact_safety_pinion: float
    contact_safety_gear: float
    accepted: bool
    governing: str


@dataclass(frozen=True, slots=True)
class GearDriveInput:
    power_kw: float
    pinion_speed_rpm: float
    gear_speed_rpm: float
    pinion_material: str
    gear_material: str
    duty: str
    life_hours: float
    safety_factor: float
    gear_type: str = "spur"
    normal_pressure_angle_deg: float | None = None
    face_width_ratio: float | None = None
    forced_module_mm: float | None = None
    forced_pinion_teeth: int | None = None
    forced_gear_teeth: int | None = None
    driver: str = "electric_motor"
    quality_grade_qv: int = 7
    reliability: float = 0.99
    mesh_alignment_class: str = "commercial"
    contact_safety_basis: str = "stress"
    module_series_name: str = "iso54_series1"
    min_module_mm: float = 1.0
    max_module_mm: float = 20.0
    min_pinion_teeth: int = 17
    ratio_tolerance: float = 0.03
    pinion_teeth_search_span: int = 6
    prefer_hunting: bool = True
    rim_thickness_mm: float | None = None
    oil_temperature_c: float = 90.0
    mesh_contacts_per_rev: int = 1
    crowned: bool = False
    shaft_material_key: str = "42CrMo4_QT"
    gear_position_ratio: float = 0.5
    bearing_span_mm: float | None = None
    bearing_span_shaft_factor: float = 3.0
    keyway_present: bool = True
    stress_concentration: str = "profile_keyway"
    bearing_type: str = "deep_groove_ball"
    bearing_reliability: float = 0.90
    custom_materials: dict | None = None


@dataclass(frozen=True, slots=True)
class GearDriveResult:
    status: str
    given: dict
    assumptions: list[Assumption]
    derived: dict
    checks: list[Check]
    iterations: list[Iteration]
    warnings: list[str]
    limitations: tuple[str, ...] = LIMITATIONS
    unverified: tuple[str, ...] = UNVERIFIED_COEFFICIENTS


def _require_positive(value: float, label: str) -> float:
    if not isfinite(value) or value <= 0.0:
        raise GearSizingError(
            "input.non_positive", f"{label} must be a positive finite number",
            {label: value},
        )
    return value


def _validate(spec: GearDriveInput) -> None:
    _require_positive(spec.power_kw, "power_kw")
    _require_positive(spec.pinion_speed_rpm, "pinion_speed_rpm")
    _require_positive(spec.gear_speed_rpm, "gear_speed_rpm")
    _require_positive(spec.life_hours, "life_hours")
    _require_positive(spec.safety_factor, "safety_factor")
    if spec.gear_type != "spur":
        raise GearSizingError(
            "input.out_of_range",
            f"only spur drives are supported, not {spec.gear_type!r}",
            {"gear_type": spec.gear_type},
        )
    if spec.contact_safety_basis not in {"stress", "load"}:
        raise GearSizingError(
            "input.unknown_option",
            f"unknown contact safety basis {spec.contact_safety_basis!r}",
            {"valid": ["stress", "load"]},
        )
    angle = spec.normal_pressure_angle_deg
    if angle is not None and not 14.5 <= angle <= 25.0:
        raise GearSizingError(
            "input.out_of_range",
            "pressure angle must be between 14.5 and 25 degrees",
            {"normal_pressure_angle_deg": angle},
        )
    if spec.pinion_speed_rpm <= spec.gear_speed_rpm:
        raise GearSizingError(
            "ratio.unreachable",
            "this engine sizes speed reducers; the pinion must turn faster "
            "than the gear",
            {
                "pinion_speed_rpm": spec.pinion_speed_rpm,
                "gear_speed_rpm": spec.gear_speed_rpm,
            },
        )


def size_gear_drive(spec: GearDriveInput) -> GearDriveResult:
    """Size a spur reduction from duty inputs.

    Iterates the module series upward and accepts the first module where both
    members clear bending and contact at the requested safety factor. Every
    rejected module stays in the record: a failed attempt is evidence.
    """
    _validate(spec)

    warnings: list[str] = []
    checks: list[Check] = []
    assumptions: list[Assumption] = []

    def assume(key: str, value: object, unit: str, source: str, why: str) -> None:
        assumptions.append(Assumption(key, value, unit, source, why))

    pressure_angle_deg = spec.normal_pressure_angle_deg
    if pressure_angle_deg is None:
        pressure_angle_deg = 20.0
        assume(
            "pressure_angle", 20.0, "deg", "ISO 53 profile A",
            "not supplied",
        )
    pressure_angle = radians(pressure_angle_deg)

    face_width_ratio = spec.face_width_ratio
    if face_width_ratio is None:
        face_width_ratio = 1.0
        assume(
            "face_width_ratio", 1.0, "b/d1", "common enclosed-drive proportion",
            "not supplied",
        )

    assume(
        "contact_safety_basis", spec.contact_safety_basis, "-",
        "engineering judgement, not a standard",
        "Hertzian stress varies as the square root of load, so a stress-basis "
        "factor is more conservative than a load-basis one; this choice can "
        "move the accepted module by one series step",
    )
    assume(
        "quality_grade_qv", spec.quality_grade_qv, "AGMA Qv",
        "ANSI/AGMA 2101-D04", "not supplied",
    )
    assume(
        "mesh_alignment_class", spec.mesh_alignment_class, "-",
        "ANSI/AGMA 2101-D04 empirical method", "not supplied",
    )
    assume(
        "reliability", spec.reliability, "-", "ANSI/AGMA 2101-D04 Table 10",
        "not supplied",
    )
    assume(
        "basic_rack", f"h_fP*={BASIC_RACK_DEDENDUM_FACTOR}, "
        f"rho_fP*={BASIC_RACK_TIP_RADIUS_FACTOR}", "x module", "ISO 53 profile A",
        "tooth form is generated from the standard basic rack",
    )
    assume(
        "profile_shift", 0.0, "x", "this engine sizes standard gears only",
        "profile shift is not designed in this version",
    )

    pinion_metal = gear_material(spec.pinion_material, spec.custom_materials)
    gear_metal = gear_material(spec.gear_material, spec.custom_materials)
    shaft_metal = shaft_material(spec.shaft_material_key)

    for role, metal in (("pinion", pinion_metal), ("gear", gear_metal)):
        if metal.life_curve_extrapolated:
            warnings.append(
                f"{role} material {metal.key!r} is rated with the AGMA "
                "stress-cycle curves, which are steel curves; applying them to "
                "cast iron is an extrapolation, so treat the life factors as "
                "indicative rather than rated"
            )
        if not is_declared(metal):
            continue
        warnings.append(
            f"{role} material {metal.key!r} uses allowable stresses you "
            "declared, not values from ANSI/AGMA 2101-D04; the safety factors "
            "below are only as good as those numbers"
        )
        assume(
            f"{role}_material_allowables",
            f"S_t={metal.bending_allowable_mpa} MPa, "
            f"S_c={metal.contact_allowable_mpa} MPa",
            "MPa",
            metal.source,
            "this material is not tabulated in the standard",
        )

    target_ratio = spec.pinion_speed_rpm / spec.gear_speed_rpm
    teeth, teeth_warnings = select_teeth(
        target_ratio=target_ratio,
        pressure_angle_rad=pressure_angle,
        min_pinion_teeth=spec.min_pinion_teeth,
        ratio_tolerance=spec.ratio_tolerance,
        search_span=spec.pinion_teeth_search_span,
        prefer_hunting=spec.prefer_hunting,
        forced_pinion_teeth=spec.forced_pinion_teeth,
        forced_gear_teeth=spec.forced_gear_teeth,
    )
    warnings.extend(teeth_warnings)
    if abs(teeth.ratio_error) > 0.01:
        warnings.append(
            f"actual ratio {teeth.ratio:.4f} differs from the requested "
            f"{target_ratio:.4f} by {teeth.ratio_error:+.2%}; output speed will "
            f"be {spec.pinion_speed_rpm / teeth.ratio:.1f} rpm"
        )

    actual_gear_speed = spec.pinion_speed_rpm / teeth.ratio
    torque_nm = TORQUE_CONSTANT * spec.power_kw / spec.pinion_speed_rpm
    overload = rating.overload_factor(spec.duty, spec.driver)
    reliability = rating.reliability_factor(spec.reliability)
    temperature = rating.temperature_factor(spec.oil_temperature_c)
    elastic = elastic_coefficient(pinion_metal, gear_metal)
    geometry_factor = contact_geometry_factor(pressure_angle, teeth.ratio)
    contact_target = contact_requirement(spec.safety_factor, spec.contact_safety_basis)

    pinion_cycles = load_cycles(
        spec.pinion_speed_rpm, spec.life_hours, spec.mesh_contacts_per_rev
    )
    gear_cycles = load_cycles(
        actual_gear_speed, spec.life_hours, spec.mesh_contacts_per_rev
    )

    both_through = (
        pinion_metal.treatment == "through" and gear_metal.treatment == "through"
    )
    hardness_ratio = rating.hardness_ratio_factor(
        pinion_metal.brinell, gear_metal.brinell, teeth.ratio, both_through
    )

    candidates = [
        value
        for value in module_series(spec.module_series_name)
        if spec.min_module_mm <= value <= spec.max_module_mm
    ]
    if spec.forced_module_mm is not None:
        candidates = [spec.forced_module_mm]

    iterations: list[Iteration] = []
    accepted: dict | None = None

    for module in candidates:
        mesh = build_mesh(
            module_mm=module,
            teeth=teeth,
            pressure_angle_rad=pressure_angle,
            face_width_ratio=face_width_ratio,
        )
        velocity = pi * mesh.pinion_pitch_mm * spec.pinion_speed_rpm / 60000.0
        tangential = 2000.0 * torque_nm / mesh.pinion_pitch_mm
        radial = tangential * tan(pressure_angle)
        dynamic = rating.dynamic_factor(velocity, spec.quality_grade_qv)
        size = max(
            rating.size_factor(module, pinion_metal.treatment == "surface"),
            rating.size_factor(module, gear_metal.treatment == "surface"),
        )
        distribution, proportion, alignment = rating.load_distribution_factor(
            face_width_mm=mesh.face_width_mm,
            pitch_diameter_mm=mesh.pinion_pitch_mm,
            alignment_class=spec.mesh_alignment_class,
            crowned=spec.crowned,
        )
        rim = rating.rim_thickness_factor(spec.rim_thickness_mm, module)

        try:
            pinion_form = tooth_form(
                teeth=teeth.pinion,
                pressure_angle_rad=pressure_angle,
                contact_ratio=mesh.transverse_contact_ratio,
                tip_diameter_over_module=mesh.pinion_tip_mm / module,
            )
            gear_form = tooth_form(
                teeth=teeth.gear,
                pressure_angle_rad=pressure_angle,
                contact_ratio=mesh.transverse_contact_ratio,
                tip_diameter_over_module=mesh.gear_tip_mm / module,
            )
        except GearSizingError:
            if spec.forced_module_mm is not None:
                raise
            continue

        def bending_for(form: ToothForm, metal: GearMaterial, cycles: float) -> StrengthResult:
            stress = bending_stress_mpa(
                tangential_force_n=tangential,
                face_width_mm=mesh.face_width_mm,
                module_mm=module,
                form_factor=form.form_factor,
                stress_correction=form.stress_correction,
                overload=overload,
                dynamic=dynamic,
                size=size,
                load_distribution=distribution,
                rim=rim,
            )
            return rate(
                stress_mpa=stress,
                allowable_stress_number_mpa=metal.bending_allowable_mpa,
                life_factor=rating.bending_stress_cycle_factor(
                    cycles, metal.life_curve
                ),
                cycles=cycles,
                temperature=temperature,
                reliability=reliability,
            )

        pinion_bending = bending_for(pinion_form, pinion_metal, pinion_cycles)
        gear_bending = bending_for(gear_form, gear_metal, gear_cycles)

        contact_stress = contact_stress_mpa(
            tangential_force_n=tangential,
            face_width_mm=mesh.face_width_mm,
            pinion_pitch_mm=mesh.pinion_pitch_mm,
            elastic_coefficient=elastic,
            geometry_factor=geometry_factor,
            overload=overload,
            dynamic=dynamic,
            size=size,
            load_distribution=distribution,
        )
        pinion_contact = rate(
            stress_mpa=contact_stress,
            allowable_stress_number_mpa=pinion_metal.contact_allowable_mpa,
            life_factor=rating.contact_stress_cycle_factor(pinion_cycles),
            cycles=pinion_cycles,
            temperature=temperature,
            reliability=reliability,
        )
        gear_contact = rate(
            stress_mpa=contact_stress,
            allowable_stress_number_mpa=gear_metal.contact_allowable_mpa,
            life_factor=rating.contact_stress_cycle_factor(gear_cycles),
            cycles=gear_cycles,
            temperature=temperature,
            reliability=reliability,
            hardness_ratio=hardness_ratio,
        )

        margins = {
            "bending.pinion": pinion_bending.safety_factor / spec.safety_factor,
            "bending.gear": gear_bending.safety_factor / spec.safety_factor,
            "contact.pinion": pinion_contact.safety_factor / contact_target,
            "contact.gear": gear_contact.safety_factor / contact_target,
        }
        governing = min(margins, key=lambda key: margins[key])
        passes = all(value >= 1.0 for value in margins.values())
        velocity_ok = velocity <= rating.max_pitch_velocity(spec.quality_grade_qv)
        width_ok = mesh.face_width_mm / mesh.pinion_pitch_mm <= 2.0
        ratio_ok = 1.0 < mesh.transverse_contact_ratio <= 2.0
        accept = passes and velocity_ok and width_ok and ratio_ok

        iterations.append(
            Iteration(
                module_mm=module,
                face_width_mm=mesh.face_width_mm,
                pinion_bending_safety=pinion_bending.safety_factor,
                gear_bending_safety=gear_bending.safety_factor,
                contact_safety_pinion=pinion_contact.safety_factor,
                contact_safety_gear=gear_contact.safety_factor,
                accepted=accept,
                governing=governing,
            )
        )

        if accept:
            accepted = {
                "mesh": mesh,
                "velocity": velocity,
                "tangential": tangential,
                "radial": radial,
                "dynamic": dynamic,
                "size": size,
                "distribution": distribution,
                "proportion": proportion,
                "alignment": alignment,
                "rim": rim,
                "pinion_form": pinion_form,
                "gear_form": gear_form,
                "pinion_bending": pinion_bending,
                "gear_bending": gear_bending,
                "pinion_contact": pinion_contact,
                "gear_contact": gear_contact,
                "governing": governing,
            }
            break

    given = {
        "power_kw": spec.power_kw,
        "pinion_speed_rpm": spec.pinion_speed_rpm,
        "gear_speed_rpm": spec.gear_speed_rpm,
        "gear_type": spec.gear_type,
        "pinion_material": spec.pinion_material,
        "gear_material": spec.gear_material,
        "shaft_material": spec.shaft_material_key,
        "duty": spec.duty,
        "driver": spec.driver,
        "life_hours": spec.life_hours,
        "safety_factor": spec.safety_factor,
    }

    if accepted is None:
        checks.append(
            Check(
                id="module.series_exhausted",
                status="failed",
                mandatory=True,
                message=(
                    "no module in the selected series satisfies the requested "
                    "safety factor within the module bounds"
                ),
                actual=iterations[-1].module_mm if iterations else None,
                expected=spec.safety_factor,
            )
        )
        return GearDriveResult(
            status="rejected",
            given=given,
            assumptions=assumptions,
            derived={
                "teeth": asdict(teeth),
                "target_ratio": target_ratio,
                "torque_nm": torque_nm,
            },
            checks=checks,
            iterations=iterations,
            warnings=warnings,
        )

    mesh: MeshGeometry = accepted["mesh"]
    shaft_result, shaft_warnings = size_shaft(
        tangential_force_n=accepted["tangential"],
        radial_force_n=accepted["radial"],
        torque_nmm=torque_nm * 1000.0,
        face_width_mm=mesh.face_width_mm,
        root_diameter_mm=mesh.pinion_root_mm,
        module_mm=mesh.module_mm,
        material=shaft_metal,
        duty=spec.duty,
        safety_factor=spec.safety_factor,
        reliability=spec.reliability,
        position_ratio=spec.gear_position_ratio,
        span_mm=spec.bearing_span_mm,
        span_shaft_factor=spec.bearing_span_shaft_factor,
        keyway_present=spec.keyway_present,
        concentration=spec.stress_concentration,
    )
    warnings.extend(shaft_warnings)

    bearing: BearingResult = required_dynamic_capacity(
        radial_n=max(
            shaft_result.loads.reaction_a_n, shaft_result.loads.reaction_b_n
        ),
        axial_n=0.0,
        speed_rpm=spec.pinion_speed_rpm,
        life_hours=spec.life_hours,
        bearing_type=spec.bearing_type,
        reliability=spec.bearing_reliability,
    )
    warnings.append(
        "bearing sizing reports a required dynamic capacity; selecting a "
        "catalogue bearing needs its static rating to settle the axial "
        "coefficients"
    )

    for name, result, target in (
        ("bending.pinion", accepted["pinion_bending"], spec.safety_factor),
        ("bending.gear", accepted["gear_bending"], spec.safety_factor),
        ("contact.pinion", accepted["pinion_contact"], contact_target),
        ("contact.gear", accepted["gear_contact"], contact_target),
    ):
        checks.append(
            Check(
                id=name,
                status="passed" if result.safety_factor >= target else "failed",
                mandatory=True,
                message=f"{name.replace('.', ' ')} safety factor meets the requirement",
                actual=result.safety_factor,
                expected=target,
            )
        )

    checks.append(
        Check(
            id="kinematics.pitch_line_velocity",
            status="passed",
            mandatory=True,
            message="pitch line velocity is inside the dynamic factor range",
            actual=accepted["velocity"],
            expected=rating.max_pitch_velocity(spec.quality_grade_qv),
        )
    )
    checks.append(
        Check(
            id="geometry.contact_ratio_transverse",
            status="passed",
            mandatory=True,
            message="transverse contact ratio keeps at least one pair in mesh",
            actual=mesh.transverse_contact_ratio,
            expected=1.0,
        )
    )
    bore_ok = shaft_result.selected_diameter_mm <= shaft_result.max_bore_from_root_mm
    checks.append(
        Check(
            id="shaft.rim_thickness",
            status="passed" if bore_ok else "failed",
            mandatory=True,
            message=(
                "pinion bore leaves enough rim under the tooth root"
                if bore_ok
                else "pinion bore leaves too little rim; use an integral pinion shaft"
            ),
            actual=shaft_result.selected_diameter_mm,
            expected=shaft_result.max_bore_from_root_mm,
        )
    )
    if not bore_ok:
        warnings.append(
            "the selected shaft diameter does not leave 2.5 modules of rim "
            "beneath the tooth root; make the pinion integral with the shaft"
        )

    derived = {
        "teeth": asdict(teeth),
        "target_ratio": target_ratio,
        "actual_gear_speed_rpm": actual_gear_speed,
        "torque_nm": torque_nm,
        "geometry": asdict(mesh),
        "forces": {
            "tangential_n": accepted["tangential"],
            "radial_n": accepted["radial"],
            "axial_n": 0.0,
            "pitch_velocity_ms": accepted["velocity"],
        },
        "factors": {
            "overload_ko": overload,
            "dynamic_kv": accepted["dynamic"],
            "size_ks": accepted["size"],
            "load_distribution_kh": accepted["distribution"],
            "pinion_proportion_khpf": accepted["proportion"],
            "mesh_alignment_khma": accepted["alignment"],
            "rim_kb": rim if (rim := accepted["rim"]) else 1.0,
            "temperature_kt": temperature,
            "reliability_kr": reliability,
            "elastic_ze": elastic,
            "geometry_zi": geometry_factor,
            "hardness_ratio_zw": hardness_ratio,
        },
        "tooth_form": {
            "pinion": asdict(accepted["pinion_form"]),
            "gear": asdict(accepted["gear_form"]),
        },
        "bending": {
            "pinion": asdict(accepted["pinion_bending"]),
            "gear": asdict(accepted["gear_bending"]),
        },
        "contact": {
            "pinion": asdict(accepted["pinion_contact"]),
            "gear": asdict(accepted["gear_contact"]),
            "required_safety_factor": contact_target,
        },
        "shaft": asdict(shaft_result),
        "bearings": asdict(bearing),
        "governing_check": accepted["governing"],
    }

    failed = [item for item in checks if item.mandatory and item.status != "passed"]
    return GearDriveResult(
        status="rejected" if failed else "sized",
        given=given,
        assumptions=assumptions,
        derived=derived,
        checks=checks,
        iterations=iterations,
        warnings=warnings,
    )


__all__ = [
    "Assumption",
    "Check",
    "GearDriveInput",
    "GearDriveResult",
    "Iteration",
    "LIMITATIONS",
    "TORQUE_CONSTANT",
    "UNVERIFIED_COEFFICIENTS",
    "size_gear_drive",
]
