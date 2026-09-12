# Changelog

## Unreleased

### Execution trust

- Verify the script before executing it, not only the interpreter. `run_freecad_script` pinned the FreeCAD executable by digest and filesystem identity, re-checking it after every run, and then read and executed whatever file it was handed without checking anything. The reviewed digests of the eight packaged scripts now live in `package_resources.PACKAGED_SCRIPT_DIGESTS`, the runner enforces them, and a packaged script that has been edited on disk is refused before FreeCAD is started rather than executed silently.
- Require any script that is not in the manifest to pass an explicit `expected_script_sha256`. Running an unreviewed file stays possible and stops being accidental.
- Read that manifest from the package in the packaging tests instead of holding a second copy, since a test-only duplicate could drift from the one that now gates execution.

### General mechanical calculation

- Source the gear engine's Marin factors and bearing-family table from `mechanics` instead of holding second copies. `gear_sizing` now imports the endurance limit, surface factor, size factor and reliability table from `mechanics.fatigue`, and derives its bearing coefficients from `mechanics.bearings`. The dependency runs one way only, from `gear_sizing` to `mechanics`.
- The exact `(d/7.62)^-0.107` size factor raises the worked example's shaft endurance limit by 0.22 percent and shrinks its solved Goodman diameter by 0.06 percent, from 21.042 to 21.030 mm. The selected shaft diameter is unchanged at 25 mm because ISO 15 bore rounding absorbs the difference, so no built design moves; the tooth counts, module, face width, centre distance, every strength result and the bearing capacity are all byte-identical.

- Add `mechanics/`, a standard-library package implementing Shigley's Mechanical Engineering Design (Budynas and Nisbett) independently of any one component. Gears were never special: a shaft, a bolted joint and a bracket all reduce to a stress state rated against a static or a fatigue criterion, and that reduction now lives in one place instead of inside the gear engine.
- Chapter 3, load and stress analysis: the full three-dimensional stress state, principal stresses from the characteristic cubic, von Mises, Mohr quantities, the elementary load cases, section properties, and thin- and thick-walled cylinders.
- Chapter 5, static failure: maximum-shear, distortion-energy and ductile Coulomb-Mohr for ductile materials; maximum-normal, brittle Coulomb-Mohr and modified-Mohr for brittle ones. A theory returns the factor of safety it computed and never raises on a failing margin, because the margin is the answer.
- Chapter 6, fatigue: the Marin factors, corrected endurance limit, the finite-life S-N line, notch sensitivity and fatigue stress concentration, and the Goodman, Gerber, ASME-elliptic, Soderberg and Morrow criteria, each reported alongside the first-cycle yield check.
- Chapter 8, bolted joints: tensile-stress area, metric coarse-thread and property-class tables, bolt and Wileman member stiffness, the joint constant, recommended preload and tightening torque, and the yielding, overload and separation margins together. This is the calculation half of the fastener gate the model validator already enforces geometrically: the validator proves a bolt is installed correctly and cannot prove the joint will hold.
- Solve the principal-stress cubic by deflating the best-conditioned root and closing the remaining quadratic in closed form. The trigonometric solution alone loses about half its significant digits at a repeated root, and a repeated root is the ordinary case: uniaxial tension, pure shear and every plane-stress state has one. Uniaxial tension now returns exactly its applied stress and two zeros.
- Take the size factor as `(d/7.62)^-0.107` rather than the rounded `1.24 d^-0.107` the text also prints, because only the first is exactly one at the rotating-beam specimen the factor is defined against; the rounded form returns 0.9978 there.
- Chapter 4, deflection and stiffness: spring rates in series and parallel, the standard beam cases of table A-9, and column buckling with the Euler and Johnson formulas selected by transition slenderness rather than by assumption, since rating an intermediate column by Euler overpredicts its capacity.
- Chapter 7, shafts: all four distortion-energy criteria, each available both as a diameter for a required factor of safety and as the factor of safety actually achieved at a diameter, because a solved diameter is rounded up to a stock or bearing-bore size and the margin at that rounded size is the one the design has.
- Chapter 10, springs: helical compression springs, with the Bergstrasser and Wahl corrections, the table 10-1 end treatments, the A/d^m wire-strength fits carrying the diameter range each was fitted over, and a rating both at working load and shut solid. A wire strength requested outside its fitted range is refused rather than extrapolated.
- Chapter 11, rolling-contact bearings: rating life, the ball and roller load exponents, equivalent radial load including the rotating-outer-ring penalty, and required dynamic capacity at a stated reliability through the three-parameter Weibull model of equation 11-18. That model independently reproduces the tabulated ISO 281 life adjustment already used by the gear engine to within one percent at the reliabilities most often designed to.
- Chapters 13 to 15, gears: nomenclature and kinematics for every gear type, gear and planetary train values, contact ratio, the three interference limits, the helical transverse-to-normal plane relations with virtual tooth count, tooth-force resolution for spur, helical, bevel and worm meshes, bevel pitch angles, worm lead angle, efficiency and self-locking, and the AGMA bending and contact equations in general form. The verified AGMA spur factors stay in `gear_sizing` and are not duplicated; what is added here is helical, bevel and worm, which that package never covered.
- Chapter 9, welds: fillet-weld throat, the unit-property weld groups of table 9-2, primary, torsional and bending shear combined on the throat, and the AISC electrode allowables.
- Chapters 16 and 17, friction drives: uniform-wear and uniform-pressure disk clutches with the closed-form optimum annulus, band brakes, flat and V belt drives with open-drive geometry and centrifugal tension, flywheel inertia, and single-stop brake temperature rise. The belt equation is implemented once and shared, because a band brake and a flat belt obey the same relation.
- Chapter 12, journal bearings: clearance, unit load, Sommerfeld number, Petroff friction and the resulting power loss. The Raimondi and Boyd performance charts are numerical solutions of the Reynolds equation and cannot be reproduced from their published form, so the chart-derived variables are taken as arguments and the estimate names which ones it could not compute rather than inventing fits for them.
- State the fatigue-strength fraction of figure 6-18 and the Neuber constant of equation 6-35 as curve fits that could not be verified against the published figures, and accept an explicit override for both. Every other shipped constant is checked against an independent anchor, including the whole metric coarse-thread series, whose tabulated areas are reproduced from the stress-area formula to within 0.4 percent.

## 0.10.0 - 2026-09-11

### Host-verified model evidence

- Re-validate the model in a process the agent does not control before recording a result as completed. `record_result` now runs the packaged `validate_model.py` under the pinned FreeCAD executable with a host-generated nonce, and accepts the attempt only when that nonce and the model digest come back unchanged. The recorded validation report is written by the same agent that did the modelling, so on its own it can only ever be self-reported; this is the part of the evidence the agent cannot author. The nonce and digest are verified in `record_result` rather than inside the validator, because a check performed by a replaceable component on itself is no check at all.
- Record the returned evidence under `validation.host_evidence`, and treat a validator that cannot run, answers a different nonce, or describes different bytes as `incomplete` rather than failing the design outright, so the attempt still reaches the correction ledger with its reason.
- Accept an injected `model_validator` alongside the existing `seed_creator` and `source_normalizer`, so the FreeCAD dependency stays testable.
- Require a standard-part validation report to carry the digest of the file it certifies and at least one mandatory check. `register_download` compared only `status == "passed"` and never related the report to the part, so any file containing that one key registered any STEP or FCStd file into the catalog under an inherited trust tier.

### Validation evidence

- Require a recorded validation report to name a known validator and report schema, to carry at least one mandatory check, to include the `file.exists`, `document.open`, `document.recompute`, and `document.geometry` baseline, and to agree with its own summary counts. A report with an empty `checks` list, or one whose only failures are marked advisory, previously satisfied the completion gate vacuously and recorded a design as `completed`; it is now `incomplete`. The existing model-hash binding is unchanged.
- Require PNG evidence to be a structurally real image, verifying the signature and the `IHDR` chunk before reading its dimensions, rather than accepting any nonempty file whose name ends in `.png`.
- Reject Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`) and trailing dots or spaces in `require_safe_id`, matching the portable-name handling `fcstd_security` already applies to archive entries. Ordinary identifiers such as `NULL` and `console` remain valid.

### Gear-drive sizing

- Bound every sizing value `create_gear_pair.py` builds from: module, face width, pressure angle, centre distance, tooth counts within the 12-400 range the tooth-form module already enforces, and bore against the root diameter. A sizing document can reach the builder without having come from the sizing engine, so the builder no longer trusts the file it was handed. An unbounded tooth count previously ran without terminating, and a bore larger than the root diameter produced an empty model that reported itself valid.
- Reject non-finite custom material values. The guard compared `<= 0`, which admits both `nan` and `inf`.

## 0.9.0 - 2026-09-11

### Gear-drive sizing

- Add `design_gear_size` and `mech-cad-design gear size`, sizing a spur reduction from duty inputs (power, pinion and gear speed, material, duty, life, safety factor) through tooth-count selection, module iteration, forces, bending and contact stress by ANSI/AGMA 2101-D04 through ISO 6336-3 form factors, shaft diameter, and required bearing dynamic capacity. Pure standard-library `gear_sizing/` package; no numerical dependency added.
- Separate every result into `given` inputs, `derived` values, declared `assumptions`, and stated `limitations`. Preliminary sizing evidence only, never a strength certification: scuffing, micropitting, thermal rating, and lubrication are explicitly out of scope, and only material allowables cross-checked against their published US-unit originals are shipped.
- Keep every rejected module iteration in the record alongside the accepted one, so the selection can be audited rather than trusted blindly.
- Add `gear_validation_spec.build_gear_validation_spec`, turning a sized result into a `freecad-model-validation` specification that checks a built pair's module, tooth counts, face width, and centre distance against the calculated values, plus an interference gate. A model that drifts from its own sizing result fails on that exact value and reaches the correction ledger the same way any other validation failure does.
- Add the packaged `create_gear_pair.py` FreeCAD script, modelling a standard involute spur pair from a sizing result and stamping the design values onto the objects as properties.

## 0.8.0 - 2026-09-08

### Learning from corrected mistakes

- Record every validation attempt in an append-only `correction_ledger` inside `design.json`, holding the model SHA-256, the outcome, and each failed check. A later attempt appends and never rewrites an earlier one, so a failure that was fixed stays readable after the model passes. Sessions written before this release load unchanged and report an empty ledger.
- Derive Design Lesson candidates from the mandatory checks a design failed and then corrected. Each carries `origin: validation_correction`, its `validator::check_id` signature, and its attempt count, and joins any agent-proposed candidate on the same immutable review card under the same single publication decision.
- Keep derivation deterministic, timestamp-free, and free of language-model output, so repeating a confirmation produces a byte-identical review card. Derivation never blocks completion: a clean design derives nothing, an advisory-only failure derives nothing, and a malformed derivation is dropped instead of raising a candidate error.
- Publish correction lessons as ordinary Design Lessons, so `design_knowledge_retrieve` returns them to later designs in the same scope and the loop closes.
- Add the `design_mistakes` MCP tool and `mech-cad-design design mistakes` command, reporting corrected and outstanding defects as `DesignMistakeSummary/v1`. Corrections are reported only while the newest attempt passes, so a currently failing design never claims a fix.
- Report every failed check from a validation report rather than stopping at the first one, without changing which status or warning a recorded result produces.

### Knowledge storage

- Add an embedded SQLite knowledge backend and make it the default, so durable Product Family Knowledge and Design Lessons work with no database service running. PostgreSQL remains available for shared team use and is selected automatically whenever `MECH_DESIGN_DATABASE_URL` is set.
- Select the backend explicitly with `MECH_DESIGN_KNOWLEDGE_BACKEND`, and relocate the local store with `MECH_DESIGN_SQLITE_PATH`.
- Share one set of validated record builders between both backends so publication, scoping, canonical shapes, and idempotency stay identical across them.
- Add `mech-cad-design migrate` to bring workspaces from earlier versions up to the current layout and knowledge schema, with `--dry-run` reporting every planned action and no change to existing design jobs.
- Add `mech-cad-design knowledge import-postgres` to copy one scope's durable records from PostgreSQL into the local store through a read-only snapshot, skipping records already present.
- Add `mech-cad-design design start`, `list`, `open`, and `status` so design jobs can be created, discovered, and resumed from the command line. Listing, opening, and reading a design job no longer require a configured FreeCADCmd.
- Add `mech-cad-design family start`, `analyze`, `review`, `publish`, and `status` so Product Family onboarding runs from the command line as well as MCP.
- Report the selected knowledge backend and whether its store is initialized in `mech-cad-design status`, and surface full setup diagnostics instead of a bare error when a command is blocked.

## 0.7.1 - 2026-09-01

- Store immutable Design Lesson review cards under model-SHA-addressed paths so evidence from an earlier model revision cannot block a confirmed newer revision.
- Keep candidate validation ahead of card publication: `candidate_errors` creates no formal review card, while corrected candidates for the same model may proceed to `review_pending`.
- Preserve state-bound legacy `lesson-review/review.json` cards unchanged and compatible with existing publication decisions.
- Require semantic expansion to authoritative manufacturer, standards-body, industry-association, or attributable authorized-distributor sources when configured structured standard-part providers miss a component that is reasonably likely to exist commercially.
- Treat a structured zero result only as a structured-search miss; allow a final not-found report only after a reasonable authoritative-source search, with complete query and provenance records.

## 0.7.0 - 2026-08-31

- Establish one normal design process from requirements and direction approval through knowledge retrieval, CAD modeling, exact-model validation, final confirmation, and automatic Design Lesson evaluation.
- Accept Chinese and English confirmation by meaning as `APPROVE`, `REJECT`, or `UNCLEAR`; no fixed confirmation phrase is required.
- Keep completed model state independent from lesson evaluation and publication. Database or graph availability cannot invalidate a completed CAD result.
- Add immutable Design Lesson review cards and one explicit decision before durable publication.
- Keep individual design-session state in portable filesystem JSON and use PostgreSQL/pgvector plus Neo4j only for durable Product Family Knowledge and Design Lessons.
- Replace the previous process APIs, persistence schema, tests, documentation, and project skill with the version 0.7.0 contract. Existing databases from earlier releases require a fresh knowledge database.
- Preserve FreeCAD/CadQuery modeling, standard-part provenance, model validation, Product Family Knowledge, Design Lessons, macOS support, and Windows support.

## Earlier releases

Earlier release notes are available from their corresponding Git tags.
