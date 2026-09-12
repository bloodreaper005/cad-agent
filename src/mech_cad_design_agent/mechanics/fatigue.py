"""Fatigue failure resulting from variable loading.

Shigley's Mechanical Engineering Design, Budynas and Nisbett, chapter 6.

Two values in this module are curve fits to published figures rather than
tabulated constants, and are flagged in the result as such: the fatigue-strength
fraction f of figure 6-18, and the Neuber constant of equation 6-35. Both may be
supplied explicitly by a caller who has the figure in front of them, which is
the recommended path for any design that will be built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import inf, log10, sqrt

from .errors import MechanicsError, require_non_negative, require_positive


# Table 6-2, surface-condition factor k_a = a * S_ut^b with S_ut in MPa.
SURFACE_FACTORS: dict[str, tuple[float, float]] = {
    "ground": (1.58, -0.085),
    "machined": (4.51, -0.265),
    "cold_drawn": (4.51, -0.265),
    "hot_rolled": (57.7, -0.718),
    "as_forged": (272.0, -0.995),
}

# Table 6-5, reliability factor k_e = 1 - 0.08 z_a.
RELIABILITY_FACTORS: dict[float, float] = {
    0.50: 1.000,
    0.90: 0.897,
    0.95: 0.868,
    0.99: 0.814,
    0.999: 0.753,
    0.9999: 0.702,
    0.99999: 0.659,
    0.999999: 0.620,
}

# Equation 6-26, load factor. Torsion is carried separately because a combined
# state must be reduced to von Mises first and then rated in bending.
LOAD_FACTORS: dict[str, float] = {
    "bending": 1.00,
    "axial": 0.85,
    "torsion": 0.59,
}

CRITERIA = ("goodman", "gerber", "asme_elliptic", "soderberg", "morrow")

# Equation 6-8. Steel only; other alloys have no knee and need an explicit
# endurance strength at a stated life.
STEEL_ENDURANCE_KNEE_MPA = 1400.0
STEEL_ENDURANCE_CEILING_MPA = 700.0


def endurance_limit_prime_mpa(ultimate_tensile_mpa: float) -> float:
    """Rotating-beam endurance limit S'_e for steel, Shigley eq. 6-8."""
    ultimate = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    if ultimate <= STEEL_ENDURANCE_KNEE_MPA:
        return 0.5 * ultimate
    return STEEL_ENDURANCE_CEILING_MPA


def surface_factor(finish: str, ultimate_tensile_mpa: float) -> float:
    """Marin surface factor k_a, Shigley eq. 6-18 and table 6-2."""
    try:
        a, b = SURFACE_FACTORS[finish]
    except KeyError:
        raise MechanicsError(
            "input.unknown_finish",
            f"unknown surface finish {finish!r}",
            {"supported": sorted(SURFACE_FACTORS)},
        ) from None
    ultimate = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    return min(a * ultimate**b, 1.0)


def size_factor(diameter_mm: float, *, loading: str = "bending") -> float:
    """Marin size factor k_b, Shigley eq. 6-20.

    Axial loading has no size effect. For a non-round or non-rotating section the
    caller must supply an equivalent diameter from table 6-3; this function
    assumes the rotating round section the equation is written for.
    """
    if loading == "axial":
        return 1.0
    diameter = require_positive(diameter_mm, "diameter_mm")
    if diameter < 2.79:
        # Below the fitted range the text takes the factor as unity.
        return 1.0
    if diameter <= 51.0:
        # Written as (d/7.62)^-0.107 rather than the rounded 1.24 d^-0.107 the
        # text also prints, because only this form is exactly one at the 7.62 mm
        # rotating-beam specimen the factor is defined against. The rounded
        # coefficient returns 0.9978 there, which is a visible error in a factor
        # that is supposed to vanish at the reference size.
        return (diameter / 7.62) ** -0.107
    if diameter <= 254.0:
        return 1.51 * diameter**-0.157
    raise MechanicsError(
        "input.out_of_range",
        "size factor is fitted only to 254 mm",
        {"diameter_mm": diameter},
    )


def load_factor(loading: str) -> float:
    """Marin load factor k_c, Shigley eq. 6-26."""
    try:
        return LOAD_FACTORS[loading]
    except KeyError:
        raise MechanicsError(
            "input.unknown_loading",
            f"unknown loading mode {loading!r}",
            {"supported": sorted(LOAD_FACTORS)},
        ) from None


def temperature_factor(celsius: float) -> float:
    """Marin temperature factor k_d, Shigley eq. 6-27.

    The polynomial is fitted from 20 to 550 degrees Celsius. Below 20 the text
    takes the factor as unity; above 550 creep governs and fatigue rating by
    this method is not applicable.
    """
    temperature = float(celsius)
    if temperature <= 20.0:
        return 1.0
    if temperature > 550.0:
        raise MechanicsError(
            "input.out_of_range",
            "temperature factor is fitted only to 550 C, above which creep governs",
            {"celsius": temperature},
        )
    return (
        0.975
        + 0.432e-3 * temperature
        - 0.115e-5 * temperature**2
        + 0.104e-8 * temperature**3
        - 0.595e-12 * temperature**4
    )


def reliability_factor(reliability: float) -> float:
    """Marin reliability factor k_e, Shigley table 6-5.

    Only the tabulated reliabilities are accepted. Interpolating the table would
    invent a transformation-variate value the text does not publish.
    """
    value = float(reliability)
    for tabulated, factor in RELIABILITY_FACTORS.items():
        if abs(tabulated - value) < 1e-12:
            return factor
    raise MechanicsError(
        "input.unsupported_reliability",
        "reliability must be one of the tabulated values",
        {"supported": sorted(RELIABILITY_FACTORS)},
    )


def fatigue_strength_fraction(ultimate_tensile_mpa: float) -> float:
    """Fatigue-strength fraction f, a fit to Shigley figure 6-18.

    Unverified against the published figure. The text reads f from a chart; this
    polynomial reproduces its shape and is used only when the caller does not
    supply f. Below 490 MPa the text itself recommends 0.9.
    """
    ultimate = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    if ultimate < 490.0:
        return 0.9
    if ultimate > 1400.0:
        return 0.77
    return 1.06 - 4.1e-4 * ultimate + 1.5e-7 * ultimate**2


def neuber_sqrt_a_mm(ultimate_tensile_mpa: float, *, torsion: bool = False) -> float:
    """Neuber constant sqrt(a), Shigley eq. 6-35, returned in sqrt(mm).

    Unverified against the published equation in SI. The fit is evaluated in the
    US customary form in which it is stated, with S_ut in kpsi and sqrt(a) in
    sqrt(inch), then converted, because that is the form the coefficients belong
    to. For torsion the text evaluates the same fit at S_ut + 20 kpsi.
    """
    ultimate = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    kpsi = ultimate / 6.894757
    if torsion:
        kpsi += 20.0
    sqrt_a_in = (
        0.246 - 3.08e-3 * kpsi + 1.51e-5 * kpsi**2 - 2.67e-8 * kpsi**3
    )
    return max(sqrt_a_in, 0.0) * sqrt(25.4)


def notch_sensitivity(
    notch_radius_mm: float, ultimate_tensile_mpa: float, *, torsion: bool = False
) -> float:
    """Notch sensitivity q, Shigley eq. 6-33."""
    radius = require_positive(notch_radius_mm, "notch_radius_mm")
    sqrt_a = neuber_sqrt_a_mm(ultimate_tensile_mpa, torsion=torsion)
    return 1.0 / (1.0 + sqrt_a / sqrt(radius))


def fatigue_stress_concentration(
    theoretical_kt: float,
    notch_radius_mm: float,
    ultimate_tensile_mpa: float,
    *,
    torsion: bool = False,
) -> float:
    """Fatigue stress-concentration factor K_f, Shigley eq. 6-32."""
    kt = require_positive(theoretical_kt, "theoretical_kt")
    q = notch_sensitivity(notch_radius_mm, ultimate_tensile_mpa, torsion=torsion)
    return 1.0 + q * (kt - 1.0)


@dataclass(frozen=True)
class EnduranceStrength:
    """A corrected endurance limit and the Marin factors that produced it."""

    endurance_limit_mpa: float
    endurance_limit_prime_mpa: float
    surface: float
    size: float
    load: float
    temperature: float
    reliability: float
    miscellaneous: float
    approximations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "endurance_limit_mpa": self.endurance_limit_mpa,
            "endurance_limit_prime_mpa": self.endurance_limit_prime_mpa,
            "marin_factors": {
                "k_a_surface": self.surface,
                "k_b_size": self.size,
                "k_c_load": self.load,
                "k_d_temperature": self.temperature,
                "k_e_reliability": self.reliability,
                "k_f_miscellaneous": self.miscellaneous,
            },
            "approximations": list(self.approximations),
        }


def corrected_endurance_limit(
    *,
    ultimate_tensile_mpa: float,
    surface_finish: str = "machined",
    diameter_mm: float = 7.62,
    loading: str = "bending",
    temperature_c: float = 20.0,
    reliability: float = 0.50,
    miscellaneous: float = 1.0,
    endurance_limit_prime_override_mpa: float | None = None,
) -> EnduranceStrength:
    """Apply the Marin equation, Shigley eq. 6-17.

    The default diameter is the 7.62 mm rotating-beam specimen, for which the
    size factor is exactly one.
    """
    prime = (
        require_positive(
            endurance_limit_prime_override_mpa, "endurance_limit_prime_override_mpa"
        )
        if endurance_limit_prime_override_mpa is not None
        else endurance_limit_prime_mpa(ultimate_tensile_mpa)
    )
    k_a = surface_factor(surface_finish, ultimate_tensile_mpa)
    k_b = size_factor(diameter_mm, loading=loading)
    k_c = load_factor(loading)
    k_d = temperature_factor(temperature_c)
    k_e = reliability_factor(reliability)
    k_f = require_positive(miscellaneous, "miscellaneous")
    return EnduranceStrength(
        endurance_limit_mpa=prime * k_a * k_b * k_c * k_d * k_e * k_f,
        endurance_limit_prime_mpa=prime,
        surface=k_a,
        size=k_b,
        load=k_c,
        temperature=k_d,
        reliability=k_e,
        miscellaneous=k_f,
    )


@dataclass(frozen=True)
class SNCurve:
    """The finite-life line S_f = a N^b, Shigley eq. 6-14 through 6-16."""

    coefficient_a: float
    exponent_b: float
    fraction_f: float

    def strength_at_life_mpa(self, cycles: float) -> float:
        n = require_positive(cycles, "cycles")
        return self.coefficient_a * n**self.exponent_b

    def life_at_stress_cycles(self, reversed_stress_mpa: float) -> float:
        stress = require_positive(reversed_stress_mpa, "reversed_stress_mpa")
        return (stress / self.coefficient_a) ** (1.0 / self.exponent_b)


def sn_curve(
    ultimate_tensile_mpa: float,
    endurance_limit_mpa: float,
    *,
    fraction_f: float | None = None,
) -> SNCurve:
    """Build the high-cycle finite-life line, Shigley eq. 6-14 and 6-15."""
    ultimate = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    endurance = require_positive(endurance_limit_mpa, "endurance_limit_mpa")
    f = (
        require_positive(fraction_f, "fraction_f")
        if fraction_f is not None
        else fatigue_strength_fraction(ultimate)
    )
    if f * ultimate <= endurance:
        raise MechanicsError(
            "input.out_of_range",
            "f * S_ut must exceed the endurance limit for a finite-life line",
            {"f_sut_mpa": f * ultimate, "endurance_limit_mpa": endurance},
        )
    a = (f * ultimate) ** 2 / endurance
    b = -log10(f * ultimate / endurance) / 3.0
    return SNCurve(coefficient_a=a, exponent_b=b, fraction_f=f)


@dataclass(frozen=True)
class FatigueResult:
    criterion: str
    factor_of_safety: float
    yield_factor_of_safety: float
    alternating_mpa: float
    midrange_mpa: float
    endurance_limit_mpa: float
    approximations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def safe(self) -> bool:
        return min(self.factor_of_safety, self.yield_factor_of_safety) >= 1.0

    @property
    def governing_factor(self) -> float:
        return min(self.factor_of_safety, self.yield_factor_of_safety)


def langer_static_factor(
    alternating_mpa: float, midrange_mpa: float, yield_strength_mpa: float
) -> float:
    """First-cycle yield check, Shigley eq. 6-49."""
    total = abs(alternating_mpa) + abs(midrange_mpa)
    if total <= 0.0:
        return inf
    return require_positive(yield_strength_mpa, "yield_strength_mpa") / total


def evaluate_fatigue(
    *,
    alternating_mpa: float,
    midrange_mpa: float,
    endurance_limit_mpa: float,
    ultimate_tensile_mpa: float,
    yield_strength_mpa: float,
    criterion: str = "goodman",
    true_fracture_strength_mpa: float | None = None,
) -> FatigueResult:
    """Rate a fluctuating stress against one failure criterion.

    Goodman (eq. 6-46), Gerber (6-47), ASME-elliptic (6-48), Soderberg (6-45)
    and Morrow. Goodman is the conservative default the text uses for design;
    Gerber and ASME-elliptic fit the data more closely and give larger factors.

    A compressive midrange stress is taken as non-damaging and clamped to zero,
    which is the text's own treatment.
    """
    sigma_a = require_non_negative(abs(alternating_mpa), "alternating_mpa")
    sigma_m = max(float(midrange_mpa), 0.0)
    endurance = require_positive(endurance_limit_mpa, "endurance_limit_mpa")
    ultimate = require_positive(ultimate_tensile_mpa, "ultimate_tensile_mpa")
    yield_strength = require_positive(yield_strength_mpa, "yield_strength_mpa")

    if sigma_a <= 0.0 and sigma_m <= 0.0:
        factor = inf
    elif criterion == "goodman":
        inverse = sigma_a / endurance + sigma_m / ultimate
        factor = inf if inverse <= 0.0 else 1.0 / inverse
    elif criterion == "soderberg":
        inverse = sigma_a / endurance + sigma_m / yield_strength
        factor = inf if inverse <= 0.0 else 1.0 / inverse
    elif criterion == "morrow":
        fracture = require_positive(
            true_fracture_strength_mpa
            if true_fracture_strength_mpa is not None
            else ultimate + 345.0,
            "true_fracture_strength_mpa",
        )
        inverse = sigma_a / endurance + sigma_m / fracture
        factor = inf if inverse <= 0.0 else 1.0 / inverse
    elif criterion == "gerber":
        if sigma_a <= 0.0:
            factor = inf
        elif sigma_m <= 0.0:
            factor = endurance / sigma_a
        else:
            factor = (
                0.5
                * (ultimate / sigma_m) ** 2
                * (sigma_a / endurance)
                * (
                    -1.0
                    + sqrt(1.0 + (2.0 * sigma_m * endurance / (ultimate * sigma_a)) ** 2)
                )
            )
    elif criterion == "asme_elliptic":
        inverse = sqrt((sigma_a / endurance) ** 2 + (sigma_m / yield_strength) ** 2)
        factor = inf if inverse <= 0.0 else 1.0 / inverse
    else:
        raise MechanicsError(
            "input.unknown_criterion",
            f"unknown fatigue criterion {criterion!r}",
            {"supported": list(CRITERIA)},
        )

    return FatigueResult(
        criterion=criterion,
        factor_of_safety=factor,
        yield_factor_of_safety=langer_static_factor(sigma_a, sigma_m, yield_strength),
        alternating_mpa=sigma_a,
        midrange_mpa=sigma_m,
        endurance_limit_mpa=endurance,
    )


def alternating_and_midrange(
    maximum_mpa: float, minimum_mpa: float
) -> tuple[float, float]:
    """Split a stress cycle into its amplitude and mean, Shigley eq. 6-36."""
    high, low = float(maximum_mpa), float(minimum_mpa)
    if low > high:
        high, low = low, high
    return ((high - low) / 2.0, (high + low) / 2.0)


__all__ = [
    "CRITERIA",
    "EnduranceStrength",
    "FatigueResult",
    "LOAD_FACTORS",
    "RELIABILITY_FACTORS",
    "SNCurve",
    "SURFACE_FACTORS",
    "alternating_and_midrange",
    "corrected_endurance_limit",
    "endurance_limit_prime_mpa",
    "evaluate_fatigue",
    "fatigue_strength_fraction",
    "fatigue_stress_concentration",
    "langer_static_factor",
    "load_factor",
    "neuber_sqrt_a_mm",
    "notch_sensitivity",
    "reliability_factor",
    "size_factor",
    "sn_curve",
    "surface_factor",
    "temperature_factor",
]
