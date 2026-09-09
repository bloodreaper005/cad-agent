from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest

from mech_cad_design_agent.config import DesignSettings
from mech_cad_design_agent.design_lesson_workflow import DesignLessonWorkflow
from mech_cad_design_agent.design_session import DesignSessionService
from mech_cad_design_agent.hashing import file_sha256
from mech_cad_design_agent.knowledge_records import build_lesson_rows
from mech_cad_design_agent.mistake_learning import (
    AGENT_ORIGIN,
    CORRECTION_ORIGIN,
)
from mech_cad_design_agent.secure_fs import FileIdentity


def _fcstd(object_name: str | None = None) -> bytes:
    object_xml = (
        f'<Object type="Part::Feature" name="{object_name}"/>' if object_name else ""
    )
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "Document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Document SchemaVersion="4">'
            f"<ObjectData>{object_xml}</ObjectData></Document>",
        )
    return output.getvalue()


def _service(tmp_path: Path) -> DesignSessionService:
    settings = DesignSettings(
        workspace=tmp_path,
        package_root=tmp_path,
        design_root=tmp_path / "designs",
        freecadcmd=tmp_path / "FreeCADCmd",
        freecadcmd_sha256="a" * 64,
        freecadcmd_identity=FileIdentity(1, 2),
        freecadcmd_version="1.1.3",
    )

    def seed(destination: Path) -> None:
        destination.write_bytes(_fcstd())

    service = DesignSessionService(settings, seed_creator=seed)
    service.start(
        design_id="carrier",
        title="Basketball Carrier",
        model_classification="new_design",
        requirements={"capacity": 4},
        proposal_summary="A PLA carrier",
        approval_text="yes",
    )
    return service


def _attempt(
    service: DesignSessionService,
    tmp_path: Path,
    *,
    object_name: str,
    failed_checks: tuple[tuple[str, str, bool], ...] = (),
) -> dict[str, object]:
    """Record one validation attempt whose report matches the exact model bytes."""
    root = tmp_path / "designs" / "carrier"
    model = root / "model.FCStd"
    model.write_bytes(_fcstd(object_name))
    model_sha256 = file_sha256(model)
    checks: list[dict[str, object]] = [
        {
            "id": "shape-validity",
            "validator": "freecad-model-validation",
            "status": "passed",
            "message": "valid",
            "mandatory": True,
        }
    ]
    for check_id, validator, mandatory in failed_checks:
        checks.append(
            {
                "id": check_id,
                "validator": validator,
                "status": "failed",
                "message": f"{check_id} did not hold",
                "mandatory": mandatory,
            }
        )
    passed = not any(mandatory for _, _, mandatory in failed_checks)
    report = root / "validation" / "model_validation.json"
    report.write_text(
        json.dumps(
            {
                "status": "passed" if passed else "failed",
                "working_sha256": model_sha256,
                "checks": checks,
                "fastener_inventory": [],
                "summary": {
                    "passed": 1,
                    "failed": len(failed_checks),
                    "warnings": 0,
                    "fasteners_detected": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    markdown = root / "validation" / "model_validation.md"
    image = root / "validation" / "model_validation.png"
    markdown.write_text("# report\n", encoding="utf-8")
    image.write_bytes(b"visual evidence")
    return service.record_result(
        design_id="carrier",
        model_path=str(model),
        validation_report_path=str(report),
        evidence_paths=[str(markdown), str(image)],
    )


def _corrected(service: DesignSessionService, tmp_path: Path) -> None:
    """Fail one mandatory check twice, then pass, as a real correction loop does."""
    _attempt(
        service,
        tmp_path,
        object_name="Draft",
        failed_checks=(("solid-volume", "freecad-model-validation", True),),
    )
    _attempt(
        service,
        tmp_path,
        object_name="DraftTwo",
        failed_checks=(("solid-volume", "freecad-model-validation", True),),
    )
    _attempt(service, tmp_path, object_name="Carrier")


def test_every_validation_attempt_is_appended_to_the_ledger(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _corrected(service, tmp_path)
    ledger = service.get("carrier")["correction_ledger"]
    assert [entry["attempt"] for entry in ledger] == [1, 2, 3]
    assert [entry["validation_status"] for entry in ledger] == [
        "failed",
        "failed",
        "passed",
    ]
    assert ledger[0]["failures"][0]["check_id"] == "solid-volume"
    assert ledger[2]["failures"] == []
    assert len({entry["model_sha256"] for entry in ledger}) == 3


def test_a_failed_attempt_survives_the_next_recorded_result(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _corrected(service, tmp_path)
    state = service.get("carrier")
    assert state["validation"]["status"] == "passed"
    assert state["model_status"] == "completed"
    assert state["correction_ledger"][0]["warning"] == "mandatory validation check failed"


def test_correction_summary_reports_the_corrected_defect(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _corrected(service, tmp_path)
    summary = service.correction_summary("carrier")
    assert summary["schema_version"] == "DesignMistakeSummary/v1"
    assert summary["design_id"] == "carrier"
    assert summary["attempts"] == 3
    assert summary["failed_attempts"] == 2
    assert summary["outstanding"] == []
    assert len(summary["corrected"]) == 1
    assert summary["corrected"][0]["occurrences"] == 2
    assert summary["resolved_by_model_sha256"] == service.get("carrier")["model"][
        "sha256"
    ]


def test_correction_summary_reports_an_outstanding_defect(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _attempt(
        service,
        tmp_path,
        object_name="Draft",
        failed_checks=(("solid-volume", "freecad-model-validation", True),),
    )
    summary = service.correction_summary("carrier")
    assert summary["corrected"] == []
    assert [record["check_id"] for record in summary["outstanding"]] == ["solid-volume"]


def test_confirmation_learns_a_lesson_from_a_corrected_defect(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workflow = DesignLessonWorkflow(service)
    _corrected(service, tmp_path)
    result = workflow.confirm(
        design_id="carrier", confirmation_text="yes", candidates=[]
    )
    assert result["lesson_review_status"] == "review_pending"
    lessons = result["review_card"]["lessons"]
    assert len(lessons) == 1
    lesson = lessons[0]
    assert lesson["origin"] == CORRECTION_ORIGIN
    assert lesson["correction_signature"] == "freecad-model-validation::solid-volume"
    assert lesson["correction_occurrences"] == 2
    assert "solid-volume" in lesson["problem"]
    assert "validation/model_validation.json" in lesson["evidence"]


def test_a_clean_design_still_reports_no_material_lessons(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workflow = DesignLessonWorkflow(service)
    _attempt(service, tmp_path, object_name="Carrier")
    result = workflow.confirm(
        design_id="carrier", confirmation_text="yes", candidates=[]
    )
    assert result["lesson_review_status"] == "no_material_lessons"


def test_agent_and_derived_lessons_share_one_review_card(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workflow = DesignLessonWorkflow(service)
    _corrected(service, tmp_path)
    candidate = {
        "problem": "A long upright handle concentrates bending at its base.",
        "decision": "Use mirrored handle roots with generous radii.",
        "evidence": ["validation/model_validation.json"],
        "applicability": "Printed carriers with a central upright handle.",
        "prevention_action": "Validate root thickness on both load paths.",
        "search_terms": ["printed carrier handle root"],
    }
    result = workflow.confirm(
        design_id="carrier", confirmation_text="yes", candidates=[candidate]
    )
    lessons = result["review_card"]["lessons"]
    assert [lesson["origin"] for lesson in lessons] == [AGENT_ORIGIN, CORRECTION_ORIGIN]
    assert lessons[0]["problem"] == candidate["problem"]
    assert "correction_signature" not in lessons[0]


def test_derived_lessons_are_stable_across_repeated_confirmation(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    workflow = DesignLessonWorkflow(service)
    _corrected(service, tmp_path)
    first = workflow.confirm(
        design_id="carrier", confirmation_text="yes", candidates=[]
    )
    second = workflow.confirm(
        design_id="carrier", confirmation_text="yes", candidates=[]
    )
    assert first["review_sha256"] == second["review_sha256"]
    assert first["review_card"] == second["review_card"]


def test_a_derived_lesson_publishes_with_its_origin_intact(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workflow = DesignLessonWorkflow(service)
    _corrected(service, tmp_path)
    result = workflow.confirm(
        design_id="carrier", confirmation_text="yes", candidates=[]
    )
    rows = build_lesson_rows(
        review_card=result["review_card"],
        review_sha256=str(result["review_sha256"]),
        decision_text="approved",
    )
    assert len(rows) == 1
    content = rows[0]["content"]
    assert content["origin"] == CORRECTION_ORIGIN
    assert content["correction_signature"] == "freecad-model-validation::solid-volume"
    assert "solid-volume" in rows[0]["search_text"]


def test_an_advisory_failure_alone_creates_no_lesson(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workflow = DesignLessonWorkflow(service)
    _attempt(
        service,
        tmp_path,
        object_name="Draft",
        failed_checks=(("edge-radius", "freecad-model-validation", False),),
    )
    _attempt(service, tmp_path, object_name="Carrier")
    result = workflow.confirm(
        design_id="carrier", confirmation_text="yes", candidates=[]
    )
    assert result["lesson_review_status"] == "no_material_lessons"


def test_a_session_written_before_the_ledger_existed_still_loads(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _attempt(service, tmp_path, object_name="Carrier")
    state_path = tmp_path / "designs" / "carrier" / "design.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    del state["correction_ledger"]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    assert service.get("carrier")["correction_ledger"] == []
    summary = service.correction_summary("carrier")
    assert summary["attempts"] == 0
    assert summary["corrected"] == []
    workflow = DesignLessonWorkflow(service)
    result = workflow.confirm(
        design_id="carrier", confirmation_text="yes", candidates=[]
    )
    assert result["lesson_review_status"] == "no_material_lessons"


def test_a_corrupt_ledger_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _attempt(service, tmp_path, object_name="Carrier")
    state_path = tmp_path / "designs" / "carrier" / "design.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["correction_ledger"] = "not-a-ledger"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="correction_ledger"):
        service.get("carrier")


def _repository(tmp_path: Path):
    from mech_cad_design_agent.knowledge_records import KnowledgeScope
    from mech_cad_design_agent.migrations import sqlite_migrations_directory
    from mech_cad_design_agent.sqlite_knowledge_repository import (
        SqliteKnowledgeRepository,
    )

    repository = SqliteKnowledgeRepository(
        tmp_path / "knowledge.sqlite3", KnowledgeScope("org", "grp")
    )
    with sqlite_migrations_directory() as migrations:
        repository.apply_migrations(migrations)
    return repository


def test_a_corrected_defect_is_retrievable_by_a_later_design(tmp_path: Path) -> None:
    """The full loop: a mistake is made, corrected, learned, and found again."""
    service = _service(tmp_path)
    workflow = DesignLessonWorkflow(service)
    repository = _repository(tmp_path)
    _corrected(service, tmp_path)

    confirmed = workflow.confirm(
        design_id="carrier", confirmation_text="yes", candidates=[]
    )
    decided = workflow.decide(
        design_id="carrier",
        decision_text="approved",
        publisher=repository,
    )
    assert decided["status"] == "published"

    found = repository.search(query="solid-volume")["lessons"]
    assert len(found) == 1
    content = found[0]["content"]
    assert content["origin"] == CORRECTION_ORIGIN
    assert content["correction_signature"] == "freecad-model-validation::solid-volume"
    assert "solid-volume" in content["prevention_action"]
    assert found[0]["provenance"]["source_review_sha256"] == confirmed["review_sha256"]


def test_publishing_the_same_correction_twice_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workflow = DesignLessonWorkflow(service)
    repository = _repository(tmp_path)
    _corrected(service, tmp_path)
    workflow.confirm(design_id="carrier", confirmation_text="yes", candidates=[])
    first = workflow.decide(
        design_id="carrier", decision_text="approved", publisher=repository
    )
    second = workflow.decide(
        design_id="carrier", decision_text="approved", publisher=repository
    )
    assert first["status"] == second["status"] == "published"
    assert len(repository.search(query="solid-volume")["lessons"]) == 1
