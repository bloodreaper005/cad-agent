# Mech CAD Design Agent project instructions

## Project scope

- This repository develops a deterministic mechanical design agent for FreeCAD. Keep changes focused on requirements, CAD modeling, standard parts, validation, reusable engineering knowledge, and release infrastructure.
- The public agent does not include rendering, video, or assembly-animation subsystems unless the project scope is explicitly expanded.
- Use the `mech-cad-design` MCP for design state and knowledge operations and the configured `freecad` MCP for interactive inspection and CAD edits.
- The agent provides design and validation evidence. It does not independently certify strength, safety, manufacturability, or standards compliance.
- Gear-drive sizing (`design_gear_size`, `mech-cad-design gear size`) computes bending and contact stress from AGMA/ISO relations, which is a deliberate expansion of that boundary into preliminary sizing evidence. It follows the same rule as validation: a result documents the checks it ran and the assumptions it made, and is never FEA, a strength certification, or manufacturing release. Every result states what it did not evaluate.

## Normal design process

- Use one process for new designs, existing-model edits, and resumed work:
  user request → requirement clarification → short design proposal → one direction approval → knowledge retrieval → CAD modeling → automatic validation and correction → final confirmation → automatic Design Lesson evaluation.
- Interpret clear Chinese and English responses by meaning as `APPROVE`, `REJECT`, or `UNCLEAR`. Never require a fixed phrase.
- After direction approval, create or resume `designs/<design-id>/model.FCStd`. For an existing model, preserve the user's source file and work on a session-owned copy.
- Retrieve applicable Product Family Knowledge and Design Lessons before substantive modeling. Use matches; continue normally when there are no matches or the knowledge service is unavailable, unless the user explicitly requires a named knowledge item.
- Continue CAD implementation and safe validation-driven corrections without further approval while the work remains inside the accepted design direction. A changed function, mechanism, key interface, specified material, manufacturing process, or explicit user constraint requires a revised proposal and direction approval.
- Bind completion evidence to the exact model SHA-256. Any model change invalidates earlier validation evidence.
- Record every validation attempt, including the failed ones, through `design_record_result`. The correction ledger is how the agent learns from its own mistakes; skipping a failed attempt to keep the record clean destroys that evidence. Read it back with `design_mistakes` before confirming.
- Final confirmation records acceptance of the completed design and starts Design Lesson evaluation. It must not reopen, downgrade, or block a completed model.
- Expect lessons derived from the mandatory checks this design failed and then corrected. They arrive on the same review card marked `origin: validation_correction`, alongside any lesson you propose. Review them as you would your own, and decline any that generalize badly.
- If evaluation finds no useful lesson, record that outcome and finish. If it finds useful knowledge, create an immutable review card. Ask once whether to publish that card to the durable knowledge store; rejection or an unavailable database leaves the model completed.

## Requirement discovery

- For non-trivial work, establish the function, operating sequence, units, envelope, interfaces, loads, motion, material, environment, standards, standard parts, fits, manufacturing constraints, maintenance, safety constraints, and acceptance criteria as applicable.
- Separate user facts from derived values and assumptions. Stop for direction when a missing choice materially affects geometry, safety, compatibility, cost, or validation.
- Record numeric requirements with units and explicit validation tolerances. Do not invent manufacturing tolerances.

## FreeCAD and standard parts

- Confirm the local FreeCAD MCP connection before reading or creating geometry.
- Prefer typed FreeCAD tools for simple operations and bounded Python execution only when the FreeCAD API is necessary.
- Keep the FreeCAD bridge local and `remote_enabled=false` unless remote access is separately requested and reviewed.
- Inspect meaningful geometry changes visually. Confirm names, placements, dimensions, shape state, and recompute results.
- For standard hardware and catalog components, use the configured standard-part providers before custom modeling. Preserve provider, manufacturer, standard, size, catalog identity, version, license, and SHA-256 provenance.
- Do not silently substitute custom geometry for a standard component. Ask for approval when no suitable verified component is available.

## Validation

- Validate every AI-created or visibly modified FCStd, assembly, or imported STEP component before reporting completion.
- Build a validation specification from the accepted requirements. Check recompute state, shape validity, solids, positive volume, dimensions, placements, required interfaces, provenance, BOM consistency, and applicable interference or fastener contracts.
- Inspect both machine-readable evidence and the generated validation view. Repair and rerun safe failures automatically.
- A passed report documents the checks performed; it is not FEA, manufacturing release, certification, or legal standards compliance.

## Learning from mistakes

- Treat a failed validation as data, not as noise to be hidden. Record it, correct it, and let the ledger carry both.
- Derived lessons are deterministic and carry no language-model output. Do not hand-write a candidate that merely restates a derived one; add a candidate only for engineering judgment the ledger cannot see.
- Before substantive modeling, use retrieved correction lessons as preventive checks. A published lesson naming a validator and check is a defect this design group has already paid for once.

## Knowledge architecture

- Filesystem JSON stores the state of an individual design session. No database is required for CAD modeling or validation.
- The knowledge store holds durable Product Family Knowledge, Design Lesson review cards, publication decisions, lessons, and searchable assertions. An embedded SQLite database inside the workspace is the default; PostgreSQL is used instead whenever `MECH_DESIGN_DATABASE_URL` is set, and `MECH_DESIGN_KNOWLEDGE_BACKEND` selects one explicitly.
- Both backends share one repository interface, one set of record builders, and identical scoping and idempotency, so behavior must not diverge between them.
- Neo4j is a rebuildable relationship projection. The knowledge store remains authoritative if projection fails.
- Keep knowledge scoped by organization, design group, family, model, applicability, and authorization.
- A database created by an earlier incompatible release must fail with a clear reinitialization message. Version 0.7.0 does not migrate removed process data.

## Portability and security

- Support Python 3.12+ on macOS and Windows. Use `pathlib`, package resources, explicit configuration, UTF-8, and platform adapters where behavior differs.
- Do not commit machine paths, usernames, secrets, runtime data, or generated CAD artifacts.
- Keep PostgreSQL, Neo4j, and RPC services on loopback interfaces by default. Do not add telemetry, hidden remote access, or execution of untrusted CAD-embedded code.

## Testing and release

- Add or update focused tests for each behavioral change, then run the supported offline suite. Run database and FreeCAD integration tests when those boundaries change and the required services are available.
- Verify wheel and source distribution contents, clean installation, CLI and MCP entry points, package resources, migrations, configuration bootstrap, public allowlists, and documentation links before release.
- Do not weaken a failing check to obtain a pass. Document precise environment-based skips and unsupported claims.
- Keep generated FCStd/STEP models, drawings, images, BOM exports, validation results, databases, knowledge contents, caches, and credentials outside the public repository.
- Do not push, publish, tag, or open a pull request unless the user explicitly requests that external action.
