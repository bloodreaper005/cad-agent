"""Run the shared FreeCAD STEP validator with provider-neutral provenance checks.

Usage inside FreeCADCmd:
  freecadcmd validate_external_step.py STEP MANIFEST SPEC REPORT_DIR VALIDATOR_MODULE

NOT WIRED IN. Nothing in this package calls this file, and it cannot run as
shipped. Its digest is pinned and enforced like every other packaged script,
which means only that the file is what the manifest says it is; it does not
mean anything reaches it.

Two things are missing, both known:

  * VALIDATOR_MODULE is the skill's freecad_model_validation.py, and
    .agents/skills/ is in neither the wheel nor the sdist, so on an installed
    copy that argument has nothing to point at.
  * validate_step() calls _save_snapshot() unconditionally, which needs
    FreeCADGui. It catches its own failure but records visual.snapshot as a
    mandatory failed check, so a headless run would report failed for a missing
    render rather than for anything about the part.

This file is the host-side half of STEP provenance validation, not an orphan.
standard_parts.register_download currently accepts an agent-authored report for
catalogue parts; the provider-neutral manifest checks below are exactly what it
does not do for itself. Completing it means packaging the shared validator with
a drift guard against the skill copy, making the snapshot check non-fatal
without a GUI, and calling this through freecad_runner. That also yields
host-run validate_fcstd, which is the same gap on the FCStd side.

Until then this is documented unbuilt work rather than a capability.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 6:
        raise SystemExit("expected STEP MANIFEST SPEC REPORT_DIR VALIDATOR_MODULE")
    step, manifest_path, specification, report_dir, module_path = map(Path, sys.argv[1:6])
    spec = importlib.util.spec_from_file_location("shared_freecad_model_validation", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load shared FreeCAD validation module")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)

    def provider_neutral_manifest_checks(report, manifest):
        provider = str(manifest.get("provider") or manifest.get("provider_name") or "").strip()
        provider_id = str(manifest.get("provider_id") or "").strip()
        part_id = str(manifest.get("part_id") or manifest.get("part_number") or "").strip()
        source_url = str(manifest.get("source_url") or "").strip()
        designation = str(manifest.get("standard") or "").strip()
        nominal_size = str(manifest.get("nominal_size") or "").strip()
        validator._check(report, "manifest.provider", bool(provider), "Manifest declares the actual provider", actual=provider, expected="non-empty")
        validator._check(report, "manifest.provider-id", bool(provider_id), "Manifest declares a stable provider ID", actual=provider_id, expected="non-empty")
        validator._check(report, "manifest.part-id", bool(part_id), "Manifest declares the provider part ID", actual=part_id, expected="non-empty")
        validator._check(report, "manifest.source", source_url.startswith("https://"), "Manifest declares an HTTPS source URL", actual=source_url, expected="https://...")
        validator._check(report, "manifest.designation", bool(designation), "Manifest declares a standard or catalog designation", actual=designation, expected="non-empty")
        validator._check(report, "manifest.nominal-size", bool(nominal_size), "Manifest declares nominal size", actual=nominal_size, expected="non-empty")

    validator._step_manifest_checks = provider_neutral_manifest_checks
    result = validator.validate_step(step, manifest_path, specification, report_dir)
    print("MECH_EXTERNAL_STEP_VALIDATION=" + json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
