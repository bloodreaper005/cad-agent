"""Anchored tests for the Shigley calculation package.

Every constant that ships is checked against something that did not come from
the same place it did: a closed-form identity, an independent formulation of the
same quantity, or a published table the shipped formula should reproduce. A test
that only restates the implementation proves nothing, so there are none here.
"""

from __future__ import annotations

from math import inf, isinf, sqrt

import pytest

from mech_cad_design_agent.mechanics import bolted_joints as bolts
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
