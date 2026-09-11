from __future__ import annotations

import json
from math import isclose, radians

import pytest

from mech_cad_design_agent.gear_sizing import (
    GearDriveInput,
    GearSizingError,
    size_gear_drive,
    to_json,
    to_markdown,
)
from mech_cad_design_agent.gear_sizing import factors, materials
from mech_cad_design_agent.gear_sizing.geometry import select_teeth, undercut_floor
from mech_cad_design_agent.gear_sizing.tooth_form import (
    contact_geometry_factor,
    tooth_form,
)


ANGLE = radians(20.0)


def _drive(**overrides: object) -> GearDriveInput:
    arguments: dict[str, object] = {
        "power_kw": 7.5,
        "pinion_speed_rpm": 1450.0,
        "gear_speed_rpm": 480.0,
        "pinion_material": "20MnCr5_carburised_G2",
        "gear_material": "20MnCr5_carburised_G2",
        "duty": "moderate",
        "life_hours": 20000.0,
        "safety_factor": 1.5,
    }
    arguments.update(overrides)
    return GearDriveInput(**arguments)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Anchors against published values. These are the tests that would catch a
# transcription error in the tooth-form or factor equations.
# ---------------------------------------------------------------------------


def test_tooth_form_matches_the_published_iso_chart_values() -> None:
    """z=20, x=0, 20 degrees, tip load: ISO 6336-3 tabulates 2.80 and 1.55."""
    form = tooth_form(
        teeth=20,
        pressure_angle_rad=ANGLE,
        contact_ratio=1.6,
        tip_diameter_over_module=22.0,
        load_at_tip=True,
    )
    assert isclose(form.form_factor, 2.800, rel_tol=1e-3)
    assert isclose(form.stress_correction, 1.5525, rel_tol=1e-3)
    assert isclose(form.root_chord, 1.94475, rel_tol=1e-4)
    assert isclose(form.root_fillet_radius, 0.57295, rel_tol=1e-4)


def test_contact_stress_cycle_factor_normalises_at_ten_million() -> None:
    """Z_N is defined as unity at 1e7 cycles; both branches must meet there."""
    assert isclose(factors.contact_stress_cycle_factor(1.0e7), 1.0, rel_tol=1e-4)
    assert isclose(factors.contact_stress_cycle_factor(9.99e6), 1.0, rel_tol=1e-3)


def test_bending_life_curves_are_continuous_at_three_million() -> None:
    """Every short-life curve must meet the long-life curve at 3e6 cycles.

    A mistyped coefficient in the digitized fits shows up here as a step.
    """
    long_life = factors.bending_stress_cycle_factor(3.0e6, "carburised")
    assert isclose(long_life, 1.0396, rel_tol=1e-3)
    for curve in ("carburised", "through_250HB", "through_160HB"):
        short = factors.bending_stress_cycle_factor(2.999999e6, curve)
        assert isclose(short, long_life, rel_tol=3e-4), curve


def test_elastic_coefficient_is_computed_not_tabulated() -> None:
    steel = materials.gear_material("20MnCr5_carburised_G2")
    assert isclose(materials.elastic_coefficient(steel, steel), 190.18, rel_tol=1e-4)


def test_reliability_factor_reproduces_the_published_table() -> None:
    assert isclose(factors.reliability_factor(0.99), 1.002, rel_tol=1e-3)
    assert isclose(factors.reliability_factor(0.999), 1.253, rel_tol=1e-3)
    assert isclose(factors.reliability_factor(0.9999), 1.504, rel_tol=1e-3)


def test_through_hardened_allowables_match_their_us_originals() -> None:
    bending, contact = materials.through_hardened_allowables(200.0, "1")
    assert isclose(bending, 194.9, rel_tol=1e-3)
    assert isclose(contact, 644.0, rel_tol=1e-3)
    bending, contact = materials.through_hardened_allowables(300.0, "2")
    assert isclose(bending, 323.9, rel_tol=1e-3)
    assert isclose(contact, 960.0, rel_tol=1e-3)


# ---------------------------------------------------------------------------
# The worked example, verified by independent hand calculation.
# ---------------------------------------------------------------------------


def test_worked_example_reproduces_every_derived_value() -> None:
    result = size_gear_drive(_drive())
    assert result.status == "sized"

    derived = result.derived
    teeth = derived["teeth"]
    mesh = derived["geometry"]
    forces = derived["forces"]

    assert (teeth["pinion"], teeth["gear"]) == (17, 52)
    assert teeth["hunting"] is True
    assert isclose(teeth["ratio"], 3.05882, rel_tol=1e-5)
    assert isclose(teeth["ratio_error"], 0.012576, rel_tol=1e-3)

    assert mesh["module_mm"] == 3.0
    assert mesh["face_width_mm"] == 51.0
    assert isclose(mesh["pinion_pitch_mm"], 51.0, rel_tol=1e-9)
    assert isclose(mesh["gear_pitch_mm"], 156.0, rel_tol=1e-9)
    assert isclose(mesh["centre_distance_mm"], 103.5, rel_tol=1e-9)
    assert isclose(mesh["transverse_contact_ratio"], 1.6381, rel_tol=1e-4)

    assert isclose(derived["torque_nm"], 49.3929, rel_tol=1e-5)
    assert isclose(forces["tangential_n"], 1937.0, rel_tol=1e-4)
    assert isclose(forces["radial_n"], 705.00, rel_tol=1e-4)
    assert isclose(forces["pitch_velocity_ms"], 3.8720, rel_tol=1e-4)
    assert forces["axial_n"] == 0.0

    assert isclose(derived["bending"]["pinion"]["stress_mpa"], 91.740, rel_tol=1e-4)
    assert isclose(derived["bending"]["gear"]["stress_mpa"], 83.766, rel_tol=1e-4)
    assert isclose(derived["bending"]["pinion"]["safety_factor"], 4.1442, rel_tol=1e-4)
    assert isclose(derived["bending"]["gear"]["safety_factor"], 4.7056, rel_tol=1e-4)

    assert isclose(derived["contact"]["pinion"]["stress_mpa"], 734.33, rel_tol=1e-4)
    assert isclose(derived["contact"]["pinion"]["safety_factor"], 1.8710, rel_tol=1e-4)
    assert isclose(derived["contact"]["gear"]["safety_factor"], 1.9197, rel_tol=1e-4)
    assert derived["governing_check"] == "contact.pinion"


def test_worked_example_sizes_the_shaft_and_bearings() -> None:
    derived = size_gear_drive(_drive()).derived
    shaft = derived["shaft"]

    assert isclose(shaft["loads"]["span_mm"], 115.0, rel_tol=1e-9)
    assert isclose(shaft["loads"]["bending_moment_nmm"], 59262.0, rel_tol=1e-4)
    assert isclose(shaft["allowable_shear_mpa"], 135.0, rel_tol=1e-9)
    assert isclose(shaft["asme_diameter_mm"], 17.406, rel_tol=1e-4)
    assert isclose(shaft["goodman_diameter_mm"], 21.042, rel_tol=1e-4)
    assert shaft["selected_diameter_mm"] == 25.0
    assert isclose(shaft["loads"]["reaction_a_n"], 1030.6, rel_tol=1e-4)

    bearings = derived["bearings"]
    assert isclose(bearings["required_dynamic_capacity_n"], 12396.0, rel_tol=1e-4)
    assert bearings["axial_load_n"] == 0.0


def test_rejected_modules_stay_in_the_record() -> None:
    """The failed attempts prove the iteration selects rather than returns."""
    result = size_gear_drive(_drive())
    by_module = {item.module_mm: item for item in result.iterations}

    assert isclose(by_module[2.0].pinion_bending_safety, 1.299, rel_tol=1e-3)
    assert isclose(by_module[2.0].contact_safety_pinion, 1.048, rel_tol=1e-3)
    assert by_module[2.0].accepted is False

    assert isclose(by_module[2.5].pinion_bending_safety, 2.489, rel_tol=1e-3)
    assert isclose(by_module[2.5].contact_safety_pinion, 1.450, rel_tol=1e-3)
    assert by_module[2.5].accepted is False

    assert by_module[3.0].accepted is True
    assert sum(1 for item in result.iterations if item.accepted) == 1


def test_load_basis_accepts_one_module_smaller() -> None:
    """sqrt(1.5) = 1.225, which the m=2.5 contact factor of 1.450 clears.

    At the thinner tooth the same shaft bore leaves too little rim under the
    root (max 23.75 mm, a 25 mm bore selected), so the mandatory rim check
    correctly rejects the drive rather than silently shipping a weak pinion.
    This is the engine catching a real consequence, not a bug: it proves the
    switch actually changed which module the geometry checks ran against.
    """
    result = size_gear_drive(_drive(contact_safety_basis="load"))
    assert result.derived["geometry"]["module_mm"] == 2.5
    assert result.derived["geometry"]["face_width_mm"] == 43.0
    assert isclose(
        result.derived["contact"]["pinion"]["safety_factor"], 1.450, rel_tol=1e-3
    )
    rim_check = next(c for c in result.checks if c.id == "shaft.rim_thickness")
    assert rim_check.status == "failed"
    assert result.status == "rejected"


def test_the_contact_basis_is_always_declared() -> None:
    ids = {item.id for item in size_gear_drive(_drive()).assumptions}
    assert "contact_safety_basis" in ids


# ---------------------------------------------------------------------------
# Tooth selection
# ---------------------------------------------------------------------------


def test_undercut_floor_is_seventeen_at_twenty_degrees() -> None:
    assert undercut_floor(ANGLE) == 17


def test_hunting_pair_is_preferred_over_the_nearest_ratio() -> None:
    """17/51 is nearer the target but shares a factor of 17."""
    teeth, _ = select_teeth(target_ratio=1450 / 480, pressure_angle_rad=ANGLE)
    assert (teeth.pinion, teeth.gear) == (17, 52)
    assert teeth.hunting is True


def test_waiving_hunting_takes_the_nearer_ratio() -> None:
    teeth, warnings = select_teeth(
        target_ratio=1450 / 480, pressure_angle_rad=ANGLE, prefer_hunting=False
    )
    assert (teeth.pinion, teeth.gear) == (17, 51)
    assert teeth.hunting is False
    assert any("common factor" in item for item in warnings)


def test_forced_pinion_teeth_below_the_floor_are_rejected() -> None:
    with pytest.raises(GearSizingError) as caught:
        select_teeth(
            target_ratio=3.0, pressure_angle_rad=ANGLE, forced_pinion_teeth=12
        )
    assert caught.value.code == "teeth.undercut"


def test_contact_geometry_factor_is_the_exact_external_spur_form() -> None:
    assert isclose(contact_geometry_factor(ANGLE, 52 / 17), 0.121105, rel_tol=1e-5)


# ---------------------------------------------------------------------------
# Rejections and refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"power_kw": 0.0}, "input.non_positive"),
        ({"power_kw": float("inf")}, "input.non_positive"),
        ({"life_hours": -1.0}, "input.non_positive"),
        ({"gear_type": "helical"}, "input.out_of_range"),
        ({"normal_pressure_angle_deg": 30.0}, "input.out_of_range"),
        ({"contact_safety_basis": "vibes"}, "input.unknown_option"),
        ({"pinion_material": "unobtainium"}, "input.unknown_material"),
        ({"duty": "catastrophic"}, "input.unknown_option"),
        ({"pinion_speed_rpm": 400.0}, "ratio.unreachable"),
    ],
)
def test_bad_input_is_refused_with_a_coded_error(
    overrides: dict, code: str
) -> None:
    with pytest.raises(GearSizingError) as caught:
        size_gear_drive(_drive(**overrides))
    assert caught.value.code == code
    assert caught.value.as_dict()["status"] == "blocked"


def test_unknown_material_lists_the_valid_keys() -> None:
    with pytest.raises(GearSizingError) as caught:
        size_gear_drive(_drive(gear_material="cheese"))
    assert "20MnCr5_carburised_G2" in caught.value.detail["valid"]


def test_an_undersized_drive_is_rejected_with_its_ledger_intact() -> None:
    """Non-convergence is an answer, not a crash: the attempts are kept."""
    result = size_gear_drive(
        _drive(power_kw=250.0, safety_factor=3.0, max_module_mm=4.0)
    )
    assert result.status == "rejected"
    assert result.iterations
    assert any(item.id == "module.series_exhausted" for item in result.checks)
    assert all(item.accepted is False for item in result.iterations)


def test_only_cross_checked_allowables_are_shipped() -> None:
    """Every shipped row converts from a round published value.

    Nitrided steel stays out because its numbers could not be checked that
    way. Cast iron is in: AGMA publishes it, and the MPa figures convert
    cleanly from the psi originals.
    """
    keys = set(materials.GEAR_MATERIALS)
    assert keys == {
        "C45E_QT_200HB",
        "42CrMo4_QT_300HB",
        "16MnCr5_carburised_G1",
        "20MnCr5_carburised_G2",
        "EN-GJL-250_grey_iron",
        "EN-GJS-600-3_ductile_iron",
    }
    assert not any("nitrid" in key.casefold() for key in keys)


def test_cast_iron_allowables_convert_from_the_published_psi_values() -> None:
    grey = materials.gear_material("EN-GJL-250_grey_iron")
    ductile = materials.gear_material("EN-GJS-600-3_ductile_iron")
    assert isclose(grey.bending_allowable_mpa, 58.6, rel_tol=2e-3)
    assert isclose(grey.contact_allowable_mpa, 344.7, rel_tol=2e-3)
    assert isclose(ductile.bending_allowable_mpa, 151.7, rel_tol=2e-3)
    assert isclose(ductile.contact_allowable_mpa, 530.9, rel_tol=2e-3)


def test_cast_iron_says_its_life_curves_are_extrapolated() -> None:
    """The AGMA stress-cycle curves are steel curves; iron must not pretend."""
    for key in ("EN-GJL-250_grey_iron", "EN-GJS-600-3_ductile_iron"):
        assert materials.gear_material(key).life_curve_extrapolated is True
    for key in ("C45E_QT_200HB", "20MnCr5_carburised_G2"):
        assert materials.gear_material(key).life_curve_extrapolated is False

    result = size_gear_drive(
        _drive(
            pinion_material="EN-GJS-600-3_ductile_iron",
            gear_material="EN-GJS-600-3_ductile_iron",
            max_module_mm=25.0,
        )
    )
    joined = " ".join(result.warnings).casefold()
    assert "steel curves" in joined
    assert "extrapolation" in joined


def test_a_softer_iron_needs_more_tooth_than_hardened_steel() -> None:
    """Same job, weaker material, bigger module. The physics must show up."""
    steel = size_gear_drive(_drive(max_module_mm=25.0))
    iron = size_gear_drive(
        _drive(
            pinion_material="EN-GJS-600-3_ductile_iron",
            gear_material="EN-GJS-600-3_ductile_iron",
            max_module_mm=25.0,
        )
    )
    assert steel.status == "sized" and iron.status == "sized"
    assert (
        iron.derived["geometry"]["module_mm"]
        > steel.derived["geometry"]["module_mm"]
    )


def test_a_material_outside_the_tables_can_be_declared() -> None:
    """Stainless has no AGMA allowables, so the user supplies them."""
    stainless = {
        "416_stainless_hardened": {
            "designation": "416 stainless, hardened and tempered to about 40 HRC",
            "bending_allowable_mpa": 200.0,
            "contact_allowable_mpa": 850.0,
            "elastic_modulus_mpa": 200000.0,
            "poisson_ratio": 0.28,
            "brinell": 375.0,
            "source": "supplier datasheet",
        }
    }
    result = size_gear_drive(
        _drive(
            pinion_material="416_stainless_hardened",
            gear_material="416_stainless_hardened",
            custom_materials=stainless,
            max_module_mm=25.0,
        )
    )
    assert result.status == "sized"
    joined = " ".join(result.warnings).casefold()
    assert "declared" in joined
    assert "not values from ansi/agma" in joined


def test_a_declared_material_must_be_complete() -> None:
    with pytest.raises(GearSizingError) as caught:
        size_gear_drive(
            _drive(
                pinion_material="mystery",
                gear_material="mystery",
                custom_materials={"mystery": {"designation": "who knows"}},
            )
        )
    assert caught.value.code == "input.incomplete_material"


def test_an_unknown_material_points_at_the_declared_route() -> None:
    with pytest.raises(GearSizingError) as caught:
        size_gear_drive(_drive(pinion_material="303_stainless"))
    assert "stainless" in caught.value.detail["hint"]


# ---------------------------------------------------------------------------
# Evidence contract
# ---------------------------------------------------------------------------


def test_result_separates_given_derived_and_assumed() -> None:
    result = size_gear_drive(_drive())
    assert result.given["power_kw"] == 7.5
    assert "module_mm" not in result.given
    assert result.assumptions
    assert result.derived["geometry"]["module_mm"] == 3.0


def test_json_is_deterministic_and_finite() -> None:
    first = to_json(size_gear_drive(_drive()))
    second = to_json(size_gear_drive(_drive()))
    assert first == second
    document = json.loads(first)
    assert document["schema_version"] == "GearDriveSizing/v1"
    assert document["status"] == "sized"
    assert "NaN" not in first and "Infinity" not in first


def test_every_result_states_what_it_did_not_evaluate() -> None:
    result = size_gear_drive(_drive())
    joined = " ".join(result.limitations).casefold()
    for fragment in ("not fea", "certification", "scuffing", "micropitting"):
        assert fragment in joined
    assert result.unverified


def test_markdown_summary_names_the_governing_check() -> None:
    text = to_markdown(size_gear_drive(_drive()))
    assert "Gear drive sizing (sized)" in text
    assert "contact.pinion" in text
    assert "Not evaluated" in text


def test_through_hardened_pair_sizes_a_larger_module() -> None:
    """A softer pair must need more tooth, which exercises the other curves."""
    result = size_gear_drive(
        _drive(
            pinion_material="42CrMo4_QT_300HB",
            gear_material="42CrMo4_QT_300HB",
            max_module_mm=25.0,
        )
    )
    assert result.status == "sized"
    assert result.derived["geometry"]["module_mm"] > 3.0
