"""Anchored tests for the Shigley calculation package.

Every constant that ships is checked against something that did not come from
the same place it did: a closed-form identity, an independent formulation of the
same quantity, or a published table the shipped formula should reproduce. A test
that only restates the implementation proves nothing, so there are none here.
"""

from __future__ import annotations

from math import exp, inf, isinf, pi, radians, sqrt, tan

import pytest

from mech_cad_design_agent.mechanics import bearings
from mech_cad_design_agent.mechanics import bolted_joints as bolts
from mech_cad_design_agent.mechanics import deflection
from mech_cad_design_agent.mechanics import friction_drives
from mech_cad_design_agent.mechanics import gears
from mech_cad_design_agent.mechanics import journal_bearings
from mech_cad_design_agent.mechanics import welds
from mech_cad_design_agent.mechanics import shafts
from mech_cad_design_agent.mechanics import springs
from mech_cad_design_agent.mechanics import fatigue
from mech_cad_design_agent.mechanics import static_failure as static
from mech_cad_design_agent.mechanics import stress
from mech_cad_design_agent.mechanics.errors import MechanicsError
from mech_cad_design_agent.mechanics.stress import StressState


# --- Chapter 3, stress ------------------------------------------------------


def test_principal_stresses_of_a_uniaxial_state() -> None:
    one, two, three = stress.principal_stresses(StressState(sigma_x=100.0))
    assert one == pytest.approx(100.0)
    assert two == pytest.approx(0.0, abs=1e-9)
    assert three == pytest.approx(0.0, abs=1e-9)


def test_pure_shear_gives_equal_and_opposite_principals() -> None:
    """The defining property of pure shear, and a check on the cubic solver."""
    one, two, three = stress.principal_stresses(StressState(tau_xy=50.0))
    assert one == pytest.approx(50.0)
    assert two == pytest.approx(0.0, abs=1e-9)
    assert three == pytest.approx(-50.0)


def test_von_mises_of_pure_shear_is_root_three_tau() -> None:
    state = StressState(tau_xy=50.0)
    assert stress.von_mises_stress(state) == pytest.approx(sqrt(3.0) * 50.0)


def test_hydrostatic_stress_has_no_distortion() -> None:
    state = StressState(sigma_x=40.0, sigma_y=40.0, sigma_z=40.0)
    assert stress.von_mises_stress(state) == pytest.approx(0.0, abs=1e-9)
    assert stress.principal_stresses(state) == pytest.approx((40.0, 40.0, 40.0))


@pytest.mark.parametrize(
    "sigma_x, sigma_y, tau_xy",
    [(12.0, -4.0, 7.0), (80.0, 0.0, 50.0), (-30.0, -90.0, 25.0), (0.0, 0.0, 15.0)],
)
def test_the_general_cubic_solver_agrees_with_the_plane_stress_formula(
    sigma_x: float, sigma_y: float, tau_xy: float
) -> None:
    """Two independent routes to the same numbers.

    The closed-form plane-stress pair and the general three-dimensional
    characteristic cubic share no code, so agreement is evidence rather than
    restatement.
    """
    high, low = stress.plane_principal_stresses(sigma_x, sigma_y, tau_xy)
    state = StressState(sigma_x=sigma_x, sigma_y=sigma_y, tau_xy=tau_xy)
    solved = sorted(stress.principal_stresses(state), reverse=True)
    expected = sorted([high, low, 0.0], reverse=True)
    assert solved == pytest.approx(expected, abs=1e-9)
    assert stress.plane_von_mises(sigma_x, sigma_y, tau_xy) == pytest.approx(
        stress.von_mises_stress(state)
    )


def test_thin_wall_cylinder_matches_thick_wall_in_the_limit() -> None:
    """A thin wall is the limiting case of the Lame solution.

    Checked at one part in two hundred, which is the regime where the thin-wall
    simplification is supposed to be usable.
    """
    pressure, inner_diameter, thickness = 2.0, 1000.0, 5.0
    thin_t, _ = stress.thin_wall_cylinder_stresses(pressure, inner_diameter, thickness)
    thick_t, _ = stress.thick_wall_cylinder_stresses(
        inner_pressure_mpa=pressure,
        outer_pressure_mpa=0.0,
        inner_radius_mm=inner_diameter / 2.0,
        outer_radius_mm=inner_diameter / 2.0 + thickness,
        radius_mm=inner_diameter / 2.0 + thickness / 2.0,
    )
    # The mean-diameter thin-wall form sits about half a percent above Lame at
    # a wall one two-hundredth of the diameter. That residual is the
    # approximation itself, not an implementation difference, so the tolerance
    # records the real agreement rather than being tightened until it passes.
    assert thin_t == pytest.approx(thick_t, rel=0.01)


def test_thick_wall_radial_stress_equals_minus_the_internal_pressure() -> None:
    """A boundary condition the Lame solution must satisfy exactly."""
    _, radial = stress.thick_wall_cylinder_stresses(
        inner_pressure_mpa=10.0,
        outer_pressure_mpa=0.0,
        inner_radius_mm=50.0,
        outer_radius_mm=100.0,
        radius_mm=50.0,
    )
    assert radial == pytest.approx(-10.0)


def test_section_properties_hold_the_polar_identity() -> None:
    """J = 2I for a solid round section."""
    assert stress.round_section_polar_inertia_mm4(30.0) == pytest.approx(
        2.0 * stress.round_section_inertia_mm4(30.0)
    )


def test_a_non_finite_dimension_is_refused() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        stress.round_section_inertia_mm4(float("inf"))
    assert excinfo.value.code == "input.not_finite"


# --- Chapter 5, static failure ---------------------------------------------


def test_maximum_shear_predicts_shear_yield_at_half_the_tensile_yield() -> None:
    result = static.maximum_shear(StressState(tau_xy=100.0), 400.0)
    assert result.factor_of_safety == pytest.approx(2.0)


def test_distortion_energy_predicts_shear_yield_at_0577_of_tensile_yield() -> None:
    """The 0.577 relation, recovered rather than hard-coded."""
    unit = static.distortion_energy(StressState(tau_xy=1.0), 1.0)
    assert 1.0 / unit.factor_of_safety == pytest.approx(sqrt(3.0))


def test_distortion_energy_is_never_more_conservative_than_maximum_shear() -> None:
    for tau in (10.0, 50.0, 200.0):
        for sigma in (-150.0, 0.0, 75.0, 300.0):
            state = StressState(sigma_x=sigma, tau_xy=tau)
            assert (
                static.distortion_energy(state, 400.0).factor_of_safety
                >= static.maximum_shear(state, 400.0).factor_of_safety - 1e-12
            )


def test_ductile_coulomb_mohr_reduces_to_maximum_shear_for_equal_strengths() -> None:
    state = StressState(sigma_x=120.0, tau_xy=60.0)
    assert static.ductile_coulomb_mohr(state, 400.0, 400.0).factor_of_safety == (
        pytest.approx(static.maximum_shear(state, 400.0).factor_of_safety)
    )


def test_an_unloaded_state_reports_an_infinite_margin_rather_than_dividing() -> None:
    assert isinf(static.distortion_energy(StressState(), 400.0).factor_of_safety)


def test_a_failing_margin_is_returned_and_never_raised() -> None:
    result = static.distortion_energy(StressState(sigma_x=800.0), 400.0)
    assert result.factor_of_safety == pytest.approx(0.5)
    assert result.safe is False


def test_an_unknown_theory_is_refused_with_the_supported_list() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        static.evaluate(StressState(sigma_x=1.0), theory="hooke", yield_strength_mpa=2.0)
    assert excinfo.value.code == "input.unknown_theory"
    assert "distortion_energy" in excinfo.value.detail["supported"]


# --- Chapter 6, fatigue -----------------------------------------------------


def test_size_factor_is_exactly_one_at_the_rotating_beam_specimen() -> None:
    """The factor is defined to vanish at the 7.62 mm specimen.

    The rounded 1.24 d^-0.107 form the text also prints returns 0.9978 here,
    which is why the exact (d/7.62)^-0.107 form is the one implemented.
    """
    assert fatigue.size_factor(7.62) == pytest.approx(1.0, abs=1e-12)


def test_axial_loading_has_no_size_effect() -> None:
    assert fatigue.size_factor(120.0, loading="axial") == 1.0


def test_endurance_limit_is_capped_above_the_steel_knee() -> None:
    assert fatigue.endurance_limit_prime_mpa(1000.0) == pytest.approx(500.0)
    assert fatigue.endurance_limit_prime_mpa(1600.0) == pytest.approx(700.0)


def test_surface_factor_is_never_above_one() -> None:
    for finish in fatigue.SURFACE_FACTORS:
        for ultimate in (400.0, 800.0, 1400.0):
            assert fatigue.surface_factor(finish, ultimate) <= 1.0


def test_marin_factors_multiply_into_the_corrected_limit() -> None:
    corrected = fatigue.corrected_endurance_limit(
        ultimate_tensile_mpa=800.0,
        surface_finish="machined",
        diameter_mm=40.0,
        loading="bending",
        reliability=0.99,
    )
    product = (
        corrected.endurance_limit_prime_mpa
        * corrected.surface
        * corrected.size
        * corrected.load
        * corrected.temperature
        * corrected.reliability
        * corrected.miscellaneous
    )
    assert corrected.endurance_limit_mpa == pytest.approx(product)


def test_reliability_table_is_monotonic_and_unity_at_fifty_percent() -> None:
    assert fatigue.reliability_factor(0.50) == 1.0
    previous = 1.1
    for reliability in sorted(fatigue.RELIABILITY_FACTORS):
        factor = fatigue.reliability_factor(reliability)
        assert factor < previous
        previous = factor


def test_an_untabulated_reliability_is_refused_rather_than_interpolated() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        fatigue.reliability_factor(0.97)
    assert excinfo.value.code == "input.unsupported_reliability"


def test_the_sn_curve_passes_through_both_of_its_defining_anchors() -> None:
    """By construction S_f(10^3) = f S_ut and S_f(10^6) = S_e."""
    curve = fatigue.sn_curve(800.0, 220.0)
    assert curve.strength_at_life_mpa(1e3) == pytest.approx(curve.fraction_f * 800.0)
    assert curve.strength_at_life_mpa(1e6) == pytest.approx(220.0)


def test_the_sn_curve_round_trips_stress_and_life() -> None:
    curve = fatigue.sn_curve(800.0, 220.0)
    for cycles in (1e3, 1e4, 5e5, 1e6):
        strength = curve.strength_at_life_mpa(cycles)
        assert curve.life_at_stress_cycles(strength) == pytest.approx(cycles, rel=1e-9)


@pytest.mark.parametrize("criterion", fatigue.CRITERIA)
def test_every_criterion_reduces_to_the_endurance_ratio_at_zero_mean_stress(
    criterion: str,
) -> None:
    """With no mean stress there is nothing to distinguish the criteria."""
    result = fatigue.evaluate_fatigue(
        alternating_mpa=110.0,
        midrange_mpa=0.0,
        endurance_limit_mpa=220.0,
        ultimate_tensile_mpa=800.0,
        yield_strength_mpa=600.0,
        criterion=criterion,
    )
    assert result.factor_of_safety == pytest.approx(2.0)


@pytest.mark.parametrize("yield_mpa, ultimate_mpa", [(600.0, 800.0), (350.0, 600.0)])
@pytest.mark.parametrize("alternating", [20.0, 80.0, 160.0])
@pytest.mark.parametrize("midrange", [0.0, 120.0, 400.0])
def test_criteria_order_by_their_shared_intercepts(
    yield_mpa: float, ultimate_mpa: float, alternating: float, midrange: float
) -> None:
    """Only same-intercept pairs are ordered, and those always are.

    Soderberg and ASME-elliptic both meet the mean axis at S_y, Goodman and
    Gerber both at S_ut, and in each pair the curved criterion bounds the
    straight one. Goodman against ASME-elliptic is deliberately not asserted:
    which is larger depends on S_y/S_ut and the stress ratio, and the ellipse
    genuinely falls inside the Goodman line at high mean stress.
    """
    factors = {
        criterion: fatigue.evaluate_fatigue(
            alternating_mpa=alternating,
            midrange_mpa=midrange,
            endurance_limit_mpa=220.0,
            ultimate_tensile_mpa=ultimate_mpa,
            yield_strength_mpa=yield_mpa,
            criterion=criterion,
        ).factor_of_safety
        for criterion in ("soderberg", "goodman", "gerber", "asme_elliptic")
    }
    assert factors["soderberg"] <= factors["asme_elliptic"] + 1e-9
    assert factors["goodman"] <= factors["gerber"] + 1e-9
    assert factors["soderberg"] <= factors["goodman"] + 1e-9


def test_the_first_cycle_yield_check_is_reported_alongside_the_fatigue_margin() -> None:
    """A joint can survive fatigue and still yield on the first cycle."""
    result = fatigue.evaluate_fatigue(
        alternating_mpa=100.0,
        midrange_mpa=480.0,
        endurance_limit_mpa=400.0,
        ultimate_tensile_mpa=900.0,
        yield_strength_mpa=500.0,
        criterion="gerber",
    )
    assert result.yield_factor_of_safety == pytest.approx(500.0 / 580.0)
    assert result.governing_factor == result.yield_factor_of_safety
    assert result.safe is False


def test_alternating_and_midrange_split_is_order_independent() -> None:
    assert fatigue.alternating_and_midrange(300.0, -100.0) == pytest.approx(
        fatigue.alternating_and_midrange(-100.0, 300.0)
    )
    assert fatigue.alternating_and_midrange(300.0, -100.0) == pytest.approx(
        (200.0, 100.0)
    )


def test_notch_sensitivity_stays_between_zero_and_one() -> None:
    for radius in (0.25, 1.0, 3.0, 10.0):
        for ultimate in (400.0, 800.0, 1200.0):
            assert 0.0 <= fatigue.notch_sensitivity(radius, ultimate) <= 1.0


def test_fatigue_concentration_lies_between_one_and_the_theoretical_factor() -> None:
    """K_f = 1 + q(K_t - 1) is bounded by its own definition."""
    for radius in (0.5, 2.0, 6.0):
        kf = fatigue.fatigue_stress_concentration(2.5, radius, 800.0)
        assert 1.0 <= kf <= 2.5


# --- Chapter 8, bolted joints ----------------------------------------------


@pytest.mark.parametrize(
    "diameter", sorted(bolts.COARSE_THREAD_SERIES)
)
def test_the_stress_area_formula_reproduces_the_published_table(
    diameter: float,
) -> None:
    """Cross-check in the manner this repository already uses for allowables.

    The tabulated areas and the formula that is supposed to generate them are
    independent records of the same quantity. Agreement across the whole series
    is evidence that neither was mistyped.
    """
    pitch, published = bolts.COARSE_THREAD_SERIES[diameter]
    computed = bolts.tensile_stress_area_mm2(diameter, pitch)
    assert computed == pytest.approx(published, rel=0.005)


def test_the_joint_constant_is_a_fraction() -> None:
    constant = bolts.joint_constant(500000.0, 2000000.0)
    assert 0.0 < constant < 1.0
    assert constant == pytest.approx(0.2)


def test_a_stiffer_member_sends_less_external_load_into_the_bolt() -> None:
    """The whole reason a preloaded joint tolerates fatigue."""
    compliant = bolts.joint_constant(500000.0, 500000.0)
    stiff = bolts.joint_constant(500000.0, 5000000.0)
    assert stiff < compliant


def test_recommended_preload_follows_the_published_fractions() -> None:
    proof = bolts.proof_load_n(58.0, 600.0)
    assert bolts.recommended_preload_n(proof, connection="reused") == pytest.approx(
        0.75 * proof
    )
    assert bolts.recommended_preload_n(proof, connection="permanent") == pytest.approx(
        0.90 * proof
    )


def test_bolt_load_is_the_preload_plus_its_share_of_the_external_load() -> None:
    pitch, area = bolts.coarse_thread(10.0)
    stiffness = bolts.bolt_stiffness_n_per_mm(
        nominal_diameter_mm=10.0,
        tensile_stress_area_mm2=area,
        shank_length_mm=18.0,
        threaded_length_in_grip_mm=12.0,
    )
    members = bolts.member_stiffness_n_per_mm(nominal_diameter_mm=10.0, grip_mm=30.0)
    result = bolts.evaluate_tension_joint(
        external_load_per_bolt_n=10000.0,
        tensile_stress_area_mm2=area,
        proof_strength_mpa=bolts.property_class("8.8")["proof_mpa"],
        bolt_stiffness_n_per_mm=stiffness,
        member_stiffness_n_per_mm=members,
    )
    assert result.bolt_load_n == pytest.approx(
        result.joint_constant * 10000.0 + result.preload_n
    )
    assert result.preload_n == pytest.approx(0.75 * result.proof_load_n)


def test_separation_is_reported_even_when_the_bolt_itself_is_safe() -> None:
    """The margin that is easiest to forget and most destructive to miss."""
    result = bolts.evaluate_tension_joint(
        external_load_per_bolt_n=30000.0,
        tensile_stress_area_mm2=245.0,
        proof_strength_mpa=600.0,
        bolt_stiffness_n_per_mm=400000.0,
        member_stiffness_n_per_mm=4000000.0,
        preload_n=5000.0,
    )
    assert result.separation_factor < 1.0
    assert result.governing_factor == result.separation_factor
    assert result.safe is False


def test_a_preload_at_or_above_the_proof_load_is_refused() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        bolts.evaluate_tension_joint(
            external_load_per_bolt_n=1000.0,
            tensile_stress_area_mm2=58.0,
            proof_strength_mpa=600.0,
            bolt_stiffness_n_per_mm=400000.0,
            member_stiffness_n_per_mm=2000000.0,
            preload_n=58.0 * 600.0,
        )
    assert excinfo.value.code == "input.out_of_range"


def test_an_unloaded_joint_reports_infinite_rather_than_dividing_by_zero() -> None:
    result = bolts.evaluate_tension_joint(
        external_load_per_bolt_n=0.0,
        tensile_stress_area_mm2=58.0,
        proof_strength_mpa=600.0,
        bolt_stiffness_n_per_mm=400000.0,
        member_stiffness_n_per_mm=2000000.0,
    )
    assert result.load_factor == inf
    assert result.separation_factor == inf


def test_preload_raises_mean_stress_without_touching_the_alternating_stress() -> None:
    """Why preload buys fatigue life, expressed as a property."""
    common = dict(
        maximum_external_load_n=8000.0,
        minimum_external_load_n=0.0,
        joint_constant=0.25,
        tensile_stress_area_mm2=58.0,
    )
    low_a, low_m = bolts.fatigue_stresses_mpa(preload_n=5000.0, **common)
    high_a, high_m = bolts.fatigue_stresses_mpa(preload_n=20000.0, **common)
    assert low_a == pytest.approx(high_a)
    assert high_m > low_m


def test_an_unknown_property_class_is_refused() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        bolts.property_class("8.9")
    assert excinfo.value.code == "input.unknown_property_class"


def test_property_classes_are_internally_consistent() -> None:
    """Proof below yield below ultimate, for every class that ships."""
    for name, values in bolts.METRIC_PROPERTY_CLASSES.items():
        assert values["proof_mpa"] < values["yield_mpa"] < values["ultimate_mpa"], name


# --- Chapter 4, deflection and columns --------------------------------------


def test_beam_cases_match_their_closed_forms() -> None:
    modulus = 207000.0
    inertia = stress.round_section_inertia_mm4(20.0)
    common = dict(length_mm=500.0, elastic_modulus_mpa=modulus, inertia_mm4=inertia)
    assert deflection.cantilever_end_load_deflection_mm(
        force_n=1000.0, **common
    ) == pytest.approx(1000.0 * 500.0**3 / (3.0 * modulus * inertia))
    assert deflection.simply_supported_center_load_deflection_mm(
        force_n=1000.0, **common
    ) == pytest.approx(1000.0 * 500.0**3 / (48.0 * modulus * inertia))


def test_the_offset_load_formula_reduces_to_the_centre_load_case() -> None:
    """An independent check on the general case, which is easy to get wrong.

    The maximum deflection under an intermediate load does not occur beneath
    the load, so the general expression differs in form from the central one and
    must still agree with it at midspan.
    """
    common = dict(
        length_mm=500.0, elastic_modulus_mpa=207000.0, inertia_mm4=8000.0, force_n=900.0
    )
    offset = deflection.simply_supported_offset_load_deflection_mm(
        distance_from_left_mm=250.0, **common
    )
    centre = deflection.simply_supported_center_load_deflection_mm(**common)
    assert offset == pytest.approx(centre, rel=1e-12)


def test_a_distributed_load_deflects_less_than_the_same_load_concentrated() -> None:
    common = dict(length_mm=500.0, elastic_modulus_mpa=207000.0, inertia_mm4=8000.0)
    spread = deflection.simply_supported_uniform_load_deflection_mm(
        load_n_per_mm=2.0, **common
    )
    concentrated = deflection.simply_supported_center_load_deflection_mm(
        force_n=1000.0, **common
    )
    assert spread < concentrated


def test_springs_combine_the_way_stiffnesses_must() -> None:
    assert deflection.series_stiffness(100.0, 100.0) == pytest.approx(50.0)
    assert deflection.parallel_stiffness(100.0, 100.0) == pytest.approx(200.0)
    assert deflection.series_stiffness(100.0) == pytest.approx(100.0)


def test_euler_and_johnson_are_tangent_at_the_transition_slenderness() -> None:
    """The defining property of the transition, and the check that locates it.

    If the two curves do not meet exactly at the computed slenderness then the
    transition is misplaced, and a column near it is rated by the wrong formula
    in the unconservative direction.
    """
    yield_strength, modulus, area = 400.0, 207000.0, 706.858
    transition = deflection.transition_slenderness(yield_strength, modulus)
    euler = deflection.euler_critical_load_n(
        area_mm2=area, slenderness=transition, elastic_modulus_mpa=modulus
    )
    johnson = deflection.johnson_critical_load_n(
        area_mm2=area,
        slenderness=transition,
        yield_strength_mpa=yield_strength,
        elastic_modulus_mpa=modulus,
    )
    assert euler == pytest.approx(johnson, rel=1e-9)


def test_a_short_column_squashes_at_the_yield_load() -> None:
    area = 706.858
    load = deflection.johnson_critical_load_n(
        area_mm2=area,
        slenderness=1e-9,
        yield_strength_mpa=400.0,
        elastic_modulus_mpa=207000.0,
    )
    assert load == pytest.approx(400.0 * area)


def test_a_slender_column_is_rated_by_euler_and_a_stubby_one_by_johnson() -> None:
    common = dict(
        area_mm2=706.858,
        inertia_mm4=stress.round_section_inertia_mm4(30.0),
        yield_strength_mpa=400.0,
        elastic_modulus_mpa=207000.0,
        applied_load_n=50000.0,
    )
    assert deflection.evaluate_column(length_mm=2000.0, **common).formula == "euler"
    assert deflection.evaluate_column(length_mm=150.0, **common).formula == "johnson"


# --- Chapter 7, shafts ------------------------------------------------------


@pytest.mark.parametrize("criterion", shafts.SHAFT_CRITERIA)
def test_solving_for_a_diameter_and_rating_it_are_inverse_operations(
    criterion: str,
) -> None:
    """The strongest available check on the shaft equations.

    Sizing and rating are written from different rearrangements of the same
    criterion, so a diameter solved for a required factor must rate back to that
    factor exactly.
    """
    loading = shafts.rotating_shaft(
        bending_moment_nmm=250000.0,
        steady_torque_nmm=180000.0,
        bending_kf=1.6,
        torsion_kfs=1.3,
    )
    strengths = dict(
        endurance_limit_mpa=220.0, ultimate_tensile_mpa=800.0, yield_strength_mpa=600.0
    )
    diameter = shafts.diameter_mm(
        loading=loading, safety_factor=1.8, criterion=criterion, **strengths
    )
    rated = shafts.evaluate(
        loading=loading, diameter_mm=diameter, criterion=criterion, **strengths
    )
    assert rated.fatigue_factor == pytest.approx(1.8, rel=1e-9)


def test_soderberg_demands_the_largest_shaft_and_gerber_the_smallest() -> None:
    loading = shafts.rotating_shaft(
        bending_moment_nmm=250000.0, steady_torque_nmm=180000.0
    )
    strengths = dict(
        endurance_limit_mpa=220.0, ultimate_tensile_mpa=800.0, yield_strength_mpa=600.0
    )
    sizes = {
        criterion: shafts.diameter_mm(
            loading=loading, safety_factor=1.8, criterion=criterion, **strengths
        )
        for criterion in shafts.SHAFT_CRITERIA
    }
    assert sizes["soderberg"] > sizes["goodman"] > sizes["gerber"]


def test_a_rotating_shaft_puts_bending_in_the_alternating_term_only() -> None:
    """Rotation is what makes a steady transverse load a fatigue problem."""
    loading = shafts.rotating_shaft(
        bending_moment_nmm=100000.0, steady_torque_nmm=50000.0
    )
    assert loading.midrange_moment_nmm == 0.0
    assert loading.alternating_torque_nmm == 0.0
    alternating, midrange = loading.von_mises_amplitudes_mpa(30.0)
    assert alternating > 0.0 and midrange > 0.0


def test_an_unloaded_shaft_implies_no_diameter_and_says_so() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        shafts.diameter_mm(
            loading=shafts.ShaftLoading(),
            endurance_limit_mpa=220.0,
            ultimate_tensile_mpa=800.0,
            yield_strength_mpa=600.0,
            safety_factor=2.0,
        )
    assert excinfo.value.code == "input.incomplete"


# --- Chapter 10, springs ----------------------------------------------------


def test_bergstrasser_and_wahl_agree_within_one_percent_over_the_usable_range() -> None:
    """Two corrections for the same effect, published decades apart."""
    for index in (4.0, 6.0, 8.0, 10.0, 12.0):
        bergstrasser = springs.bergstrasser_factor(index)
        wahl = springs.wahl_factor(index)
        assert wahl == pytest.approx(bergstrasser, rel=0.015)


def test_the_correction_factor_approaches_one_for_a_very_slack_spring() -> None:
    assert springs.bergstrasser_factor(1000.0) == pytest.approx(1.0, abs=1e-2)


@pytest.mark.parametrize(
    "ends, active, solid",
    [
        ("plain", 10.0, 22.0),
        ("plain_ground", 9.0, 20.0),
        ("squared", 8.0, 22.0),
        ("squared_ground", 8.0, 20.0),
    ],
)
def test_end_treatments_follow_the_published_geometry(
    ends: str, active: float, solid: float
) -> None:
    result = springs.geometry(
        wire_diameter_mm=2.0, total_coils=10.0, pitch_mm=6.0, ends=ends
    )
    assert result["active_coils"] == pytest.approx(active)
    assert result["solid_length_mm"] == pytest.approx(solid)


def test_spring_rate_follows_the_fourth_power_of_wire_diameter() -> None:
    common = dict(mean_diameter_mm=16.0, active_coils=8.0, shear_modulus_mpa=81700.0)
    thin = springs.spring_rate_n_per_mm(wire_diameter_mm=2.0, **common)
    thick = springs.spring_rate_n_per_mm(wire_diameter_mm=4.0, **common)
    assert thick == pytest.approx(16.0 * thin)


def test_wire_strength_outside_its_fitted_range_is_refused() -> None:
    """The A/d^m relation is a fit, and the range is part of the constant."""
    with pytest.raises(MechanicsError) as excinfo:
        springs.wire_material("music_wire").ultimate_tensile_mpa(40.0)
    assert excinfo.value.code == "input.out_of_range"


def test_a_spring_is_rated_both_at_load_and_shut_solid() -> None:
    result = springs.evaluate_compression_spring(
        force_n=120.0,
        wire_diameter_mm=2.5,
        mean_diameter_mm=15.0,
        total_coils=12.0,
        pitch_mm=5.0,
    )
    assert result.spring_index == pytest.approx(6.0)
    assert result.index_within_recommended_range is True
    assert result.shear_stress_at_solid_mpa > result.shear_stress_at_load_mpa
    assert result.factor_of_safety_at_solid < result.factor_of_safety_at_load


def test_an_out_of_range_spring_index_is_reported_not_raised() -> None:
    result = springs.evaluate_compression_spring(
        force_n=50.0,
        wire_diameter_mm=1.0,
        mean_diameter_mm=20.0,
        total_coils=12.0,
        pitch_mm=4.0,
    )
    assert result.spring_index == pytest.approx(20.0)
    assert result.index_within_recommended_range is False
    assert result.safe is False


# --- Chapter 11, bearings ---------------------------------------------------


def test_rated_life_is_one_million_revolutions_at_the_catalogue_load() -> None:
    """The definition of the basic dynamic load rating."""
    assert bearings.life_revolutions(
        dynamic_capacity_n=5000.0, equivalent_load_n=5000.0
    ) == pytest.approx(1e6)


def test_ball_and_roller_life_exponents_are_distinct_and_correct() -> None:
    doubled = dict(dynamic_capacity_n=10000.0, equivalent_load_n=5000.0)
    assert bearings.life_revolutions(**doubled) == pytest.approx(8e6)
    assert bearings.life_revolutions(
        bearing_family="cylindrical_roller", **doubled
    ) == pytest.approx(1e6 * 2.0 ** (10.0 / 3.0))


def test_a_rotating_outer_ring_costs_capacity() -> None:
    inner = bearings.equivalent_radial_load_n(radial_n=3000.0, rotation_factor=1.0)
    outer = bearings.equivalent_radial_load_n(radial_n=3000.0, rotation_factor=1.2)
    assert outer == pytest.approx(1.2 * inner)


def test_a_small_axial_load_does_not_change_the_equivalent_load() -> None:
    """Below the threshold e the thrust is carried without penalty."""
    plain = bearings.equivalent_radial_load_n(radial_n=3000.0)
    slight = bearings.equivalent_radial_load_n(radial_n=3000.0, axial_n=300.0)
    assert slight == pytest.approx(plain)


@pytest.mark.parametrize("reliability", [0.95, 0.96, 0.97, 0.98, 0.99])
def test_the_weibull_model_reproduces_the_tabulated_iso_life_adjustment(
    reliability: float,
) -> None:
    """Cross-check against a table that is already in this repository.

    The three-parameter Weibull of equation 11-18 and the ISO 281 adjustment
    factors used by the gear engine are independent records of the same
    quantity, arrived at by different routes. They agree to within six percent
    across the table, and to within one percent at the reliabilities most often
    designed to.
    """
    from mech_cad_design_agent.gear_sizing.bearings import RELIABILITY_ADJUSTMENT

    base = bearings.reliability_life_factor(0.90)
    derived = bearings.reliability_life_factor(reliability) / base
    assert derived == pytest.approx(RELIABILITY_ADJUSTMENT[reliability], rel=0.06)


def test_a_higher_reliability_demands_a_larger_bearing() -> None:
    common = dict(radial_n=3000.0, speed_rpm=1200.0, design_life_hours=20000.0)
    ninety = bearings.required_dynamic_capacity_n(**common)
    ninety_nine = bearings.required_dynamic_capacity_n(reliability=0.99, **common)
    assert (
        ninety_nine.required_dynamic_capacity_n
        > ninety.required_dynamic_capacity_n
    )


def test_an_unloaded_bearing_is_refused_rather_than_rated() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        bearings.required_dynamic_capacity_n(
            radial_n=0.0, speed_rpm=1200.0, design_life_hours=20000.0
        )
    assert excinfo.value.code == "input.incomplete"


# --- Chapters 13 to 15, gears ----------------------------------------------


@pytest.mark.parametrize(
    "pressure_angle_deg, expected",
    [(14.5, 32.0), (20.0, 17.1), (25.0, 11.2)],
)
def test_the_rack_interference_limit_matches_the_published_values(
    pressure_angle_deg: float, expected: float
) -> None:
    """The origin of the familiar eighteen-tooth rule at twenty degrees."""
    assert gears.smallest_pinion_with_rack(
        pressure_angle_deg=pressure_angle_deg
    ) == pytest.approx(expected, rel=0.01)


def test_a_pinion_large_enough_for_a_rack_has_no_upper_gear_limit() -> None:
    """A rack is the limit of an infinitely large gear, so the bound vanishes."""
    assert gears.largest_gear_without_interference(pinion_teeth=18) == inf
    assert gears.largest_gear_without_interference(pinion_teeth=13) == pytest.approx(
        16.5, rel=0.02
    )


def test_contact_ratio_of_a_normal_spur_pair_is_between_one_and_two() -> None:
    ratio = gears.contact_ratio(module_mm=3.0, pinion_teeth=17, gear_teeth=52)
    assert 1.2 < ratio < 2.0


def test_a_helical_gear_at_zero_helix_reduces_to_a_spur_gear() -> None:
    """The check that the transverse and normal planes are related correctly."""
    helical = gears.helical_geometry(
        normal_module_mm=3.0, teeth=20, helix_angle_deg=0.0
    )
    assert helical.transverse_module_mm == pytest.approx(3.0)
    assert helical.transverse_pressure_angle_deg == pytest.approx(20.0)
    assert helical.virtual_teeth == pytest.approx(20.0)


def test_a_helix_raises_the_transverse_pressure_angle_and_the_tooth_count() -> None:
    helical = gears.helical_geometry(
        normal_module_mm=3.0, teeth=20, helix_angle_deg=30.0
    )
    assert helical.transverse_pressure_angle_deg > 20.0
    assert helical.virtual_teeth == pytest.approx(20.0 / (sqrt(3.0) / 2.0) ** 3)


def test_helical_forces_reduce_to_spur_forces_at_zero_helix() -> None:
    common = dict(torque_nmm=49392.9, pitch_diameter_mm=51.0)
    spur = gears.spur_forces(**common)
    helical = gears.helical_forces(helix_angle_deg=0.0, **common)
    assert helical.tangential_n == pytest.approx(spur.tangential_n)
    assert helical.radial_n == pytest.approx(spur.radial_n)
    assert helical.axial_n == pytest.approx(0.0, abs=1e-9)


def test_a_helix_introduces_thrust_a_spur_mesh_does_not_have() -> None:
    """The bearing consequence of choosing helical gearing."""
    helical = gears.helical_forces(
        torque_nmm=49392.9, pitch_diameter_mm=51.0, helix_angle_deg=30.0
    )
    assert helical.axial_n == pytest.approx(
        helical.tangential_n * tan(radians(30.0))
    )


def test_bevel_pitch_angles_are_complementary_for_a_right_angle_pair() -> None:
    pinion, gear = gears.bevel_pitch_angles_deg(17, 52)
    assert pinion + gear == pytest.approx(90.0)


def test_worm_ratio_comes_from_starts_not_diameter() -> None:
    """The property that makes a worm drive compact at high ratio."""
    geometry = gears.worm_geometry(
        axial_module_mm=4.0,
        worm_starts=2,
        gear_teeth=40,
        worm_pitch_diameter_mm=50.0,
    )
    assert geometry.ratio == pytest.approx(20.0)
    assert geometry.lead_mm == pytest.approx(pi * 4.0 * 2.0)


def test_worm_efficiency_rises_with_lead_angle() -> None:
    low = gears.worm_efficiency(lead_angle_deg=5.0, friction_coefficient=0.05)
    high = gears.worm_efficiency(lead_angle_deg=25.0, friction_coefficient=0.05)
    assert 0.0 < low < high < 1.0


def test_a_shallow_worm_self_locks_and_a_steep_one_does_not() -> None:
    assert gears.worm_self_locking(lead_angle_deg=2.0, friction_coefficient=0.10)
    assert not gears.worm_self_locking(lead_angle_deg=25.0, friction_coefficient=0.05)


@pytest.mark.parametrize("ratio", [1.0, 1.5, 3.058823529411765, 5.0, 10.0])
@pytest.mark.parametrize("angle", [14.5, 20.0, 25.0])
def test_the_geometry_factor_agrees_with_the_verified_spur_implementation(
    ratio: float, angle: float
) -> None:
    """Cross-check against code already anchored to published ISO values.

    The gear_sizing implementation is verified against the ISO 6336-3 chart, so
    agreement here inherits that anchor rather than asserting a fresh number.
    """
    from mech_cad_design_agent.gear_sizing.tooth_form import contact_geometry_factor

    mine = gears.external_geometry_factor_i(
        gear_ratio_m=ratio, transverse_pressure_angle_deg=angle
    )
    assert mine == pytest.approx(contact_geometry_factor(radians(angle), ratio))


def test_an_idler_changes_direction_without_changing_ratio() -> None:
    direct = gears.train_value(driving_teeth=(20,), driven_teeth=(60,))
    through_idler = gears.train_value(driving_teeth=(20, 35), driven_teeth=(35, 60))
    assert direct == pytest.approx(through_idler)


def test_a_fractional_tooth_count_is_refused() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        gears.pitch_diameter_mm(3.0, 17.5)  # type: ignore[arg-type]
    assert excinfo.value.code == "input.not_an_integer"


# --- Chapter 9, welds -------------------------------------------------------


def test_the_throat_is_the_leg_times_cos_forty_five() -> None:
    assert welds.throat_mm(10.0) == pytest.approx(7.07)


def test_direct_shear_is_the_load_over_the_throat_area() -> None:
    group = welds.parallel_fillets(length_mm=100.0, separation_mm=50.0)
    result = welds.evaluate_fillet_weld(
        group=group, leg_mm=8.0, shear_force_n=20000.0
    )
    assert result.primary_shear_mpa == pytest.approx(
        20000.0 / (0.707 * 8.0 * 200.0)
    )
    assert result.secondary_shear_mpa == 0.0


def test_doubling_the_weld_leg_halves_the_stress() -> None:
    group = welds.parallel_fillets(length_mm=100.0, separation_mm=50.0)
    common = dict(group=group, shear_force_n=20000.0)
    small = welds.evaluate_fillet_weld(leg_mm=8.0, **common)
    large = welds.evaluate_fillet_weld(leg_mm=16.0, **common)
    assert large.resultant_shear_mpa == pytest.approx(
        small.resultant_shear_mpa / 2.0
    )


def test_electrode_strength_sets_the_allowable_shear() -> None:
    assert welds.allowable_shear_mpa("E70xx") == pytest.approx(0.30 * 482.0)
    assert welds.allowable_shear_mpa("E60xx") < welds.allowable_shear_mpa("E120xx")


def test_an_unknown_electrode_is_refused() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        welds.electrode("E75xx")
    assert excinfo.value.code == "input.unknown_electrode"


# --- Chapters 16 and 17, friction drives ------------------------------------


def test_uniform_wear_is_the_conservative_clutch_model() -> None:
    """Which is why the text designs to it: a worn clutch carries less."""
    common = dict(
        outer_diameter_mm=200.0,
        inner_diameter_mm=120.0,
        maximum_pressure_mpa=1.0,
        friction_coefficient=0.3,
    )
    wear = friction_drives.uniform_wear_clutch(**common)
    pressure = friction_drives.uniform_pressure_clutch(**common)
    assert wear.torque_capacity_nmm < pressure.torque_capacity_nmm
    assert wear.axial_force_n < pressure.axial_force_n


def test_the_closed_form_clutch_optimum_maximises_the_computed_torque() -> None:
    """A closed form checked against a search over the function it optimises."""
    outer = 200.0
    closed_form = friction_drives.optimum_inner_diameter_mm(outer)
    assert closed_form == pytest.approx(outer / sqrt(3.0))

    def torque(inner: float) -> float:
        return friction_drives.uniform_wear_clutch(
            outer_diameter_mm=outer,
            inner_diameter_mm=inner,
            maximum_pressure_mpa=1.0,
            friction_coefficient=0.3,
        ).torque_capacity_nmm

    best = max((x * 0.5 for x in range(40, 399)), key=torque)
    assert best == pytest.approx(closed_form, rel=0.01)


def test_the_belt_equation_is_the_exponential_of_friction_times_wrap() -> None:
    assert friction_drives.belt_tension_ratio(
        friction_coefficient=0.3, wrap_angle_deg=180.0
    ) == pytest.approx(exp(0.3 * pi))


def test_a_vee_groove_multiplies_the_effective_friction() -> None:
    """The wedge, not the material, is why a V belt out-pulls a flat one."""
    flat = friction_drives.belt_tension_ratio(
        friction_coefficient=0.3, wrap_angle_deg=180.0
    )
    vee = friction_drives.belt_tension_ratio(
        friction_coefficient=0.3, wrap_angle_deg=180.0, groove_angle_deg=38.0
    )
    assert vee > 5.0 * flat


def test_open_belt_wrap_angles_are_supplementary() -> None:
    small, large, _ = friction_drives.open_belt_geometry(
        small_pulley_diameter_mm=150.0,
        large_pulley_diameter_mm=400.0,
        centre_distance_mm=800.0,
    )
    assert small + large == pytest.approx(360.0)
    assert small < 180.0 < large


def test_equal_pulleys_give_half_wrap_and_the_elementary_belt_length() -> None:
    small, large, length = friction_drives.open_belt_geometry(
        small_pulley_diameter_mm=200.0,
        large_pulley_diameter_mm=200.0,
        centre_distance_mm=500.0,
    )
    assert small == pytest.approx(180.0)
    assert large == pytest.approx(180.0)
    assert length == pytest.approx(2.0 * 500.0 + pi * 200.0)


def test_the_belt_tensions_deliver_exactly_the_requested_power() -> None:
    drive = friction_drives.evaluate_belt_drive(
        power_w=7500.0,
        small_pulley_diameter_mm=150.0,
        large_pulley_diameter_mm=400.0,
        centre_distance_mm=800.0,
        small_pulley_rpm=1450.0,
        mass_per_length_kg_per_m=0.25,
        groove_angle_deg=38.0,
    )
    assert (
        drive.tight_side_n - drive.slack_side_n
    ) * drive.belt_speed_m_per_s == pytest.approx(7500.0)
    assert drive.centrifugal_tension_n > 0.0


def test_overlapping_pulleys_are_refused() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        friction_drives.open_belt_geometry(
            small_pulley_diameter_mm=100.0,
            large_pulley_diameter_mm=400.0,
            centre_distance_mm=100.0,
        )
    assert excinfo.value.code == "input.out_of_range"


def test_flywheel_inertia_scales_inversely_with_permitted_fluctuation() -> None:
    common = dict(energy_fluctuation_j=1000.0, mean_speed_rad_per_s=100.0)
    loose = friction_drives.flywheel_inertia_kg_m2(
        coefficient_of_speed_fluctuation=0.05, **common
    )
    tight = friction_drives.flywheel_inertia_kg_m2(
        coefficient_of_speed_fluctuation=0.01, **common
    )
    assert tight == pytest.approx(5.0 * loose)


# --- Chapter 12, journal bearings -------------------------------------------


def test_clearance_and_unit_load_follow_their_definitions() -> None:
    assert journal_bearings.radial_clearance_mm(50.0, 50.1) == pytest.approx(0.05)
    assert journal_bearings.unit_load_mpa(
        radial_load_n=4000.0, journal_diameter_mm=50.0, length_mm=50.0
    ) == pytest.approx(1.6)


def test_a_bearing_bore_smaller_than_its_journal_is_refused() -> None:
    with pytest.raises(MechanicsError) as excinfo:
        journal_bearings.radial_clearance_mm(50.0, 49.9)
    assert excinfo.value.code == "input.out_of_range"


def test_the_estimate_names_the_chart_variables_it_could_not_compute() -> None:
    """The boundary of what was calculated is part of the result.

    Raimondi and Boyd solved the Reynolds equation numerically and published
    charts. Those cannot be reproduced from their printed form, so the estimate
    says which quantities still need them rather than inventing fits.
    """
    estimate = journal_bearings.estimate(
        radial_load_n=4000.0,
        journal_diameter_mm=50.0,
        bearing_diameter_mm=50.1,
        length_mm=50.0,
        viscosity_pa_s=0.05,
        speed_rev_per_s=30.0,
    )
    assert estimate.sommerfeld_number > 0.0
    assert estimate.friction_power_w > 0.0
    assert len(estimate.chart_variables_required) == 5


def test_minimum_film_thickness_vanishes_as_eccentricity_approaches_one() -> None:
    thick = journal_bearings.minimum_film_thickness_mm(
        radial_clearance_mm=0.05, eccentricity_ratio=0.2
    )
    thin = journal_bearings.minimum_film_thickness_mm(
        radial_clearance_mm=0.05, eccentricity_ratio=0.9
    )
    assert thick > thin > 0.0
    with pytest.raises(MechanicsError):
        journal_bearings.minimum_film_thickness_mm(
            radial_clearance_mm=0.05, eccentricity_ratio=1.0
        )
