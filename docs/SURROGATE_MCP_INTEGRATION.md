# Surrogate Screening MCP Integration Boundary

## Position in the system

`fea-surrogate` is an optional external integration that supplies learned
screening estimates. It is not bundled with Mech CAD Design Agent, is not a
backend dependency of the Mech CAD Design MCP, and is not built or released by
this project. Mech CAD Design MCP does not discover, launch, stop, probe, or
validate it at runtime.

The integration answers one question: *is this geometry obviously in trouble?*,
in milliseconds, so that expensive checks run on the right candidate. It does
not analyse. A screening estimate never changes `model_status`, never satisfies
a validation check, and never justifies confirmation. A design that screens
badly still completes on passed validation; one that screens well still requires
it. See [SURROGATE_SCREENING.md](SURROGATE_SCREENING.md) for the consumer-side
contract and the rules `surrogate_screening.py` enforces on arrival.

Everything in this project works with the integration absent. A workspace with
no surrogate configured runs requirement discovery, CAD modeling, validation,
confirmation and Design Lesson evaluation unchanged.

## Why it is a separate repository

This is the load-bearing reason, and it is a licence fact rather than a
preference:

| Fact | Audited value |
| --- | --- |
| This project's licence | Apache-2.0 |
| `gmsh`, required for meshing in the surrogate | **GPL-2.0-or-later** |

Mech CAD Design Agent ships an sdist built from an `only-include` allowlist,
maintains [Third-Party Notices](../THIRD_PARTY_NOTICES.md), and keeps an audited
`third-party-components.toml`. A GPL meshing dependency inside that boundary
would be a genuine distribution problem. Keeping the surrogate in its own
repository, invoked over MCP, keeps the licence boundary where the process
boundary already is.

Three further reasons, none of them sufficient alone:

- `tests/test_boundaries.py` re-reads `pyproject.toml` specifically to stop
  heavy dependencies creeping in. This package is stdlib plus `mcp`, `psycopg`
  and `pywin32`; the surrogate needs torch, PyTorch Geometric, gmsh and scipy.
- Model weights are large binaries with their own release cadence, and have no
  business near an allowlisted sdist.
- The precedent already exists here. See
  [FREECAD_GUI_MCP_INTEGRATION.md](FREECAD_GUI_MCP_INTEGRATION.md), which
  documents the same shape of boundary for the interactive FreeCAD server.

## Audited upstream identity

Unlike the FreeCAD GUI MCP, this is not third-party code. It is a separate
project by the same author, and the separation is structural rather than a
question of ownership.

| Fact | Audited value |
| --- | --- |
| Source | `git@github.com:bloodreaper005/fea-surrogate.git` (private) |
| Emitted schema | `SurrogateScreening/v1` |
| Distribution relationship | external integration; not distributed by this project |
| Training data | self-generated parametric sweep; no third-party CAD redistributed |

`SimJEB` and `DeepJEB` are benchmarks only. Their labels are Open Data Commons
Attribution but their CAD is GrabCAD non-commercial, so nothing derived from
that CAD is trained on, shipped, or redistributed by either project.

### Dependency licences of the external integration

Recorded because they are the reason for the boundary, not because this project
distributes them:

| Package | Version observed | Licence |
| --- | --- | --- |
| gmsh | 4.15.2 | GPL-2.0-or-later |
| torch | 2.14.0 | Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |
| torch-geometric | 2.8.0 | MIT |
| scipy | 1.18.1 | BSD-3-Clause |
| matplotlib | 3.11.2 | Python Software Foundation License |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| mcp | 1.30.0 | MIT |
| huggingface-hub | 1.32.0 | Apache-2.0 |

## Model and calibration identity

A coverage claim means nothing without the model and the set it was measured on.
Both digests travel inside every document, and this project stores them verbatim
rather than normalising them.

| Fact | Audited value |
| --- | --- |
| Model | `sweep-mesh-gnn@0.4.0` |
| Weights SHA-256 | `8e3ad56d7accbc777ce7402969aeed6db2a57f7b66fadb94c0ec630e659fc539` |
| Feature version | `sweep/v2`, 13 columns |
| Calibration method | `mondrian_split_conformal_log_residual` |
| Calibration set size | 600 held-out samples, 100 per family |
| Nominal coverage | 0.90 |

Per-family bands, as multiplicative factors:

| Family | Band | Held-out n |
| --- | --- | --- |
| flange | ×/÷ 1.28 | 100 |
| pulley | ×/÷ 1.31 | 100 |
| spring | ×/÷ 1.33 | 100 |
| bracket | ×/÷ 1.38 | 100 |
| stepped_shaft | ×/÷ 1.51 | 100 |
| welded_t | ×/÷ 1.75 | 100 |

A part whose family is not declared receives the **widest** band, not the
average. An estimate whose group is unknown has not been shown to be covered at
the stated rate by anything narrower.

## Security boundary

The intended MCP transport is stdio. The server opens no listening socket and
makes no outbound request while screening; the model and calibration record are
read from local files named by `FEA_SURROGATE_WEIGHTS` and
`FEA_SURROGATE_CALIBRATION`. Mech CAD Design MCP creates no network dependency
on it.

Screening reads a STEP file and writes nothing into the design session except
through `design_screening_record`, which is subject to the same digest binding
as validation evidence: a changed model invalidates a stored screening result
exactly as it invalidates a validation report.

Rendered screening images are hash-bound and deliberately distinguishable from
validation renders. `_screening_images()` rejects a screening PNG offered as
validation evidence.

## What does not cross the boundary

The surrogate also draws a **load-case diagram** — the part with its held and
loaded regions marked and the load direction arrowed. It is genuinely useful,
because a part held on the wrong face screens cleanly and answers a different
question than the one asked, and nothing else makes that visible.

It does not cross this boundary, and that is deliberate. `SurrogateScreening/v1`
refuses unknown top-level keys, and the image cannot ride inside `fields[]`
because a `fields[]` entry is a *predicted field* and requires a per-node
interval. A load case is an input, not a prediction.

The decision is to leave it on the surrogate side rather than widen the schema
to carry it. The two are independent agents; the contract should carry what this
project enforces on arrival, and nothing else. The diagram is written next to
the screened STEP file and read there by whoever is looking at the part.

## What this project enforces on arrival

`surrogate_screening.py` refuses, rather than repairs, a document that:

- carries a point estimate — `lower` and `upper` are both required
- carries no calibration provenance, or a coverage outside `(0, 1)`
- carries non-finite or inverted bounds, or an empty `predictions[]`
- declares an empty domain envelope, which would claim to cover everything
- carries an `attestation` other than `screening_estimate`
- carries a `fields[]` entry without `interval_relative_path` — a predicted
  field with no per-node band is not storable
- carries an unexpected top-level key

`surrogate_domain.py` gates strictly and conjunctively: a missing key is a
refusal, never a permissive default, and the refusal names the violated bound
and what would have to change.

## Acceptance status

| Item | Status |
| --- | --- |
| `SurrogateScreening/v1` contract, both sides | passed |
| Domain refusal names the violated bound | passed |
| Screening cannot affect `model_status` or `design_confirm` | passed |
| Screening image refused as validation evidence | passed |
| Closed-form crosscheck against `mechanics` | passed for axial, round cantilever bending, round torsion |
| Deployed coverage against the solver | see below |
| Per-node field rendering | **not available**; the model has a graph-level head |

The surrogate provides screening evidence. It does not certify strength,
manufacturability, or standards compliance, and a screening interval is never
an analysis result.
