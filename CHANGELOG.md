# Changelog

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
