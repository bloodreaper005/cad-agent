from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from . import __version__
from .approval_semantics import APPROVE, classify_approval
from .bootstrap_diagnostics import DiagnosticGateError
from .bootstrap_runtime import BootstrapRuntime
from .database_bootstrap import bootstrap_knowledge_database
from .design_session import DesignSessionService
from .freecad_runner import run_freecad_script
from .gear_sizing import (
    GearDriveInput,
    GearSizingError,
    size_gear_drive,
    to_mapping,
    to_review_sheet,
)
from .knowledge_backend import build_repository
from .package_resources import freecad_scripts_directory
from .secure_fs import atomic_publish_new, atomic_replace
from .knowledge_import import import_into_sqlite, read_postgres_knowledge
from .knowledge_repository import KnowledgeRepository, KnowledgeScope
from .product_family_knowledge import ProductFamilyKnowledgeService
from .long_term_knowledge_database import publish_source_backup, read_source_export
from .long_term_knowledge_migration import build_parity_probes
from .long_term_knowledge_target import (
    build_simplified_payload,
    create_target_database,
    import_simplified_payload,
    validate_simplified_target,
)
from .models import canonical_json
from .standard_part_configuration import (
    disable_standard_part_catalog,
    enable_standard_part_catalog,
    load_standard_part_provider_catalog,
)
from .workspace_bootstrap import (
    BootstrapFailure,
    initialize_workspace,
    read_workspace_manifest,
)
from .workspace_migration import migrate_workspace


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mech-cad-design")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="initialize a design workspace")
    init.add_argument("--workspace", type=Path, required=True)
    init.add_argument("--actor")
    init.add_argument("--organization")
    init.add_argument("--design-group")
    init.add_argument("--dry-run", action="store_true")

    status = commands.add_parser("status", help="inspect runtime readiness")
    status.add_argument("--workspace", type=Path)

    knowledge = commands.add_parser("knowledge", help="manage knowledge infrastructure")
    knowledge_commands = knowledge.add_subparsers(dest="knowledge_command", required=True)
    knowledge_bootstrap = knowledge_commands.add_parser("bootstrap")
    knowledge_bootstrap.add_argument("--workspace", type=Path)

    migrate = commands.add_parser(
        "knowledge-migrate", help="analyze or execute the simplified knowledge migration"
    )
    mode = migrate.add_mutually_exclusive_group()
    mode.add_argument("--analyze-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    migrate.add_argument("--source-env", type=Path)
    migrate.add_argument("--output", type=Path)
    migrate.add_argument("--analysis-report", type=Path)
    migrate.add_argument("--target-name")
    migrate.add_argument("--cutover-env", type=Path)

    standard = commands.add_parser("standard-parts")
    standard_commands = standard.add_subparsers(dest="standard_command", required=True)
    providers = standard_commands.add_parser("providers")
    providers.add_argument("--category", default="")
    sources = standard_commands.add_parser("status")
    sources.add_argument("--workspace", type=Path)
    catalog = standard_commands.add_parser("catalog")
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    enable = catalog_commands.add_parser("enable")
    enable.add_argument("--root", type=Path, required=True)
    enable.add_argument("--workspace", type=Path, required=True)
    disable = catalog_commands.add_parser("disable")
    disable.add_argument("--workspace", type=Path, required=True)

    migrate_workspace_command = commands.add_parser(
        "migrate", help="upgrade an existing workspace to the current layout"
    )
    migrate_workspace_command.add_argument("--workspace", type=Path)
    migrate_workspace_command.add_argument("--dry-run", action="store_true")

    knowledge_import = knowledge_commands.add_parser(
        "import-postgres", help="copy PostgreSQL knowledge into the local store"
    )
    knowledge_import.add_argument("--workspace", type=Path)
    knowledge_import.add_argument("--source-env", type=Path, required=True)

    design = commands.add_parser("design", help="create, inspect, and resume design jobs")
    design_commands = design.add_subparsers(dest="design_command", required=True)

    design_start = design_commands.add_parser("start", help="start or resume a design job")
    design_start.add_argument("--workspace", type=Path)
    design_start.add_argument("--design-id", required=True)
    design_start.add_argument("--title", required=True)
    design_start.add_argument(
        "--classification",
        choices=("new_design", "existing_model"),
        default="new_design",
    )
    requirements = design_start.add_mutually_exclusive_group(required=True)
    requirements.add_argument("--requirements-json")
    requirements.add_argument("--requirements-file", type=Path)
    design_start.add_argument("--proposal", required=True)
    design_start.add_argument("--approve", required=True)
    design_start.add_argument("--source", default="")

    design_status = design_commands.add_parser("status", help="read design job state")
    design_status.add_argument("--workspace", type=Path)
    design_status.add_argument("--design-id", required=True)

    design_open = design_commands.add_parser(
        "open", help="open an existing design job and report the next action"
    )
    design_open.add_argument("--workspace", type=Path)
    design_open.add_argument("--design-id", required=True)

    design_list = design_commands.add_parser("list", help="list design jobs")
    design_list.add_argument("--workspace", type=Path)

    design_mistakes = design_commands.add_parser(
        "mistakes", help="report corrected and outstanding validation defects"
    )
    design_mistakes.add_argument("--workspace", type=Path)
    design_mistakes.add_argument("--design-id", required=True)

    gear = commands.add_parser("gear", help="size gear drives")
    gear_commands = gear.add_subparsers(dest="gear_command", required=True)

    gear_size = gear_commands.add_parser(
        "size", help="size a spur gear drive from duty inputs"
    )
    gear_size.add_argument("--power-kw", type=float, required=True)
    gear_size.add_argument("--pinion-rpm", type=float, required=True)
    gear_size.add_argument("--gear-rpm", type=float, required=True)
    gear_size.add_argument("--gear-type", default="spur", choices=("spur",))
    gear_size.add_argument("--pinion-material", required=True)
    gear_size.add_argument("--gear-material", required=True)
    gear_size.add_argument(
        "--duty",
        required=True,
        choices=("uniform", "light", "moderate", "heavy"),
    )
    gear_size.add_argument("--life-hours", type=float, required=True)
    gear_size.add_argument("--safety-factor", type=float, required=True)
    gear_size.add_argument("--driver", default="electric_motor")
    gear_size.add_argument("--pressure-angle-deg", type=float)
    gear_size.add_argument("--face-width-ratio", type=float)
    gear_size.add_argument("--module-mm", type=float)
    gear_size.add_argument("--pinion-teeth", type=int)
    gear_size.add_argument("--gear-teeth", type=int)
    gear_size.add_argument(
        "--contact-safety-basis", default="stress", choices=("stress", "load")
    )
    gear_size.add_argument("--quality-grade", type=int, default=7)
    gear_size.add_argument("--reliability", type=float, default=0.99)
    gear_size.add_argument("--shaft-material", default="42CrMo4_QT")
    gear_size.add_argument("--bearing-type", default="deep_groove_ball")
    gear_size.add_argument(
        "--materials-file",
        type=Path,
        help="JSON file declaring allowables for materials outside the AGMA "
        "tables, such as stainless; the result records them as declared",
    )
    gear_size.add_argument("--out", type=Path)
    gear_size.add_argument(
        "--format",
        dest="output_format",
        default="json",
        choices=("json", "review"),
        help="review prints the sheet to read before approving the build",
    )

    gear_build = gear_commands.add_parser(
        "build",
        help="model the sized pair in FreeCAD, once the sizing is approved",
    )
    gear_build.add_argument("--workspace", type=Path)
    gear_build.add_argument("--design-id", required=True)
    gear_build.add_argument("--title", required=True)
    gear_build.add_argument("--sizing", type=Path, required=True)
    gear_build.add_argument(
        "--approve",
        required=True,
        help="your decision on the sizing, in your own words; nothing is "
        "modelled unless this reads as approval",
    )

    family = commands.add_parser("family", help="onboard Product Family Knowledge")
    family_commands = family.add_subparsers(dest="family_command", required=True)

    family_start = family_commands.add_parser("start", help="start a new product family")
    family_start.add_argument("--workspace", type=Path)
    family_start.add_argument("--onboarding-id", required=True)
    family_start.add_argument("--family-id", required=True)
    family_start.add_argument("--family-name", required=True)
    family_start.add_argument("--alias", action="append", default=[])

    family_analyze = family_commands.add_parser(
        "analyze", help="attach analyzed knowledge assertions"
    )
    family_analyze.add_argument("--workspace", type=Path)
    family_analyze.add_argument("--onboarding-id", required=True)
    analysis = family_analyze.add_mutually_exclusive_group(required=True)
    analysis.add_argument("--analysis-json")
    analysis.add_argument("--analysis-file", type=Path)

    family_review = family_commands.add_parser("review", help="record a review decision")
    family_review.add_argument("--workspace", type=Path)
    family_review.add_argument("--onboarding-id", required=True)
    family_review.add_argument("--decision", required=True)
    family_review.add_argument("--notes-json", default="{}")

    family_publish = family_commands.add_parser(
        "publish", help="publish approved knowledge to the knowledge store"
    )
    family_publish.add_argument("--workspace", type=Path)
    family_publish.add_argument("--onboarding-id", required=True)

    family_status = family_commands.add_parser("status", help="read onboarding state")
    family_status.add_argument("--workspace", type=Path)
    family_status.add_argument("--onboarding-id", required=True)

    return parser


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str))


def _exit_code(value: object) -> int:
    if not isinstance(value, dict):
        return 0
    status = value.get("status")
    if isinstance(status, dict):
        status = status.get("overall")
    return {
        "ok": 0,
        "ready": 0,
        "warning": 1,
        "setup_required": 2,
        "blocked": 3,
    }.get(str(status), 0)


def _runtime(workspace: Path | None) -> BootstrapRuntime:
    return BootstrapRuntime.from_process(
        cwd=Path.cwd(),
        environ=os.environ,
        workspace=workspace,
    )


def _database_url_from_env_file(path: Path) -> str:
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("source environment path must be a file")
    found: list[str] = []
    for raw_line in resolved.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "MECH_DESIGN_DATABASE_URL":
            continue
        normalized = value.strip()
        if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {
            "'",
            '"',
        }:
            normalized = normalized[1:-1]
        if normalized:
            found.append(normalized)
    if len(found) != 1:
        raise ValueError(
            "source environment must define MECH_DESIGN_DATABASE_URL exactly once"
        )
    return found[0]


def _atomic_write_text(path: Path, content: str) -> None:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, destination)
    finally:
        if temporary_name and Path(temporary_name).exists():
            Path(temporary_name).unlink()


def _write_json_report(path: Path, value: object) -> None:
    encoded = canonical_json(value)
    if "postgresql://" in encoded or "postgres://" in encoded:
        raise ValueError("migration report contains a database URL")
    _atomic_write_text(path, encoded + "\n")


def _read_json_report(path: Path) -> dict[str, object]:
    try:
        value = json.loads(Path(path).expanduser().resolve(strict=True).read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("analysis report is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("analysis report must be a JSON object")
    return value


def _analyze_knowledge_migration(source_env: Path, output: Path) -> dict[str, object]:
    source_path = Path(source_env).expanduser().resolve(strict=True)
    source_database_url = _database_url_from_env_file(source_path)
    export = read_source_export(source_database_url)
    payload = build_simplified_payload(export)
    probes = build_parity_probes(export)
    output_path = Path(output).expanduser().resolve()
    backup = publish_source_backup(export, output_path.parent / "source-export.json")
    report: dict[str, object] = {
        "schema_version": "SimplifiedKnowledgeMigrationAnalysis/v1",
        "status": "passed",
        "source_env_path": str(source_path),
        "source_env_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "source_export_sha256": export.sha256,
        "target_payload_sha256": payload.sha256,
        "counts": {
            "product_families": len(payload.product_families),
            "knowledge_assertions": len(payload.knowledge_assertions),
            "design_lessons": len(payload.design_lessons),
        },
        "probe_count": len(probes),
        "probes": [asdict(probe) for probe in probes],
        "source_backup": {
            "path": backup["path"],
            "sha256": backup["sha256"],
        },
    }
    _write_json_report(output_path, report)
    return report


def _unrelated_scope_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"unrelated-{prefix}-{digest}"


def _run_target_parity(target_database_url: str, probes: tuple[object, ...]) -> dict[str, object]:
    repositories: dict[tuple[str, str], KnowledgeRepository] = {}
    failed: list[dict[str, str]] = []
    for probe in probes:
        key = (probe.organization_id, probe.design_group_id)
        repository = repositories.setdefault(
            key,
            KnowledgeRepository(
                target_database_url,
                KnowledgeScope(probe.organization_id, probe.design_group_id),
            ),
        )
        found = False
        if probe.kind == "product_family":
            match = repository.match_product_family(
                query=probe.query,
                design_features={},
                requested_family_id=None,
            )
            found = bool(match and match["id"] == probe.expected_id)
        else:
            result = repository.search(
                query=probe.query, product_family_id=probe.product_family_id
            )
            collection = (
                result["assertions"]
                if probe.kind == "knowledge_assertion"
                else result["lessons"]
            )
            found = probe.expected_id in {str(row["id"]) for row in collection}
        if not found:
            failed.append(
                {
                    "kind": probe.kind,
                    "expected_id": probe.expected_id,
                    "query_sha256": hashlib.sha256(
                        probe.query.encode("utf-8")
                    ).hexdigest(),
                }
            )

    negative_failures = 0
    for (organization_id, design_group_id), repository in repositories.items():
        sample = next(
            probe
            for probe in probes
            if probe.organization_id == organization_id
            and probe.design_group_id == design_group_id
        )
        for scope in (
            KnowledgeScope(
                _unrelated_scope_id("org", organization_id), design_group_id
            ),
            KnowledgeScope(
                organization_id, _unrelated_scope_id("group", design_group_id)
            ),
        ):
            unrelated = KnowledgeRepository(target_database_url, scope)
            if unrelated.search(query=sample.query)["matches"]:
                negative_failures += 1
    return {
        "status": "passed" if not failed and not negative_failures else "failed",
        "probe_count": len(probes),
        "passed": len(probes) - len(failed),
        "failed": len(failed),
        "failures": failed,
        "negative_scope_failures": negative_failures,
    }


def _update_environment_database_url(path: Path, target_database_url: str) -> None:
    destination = Path(path).expanduser().resolve(strict=True)
    lines = destination.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    replacements = 0
    for line in lines:
        if line.strip().startswith("MECH_DESIGN_DATABASE_URL="):
            updated.append(f"MECH_DESIGN_DATABASE_URL={target_database_url}")
            replacements += 1
        else:
            updated.append(line)
    if replacements != 1:
        raise ValueError(
            "cutover environment must define MECH_DESIGN_DATABASE_URL exactly once"
        )
    _atomic_write_text(destination, "\n".join(updated) + "\n")


def _execute_knowledge_migration(arguments: argparse.Namespace) -> dict[str, object]:
    if arguments.analysis_report is None:
        raise ValueError("--analysis-report is required with --execute")
    if not arguments.target_name:
        raise ValueError("--target-name is required with --execute")
    analysis_path = arguments.analysis_report.expanduser().resolve(strict=True)
    analysis = _read_json_report(analysis_path)
    if analysis.get("schema_version") != "SimplifiedKnowledgeMigrationAnalysis/v1" or (
        analysis.get("status") != "passed"
    ):
        raise ValueError("--analysis-report must contain a passed simplified analysis")
    source_path = Path(
        arguments.source_env or str(analysis.get("source_env_path", ""))
    ).expanduser().resolve(strict=True)
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != analysis.get(
        "source_env_sha256"
    ):
        raise ValueError("source environment changed after analysis")
    source_database_url = _database_url_from_env_file(source_path)
    export = read_source_export(source_database_url)
    payload = build_simplified_payload(export)
    probes = build_parity_probes(export)
    expected_counts = {
        "product_families": len(payload.product_families),
        "knowledge_assertions": len(payload.knowledge_assertions),
        "design_lessons": len(payload.design_lessons),
    }
    if (
        export.sha256 != analysis.get("source_export_sha256")
        or payload.sha256 != analysis.get("target_payload_sha256")
        or expected_counts != analysis.get("counts")
        or len(probes) != analysis.get("probe_count")
        or [asdict(probe) for probe in probes] != analysis.get("probes")
    ):
        raise ValueError("source export no longer matches the passed analysis")

    target_database_url = create_target_database(
        source_database_url, arguments.target_name
    )
    imported = import_simplified_payload(target_database_url, payload)
    validation = validate_simplified_target(target_database_url, payload)
    parity = _run_target_parity(target_database_url, probes)
    report: dict[str, object] = {
        "schema_version": "SimplifiedKnowledgeMigrationReport/v1",
        "status": "passed" if parity["status"] == "passed" else "failed",
        "source_export_sha256": export.sha256,
        "target_payload_sha256": payload.sha256,
        "target_database_name": arguments.target_name,
        "import": asdict(imported),
        "validation": validation,
        "parity": parity,
        "cutover": bool(arguments.cutover_env and parity["status"] == "passed"),
    }
    output_path = (
        arguments.output.expanduser().resolve()
        if arguments.output
        else analysis_path.parent / "execution-report.json"
    )
    _write_json_report(output_path, report)
    if parity["status"] != "passed":
        raise ValueError("target parity validation failed; environment was not changed")
    if arguments.cutover_env:
        _update_environment_database_url(
            arguments.cutover_env, target_database_url
        )
    return report


def _knowledge_migrate(arguments: argparse.Namespace) -> dict[str, object]:
    if arguments.analyze_only:
        if arguments.source_env is None:
            raise ValueError("--source-env is required with --analyze-only")
        if arguments.output is None:
            raise ValueError("--output is required with --analyze-only")
        return _analyze_knowledge_migration(arguments.source_env, arguments.output)
    if arguments.execute:
        return _execute_knowledge_migration(arguments)
    raise ValueError("choose --analyze-only or --execute")


def _json_argument(inline: str | None, path: Path | None, label: str) -> dict[str, Any]:
    if path is not None:
        try:
            raw = Path(path).expanduser().resolve(strict=True).read_text("utf-8")
        except OSError as exc:
            raise ValueError(f"{label} file is unreadable") from exc
    else:
        raw = inline or "{}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is invalid JSON: {exc.msg}") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _design_service(runtime: BootstrapRuntime, *, require_freecad: bool):
    settings = (
        runtime.design_settings()
        if require_freecad
        else runtime.design_inspection_settings()
    )
    return DesignSessionService(settings)


def _gear_command(arguments: argparse.Namespace) -> dict[str, object]:
    specification = GearDriveInput(
        power_kw=arguments.power_kw,
        pinion_speed_rpm=arguments.pinion_rpm,
        gear_speed_rpm=arguments.gear_rpm,
        gear_type=arguments.gear_type,
        pinion_material=arguments.pinion_material,
        gear_material=arguments.gear_material,
        duty=arguments.duty,
        life_hours=arguments.life_hours,
        safety_factor=arguments.safety_factor,
        driver=arguments.driver,
        normal_pressure_angle_deg=arguments.pressure_angle_deg,
        face_width_ratio=arguments.face_width_ratio,
        forced_module_mm=arguments.module_mm,
        forced_pinion_teeth=arguments.pinion_teeth,
        forced_gear_teeth=arguments.gear_teeth,
        contact_safety_basis=arguments.contact_safety_basis,
        quality_grade_qv=arguments.quality_grade,
        reliability=arguments.reliability,
        shaft_material_key=arguments.shaft_material,
        bearing_type=arguments.bearing_type,
        custom_materials=(
            json.loads(arguments.materials_file.read_text(encoding="utf-8"))
            if getattr(arguments, "materials_file", None)
            else None
        ),
    )
    sized = size_gear_drive(specification)
    result = to_mapping(sized)
    if arguments.out is not None:
        arguments.out.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
    if getattr(arguments, "output_format", "json") == "review":
        print(to_review_sheet(sized))
        raise SystemExit(_gear_exit_code(result))
    return result


def _gear_build_command(arguments: argparse.Namespace) -> dict[str, object]:
    """Model the sized pair, but only once the sizing has been approved.

    The approval is read by meaning, not by matching a fixed phrase, and it
    becomes the design session's direction approval. Anything that is not a
    clear yes leaves the workspace untouched: no design job, no geometry.
    """
    decision = classify_approval(arguments.approve)
    sizing = json.loads(arguments.sizing.read_text(encoding="utf-8"))

    if decision != APPROVE:
        return {
            "schema_version": "GearBuildResult/v1",
            "status": "not_approved",
            "decision_state": decision,
            "design_id": arguments.design_id,
            "built": False,
            "next_action": (
                "revise_the_sizing"
                if decision == "REJECT"
                else "restate_the_decision"
            ),
            "message": (
                "nothing was modelled; the sizing needs a clear approval first"
            ),
        }
    if sizing.get("status") != "sized":
        raise GearSizingError(
            "sizing.not_sized",
            f"the sizing result is {sizing.get('status')!r}; only a sized "
            "drive has geometry to build",
        )

    geometry = sizing["derived"]["geometry"]
    teeth = sizing["derived"]["teeth"]
    runtime = _runtime(arguments.workspace)
    settings = runtime.design_settings()
    service = DesignSessionService(settings)

    started = service.start(
        design_id=arguments.design_id,
        title=arguments.title,
        model_classification="new_design",
        requirements=dict(sizing["given"]),
        proposal_summary=(
            f"Spur pair, module {geometry['module_mm']} mm, "
            f"{teeth['pinion']}/{teeth['gear']} teeth, "
            f"{geometry['face_width_mm']} mm face width, "
            f"{geometry['centre_distance_mm']} mm centres, sized from duty inputs"
        ),
        approval_text=arguments.approve,
        source_path=None,
    )

    design_root = Path(str(started["design_root"]))
    record = design_root / "gear_sizing.json"
    payload = json.dumps(sizing, ensure_ascii=False, sort_keys=True, indent=2)
    if record.is_file():
        atomic_replace(record, payload.encode("utf-8"))
    else:
        atomic_publish_new(record, payload.encode("utf-8"))

    with freecad_scripts_directory() as scripts:
        completed = run_freecad_script(
            settings.freecadcmd,
            scripts / "create_gear_pair.py",
            [design_root / "model.FCStd", record],
            timeout_seconds=600,
            expected_sha256=settings.freecadcmd_sha256,
            expected_identity=settings.freecadcmd_identity,
            controlled_directory=design_root,
        )
    if completed.returncode != 0:
        diagnostic = (completed.stderr + "\n" + completed.stdout)[-2000:]
        raise RuntimeError(f"FreeCAD could not build the gear pair: {diagnostic}")

    return {
        "schema_version": "GearBuildResult/v1",
        "status": "built",
        "decision_state": decision,
        "design_id": arguments.design_id,
        "built": True,
        "design_root": str(design_root),
        "model_path": str(design_root / "model.FCStd"),
        "sizing_record": str(record),
        "build_variables": {
            "module_mm": geometry["module_mm"],
            "pinion_teeth": teeth["pinion"],
            "gear_teeth": teeth["gear"],
            "face_width_mm": geometry["face_width_mm"],
            "centre_distance_mm": geometry["centre_distance_mm"],
            "bore_mm": sizing["derived"]["shaft"]["selected_diameter_mm"],
        },
        "next_action": "validate_the_model_and_record_the_result",
    }


def _gear_exit_code(result: dict[str, object]) -> int:
    status = result.get("status")
    if status == "sized":
        return 1 if result.get("warnings") else 0
    if status == "built":
        return 0
    if status == "not_approved":
        # Nothing was modelled and nothing is wrong; the design is waiting on
        # a decision, which is a warning rather than a failure.
        return 1
    return 3


def _family_service(runtime: BootstrapRuntime) -> ProductFamilyKnowledgeService:
    settings = runtime.knowledge_settings()
    return ProductFamilyKnowledgeService(settings.workspace, build_repository(settings))


def _design_command(arguments: argparse.Namespace) -> dict[str, object]:
    runtime = _runtime(arguments.workspace)
    if arguments.design_command == "start":
        requirements = _json_argument(
            arguments.requirements_json, arguments.requirements_file, "requirements"
        )
        service = _design_service(runtime, require_freecad=True)
        return service.start(
            design_id=arguments.design_id,
            title=arguments.title,
            model_classification=arguments.classification,
            requirements=requirements,
            proposal_summary=arguments.proposal,
            approval_text=arguments.approve,
            source_path=arguments.source or None,
        )
    service = _design_service(runtime, require_freecad=False)
    if arguments.design_command == "status":
        return service.get(arguments.design_id)
    if arguments.design_command == "open":
        return service.resume(arguments.design_id)
    if arguments.design_command == "mistakes":
        return service.correction_summary(arguments.design_id)
    return service.list_designs()


def _family_command(arguments: argparse.Namespace) -> dict[str, object]:
    runtime = _runtime(arguments.workspace)
    service = _family_service(runtime)
    if arguments.family_command == "start":
        return service.start(
            {
                "onboarding_id": arguments.onboarding_id,
                "family_id": arguments.family_id,
                "family_name": arguments.family_name,
                "aliases": list(arguments.alias),
            }
        )
    if arguments.family_command == "analyze":
        return service.analyze(
            arguments.onboarding_id,
            _json_argument(
                arguments.analysis_json, arguments.analysis_file, "analysis"
            ),
        )
    if arguments.family_command == "review":
        return service.review(
            arguments.onboarding_id,
            arguments.decision,
            _json_argument(arguments.notes_json, None, "review notes"),
        )
    if arguments.family_command == "publish":
        return service.publish(arguments.onboarding_id)
    return service.status(arguments.onboarding_id)


def _knowledge_command(arguments: argparse.Namespace) -> dict[str, object]:
    runtime = _runtime(arguments.workspace)
    settings = runtime.knowledge_settings()
    if arguments.knowledge_command == "bootstrap":
        return bootstrap_knowledge_database(settings)
    source_url = _database_url_from_env_file(arguments.source_env)
    scope = KnowledgeScope(settings.organization_id, settings.design_group_id)
    export = read_postgres_knowledge(source_url, scope)
    return import_into_sqlite(
        export=export,
        database_path=settings.effective_sqlite_path,
        scope=scope,
    )


def main() -> None:
    arguments = _parser().parse_args()
    try:
        result: Any
        if arguments.command == "init":
            result = initialize_workspace(
                workspace=arguments.workspace,
                actor_id=arguments.actor,
                organization_id=arguments.organization,
                design_group_id=arguments.design_group,
                dry_run=arguments.dry_run,
            ).as_dict()
        elif arguments.command == "status":
            result = _runtime(arguments.workspace).status()
        elif arguments.command == "knowledge":
            result = _knowledge_command(arguments)
        elif arguments.command == "knowledge-migrate":
            result = _knowledge_migrate(arguments)
        elif arguments.command == "migrate":
            settings = _runtime(arguments.workspace).knowledge_settings()
            result = migrate_workspace(
                workspace=settings.workspace,
                settings=settings,
                dry_run=arguments.dry_run,
            )
        elif arguments.command == "design":
            result = _design_command(arguments)
        elif arguments.command == "gear":
            result = (
                _gear_build_command(arguments)
                if arguments.gear_command == "build"
                else _gear_command(arguments)
            )
            _print(result)
            raise SystemExit(_gear_exit_code(result))
        elif arguments.command == "family":
            result = _family_command(arguments)
        elif arguments.standard_command == "providers":
            result = load_standard_part_provider_catalog().as_dict(arguments.category)
        elif arguments.standard_command == "status":
            result = _runtime(arguments.workspace).standard_part_sources_status()
        elif arguments.catalog_command == "enable":
            manifest = read_workspace_manifest(arguments.workspace)
            result = enable_standard_part_catalog(
                manifest=manifest, root_path=arguments.root
            )
        else:
            manifest = read_workspace_manifest(arguments.workspace)
            result = disable_standard_part_catalog(manifest=manifest)
        _print(result)
        raise SystemExit(_exit_code(result))
    except DiagnosticGateError as exc:
        _print(exc.response)
        raise SystemExit(_exit_code(exc.response)) from None
    except BootstrapFailure as exc:
        _print(exc.as_dict())
        raise SystemExit(_exit_code(exc.as_dict())) from None
    except GearSizingError as exc:
        _print(exc.as_dict())
        raise SystemExit(3) from None
    except Exception as exc:
        value = {
            "schema_version": "MechanicalDesignCommandError/v1",
            "status": "blocked",
            "code": type(exc).__name__,
            "message": str(exc),
        }
        _print(value)
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
