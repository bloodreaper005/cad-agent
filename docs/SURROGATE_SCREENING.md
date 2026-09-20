# Surrogate screening

Screening is a fast, learned estimate of what a structural analysis would say,
used to aim expensive work at the right candidate. It is not evidence, it does
not gate completion, and it refuses to answer outside the domain it was fitted
over.

## Why this does not contradict "nothing here guesses a number"

A learned surrogate predicts from data and therefore has an error distribution.
A point estimate from one would be a guess, and this package would stop being
able to say what it says on its first page.

Three rules keep the claim true.

**An interval, never a point.** `SurrogateScreening/v1` requires `lower` and
`upper` on every prediction. A document carrying a bare value is refused at the
parse, not widened into one. "Peak von Mises in [142, 198] MPa at 90% coverage,
calibrated on 2400 held-out runs" is a measurement with declared uncertainty,
which is the same register the gear engine already speaks in when it separates
`given` from `derived` and states its `limitations`.

**Calibration provenance is mandatory.** An interval with no `calibration`
record is refused. The record names the method, the size of the calibration set
and its SHA-256, so the coverage figure is attributable rather than asserted.
Split-conformal methods are the intended family because they give
distribution-free coverage without constraining the model that produced the
prediction.

**Refusal outside the fitted domain.** The dominant failure of a surrogate is
confident nonsense on geometry unlike anything it saw. `surrogate_domain`
declares the envelope and answers `in_domain` or `out_of_domain`, naming every
bound that was violated. This is the rule the springs module already follows
when it refuses a wire strength requested outside its fitted range rather than
extrapolating.

## What it may not do

- It never sets `model_status`, never writes `validation`, and cannot reach
  `final_confirmation`. A design that screens badly is not blocked, and one that
  screens well is not complete.
- A screening image is never accepted as validation evidence.
- `attestation` is always `screening_estimate`. There is no value it can carry
  that would make it proof.

A screening record is stored in `design.json` under `screening`, bound to the
model SHA-256 it was taken against. A later byte change invalidates it, exactly
as it invalidates validation evidence.

## The closed-form anchor

Where a load case reduces to a case `mechanics/` computes exactly, the interval
is tested against that closed-form value. `surrogate_crosscheck` reports
`interval_contains`, `interval_excludes` or `not_applicable`.

An interval that excludes the closed-form answer means the surrogate is
miscalibrated on that case. The analytical value is never adjusted to agree, and
`not_applicable` is the honest default: a comparison is attempted only when the
load case is one of the elementary Shigley chapter 3 cases and the prediction is
stated in the same units.

Supported anchors today:

| `kind` | Inputs | Criterion |
| --- | --- | --- |
| `axial` | `force_n`, `area_mm2` | eq. 3-22, von Mises = \|sigma\| |
| `round_cantilever_bending` | `force_n`, `length_mm`, `diameter_mm` | eq. 3-24, von Mises = \|sigma\| |
| `round_torsion` | `torque_nmm`, `diameter_mm` | eq. 3-37, von Mises = sqrt(3) tau |

## Full fields and rendered plots

A `fields[]` entry carries a per-node result and the image rendered from it. It
must carry `interval_relative_path` alongside `values_relative_path`: a field
with no per-node bands cannot be stored, because a contour plot with no
uncertainty behind it is the one artifact this subsystem must not produce.

A renderer consuming these must keep visual saliency matched to confidence —
uncertain regions quieter, out-of-domain regions hatched rather than
interpolated over, and the coverage level and model identity legible in the
image itself, because images are read far from the JSON that qualifies them.

## Where the model lives

Not in this package. `pyproject.toml` stays at `mcp`, `psycopg` and `pywin32`,
and `tests/test_boundaries.py` enforces that no model dependency enters. The
surrogate runs in a separate process that emits `SurrogateScreening/v1`, the way
the external FreeCAD GUI MCP is a separate project under a documented boundary.

## Commands

```bash
mech-cad-design screening record --workspace W --design-id ID \
  --screening-file screening.json --query-json '{"material_class":"linear_elastic"}' \
  [--load-case-json '{"kind":"axial","force_n":17000,"area_mm2":100}']
mech-cad-design screening status --workspace W --design-id ID
```

The MCP surface exposes the same two operations as `design_screening_record`
and `design_screening_status`.
