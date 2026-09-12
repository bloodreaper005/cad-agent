from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import sys
import zipfile

import pytest

from mech_cad_design_agent import cli
from mech_cad_design_agent.bootstrap_runtime import BootstrapRuntime
from mech_cad_design_agent.config import DesignSettings
from mech_cad_design_agent.design_session import DesignSessionService
from mech_cad_design_agent.secure_fs import FileIdentity
from mech_cad_design_agent.workspace_bootstrap import initialize_workspace


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
) -> tuple[int, dict[str, object]]:
    monkeypatch.setattr(sys, "argv", ["mech-cad-design", *arguments])
    with pytest.raises(SystemExit) as captured:
        cli.main()
    return int(captured.value.code), json.loads(capsys.readouterr().out)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    initialize_workspace(
        workspace=workspace,
        actor_id="agent",
        organization_id="org-1",
        design_group_id="group-1",
        dry_run=False,
    )
    return workspace


def _empty_fcstd() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "Document.xml",
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<Document SchemaVersion="4"><ObjectData/></Document>',
        )
    return output.getvalue()


def _seed_design(workspace: Path, design_id: str, title: str) -> None:
    settings = DesignSettings(
        workspace=workspace,
        package_root=workspace,
        design_root=workspace / "designs",
        freecadcmd=workspace / "FreeCADCmd",
        freecadcmd_sha256="a" * 64,
        freecadcmd_identity=FileIdentity(1, 2),
        freecadcmd_version="1.1.3",
    )
    service = DesignSessionService(
        settings, seed_creator=lambda path: path.write_bytes(_empty_fcstd())
    )
    service.start(
        design_id=design_id,
        title=title,
        model_classification="new_design",
        requirements={"capacity": 4},
        proposal_summary="Printed part",
        approval_text="yes",
    )


def _bootstrap_knowledge(workspace: Path) -> None:
    settings = BootstrapRuntime.from_process(
        cwd=workspace, environ={}
    ).knowledge_settings()
    from mech_cad_design_agent.knowledge_backend import build_repository

    build_repository(settings).apply_migrations()


def test_design_list_reports_every_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    _seed_design(workspace, "carrier", "Ball Carrier")
    _seed_design(workspace, "bracket", "Wall Bracket")

    code, result = _run(
        monkeypatch, capsys, "design", "list", "--workspace", str(workspace)
    )

    assert code == 0
    assert result["count"] == 2
    assert [row["design_id"] for row in result["designs"]] == ["bracket", "carrier"]
    assert result["designs"][0]["title"] == "Wall Bracket"


def test_design_list_is_empty_for_a_fresh_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)

    code, result = _run(
        monkeypatch, capsys, "design", "list", "--workspace", str(workspace)
    )

    assert code == 0
    assert result == {
        "schema_version": "DesignList/v1",
        "status": "ok",
        "count": 0,
        "designs": [],
        "unreadable": [],
    }


def test_design_open_reports_the_next_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    _seed_design(workspace, "carrier", "Ball Carrier")

    code, result = _run(
        monkeypatch,
        capsys,
        "design",
        "open",
        "--workspace",
        str(workspace),
        "--design-id",
        "carrier",
    )

    assert code == 0
    assert result["resumed"] is True
    assert result["next_action"] == "retrieve_knowledge"
    assert result["design_id"] == "carrier"
    assert result["model_path"].endswith("designs/carrier/model.FCStd")


def test_design_open_names_the_known_jobs_when_one_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    _seed_design(workspace, "carrier", "Ball Carrier")

    code, result = _run(
        monkeypatch,
        capsys,
        "design",
        "open",
        "--workspace",
        str(workspace),
        "--design-id",
        "absent",
    )

    assert code == 3
    assert "unknown design job: absent" in result["message"]
    assert "carrier" in result["message"]


def test_design_status_returns_the_stored_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    _seed_design(workspace, "carrier", "Ball Carrier")

    code, result = _run(
        monkeypatch,
        capsys,
        "design",
        "status",
        "--workspace",
        str(workspace),
        "--design-id",
        "carrier",
    )

    assert code == 0
    assert result["design_id"] == "carrier"
    assert result["model_status"] == "approved"
    assert result["direction_approval"]["state"] == "APPROVE"


def test_design_start_reports_actionable_setup_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)

    code, result = _run(
        monkeypatch,
        capsys,
        "design",
        "start",
        "--workspace",
        str(workspace),
        "--design-id",
        "carrier",
        "--title",
        "Ball Carrier",
        "--requirements-json",
        '{"capacity": 4}',
        "--proposal",
        "Printed carrier",
        "--approve",
        "yes",
    )

    assert code == 2
    assert result["code"] == "FREECADCMD_NOT_CONFIGURED"
    assert result["capability"] == "design"
    names = [item["name"] for item in result["diagnostics"]["components"]]
    assert names == [
        "workspace",
        "freecadcmd",
        "freecad_gui_bridge",
        "knowledge",
    ]


def test_design_start_rejects_invalid_requirements_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)

    code, result = _run(
        monkeypatch,
        capsys,
        "design",
        "start",
        "--workspace",
        str(workspace),
        "--design-id",
        "carrier",
        "--title",
        "Ball Carrier",
        "--requirements-json",
        "[1, 2]",
        "--proposal",
        "Printed carrier",
        "--approve",
        "yes",
    )

    assert code == 3
    assert "requirements must be a JSON object" in result["message"]


def test_product_family_onboarding_publishes_to_the_local_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    _bootstrap_knowledge(workspace)
    common = ["--workspace", str(workspace), "--onboarding-id", "ob-1"]

    started_code, started = _run(
        monkeypatch,
        capsys,
        "family",
        "start",
        *common,
        "--family-id",
        "ball-carrier",
        "--family-name",
        "Basketball Cradle",
        "--alias",
        "ball carrier",
    )
    analyzed_code, analyzed = _run(
        monkeypatch,
        capsys,
        "family",
        "analyze",
        *common,
        "--analysis-json",
        json.dumps(
            {
                "retrieval_terms": ["cradle"],
                "assertions": [
                    {
                        "subject": "wall",
                        "predicate": "min_thickness_mm",
                        "object": 2.4,
                        "search_terms": ["wall thickness"],
                    }
                ],
            }
        ),
    )
    reviewed_code, reviewed = _run(
        monkeypatch, capsys, "family", "review", *common, "--decision", "approved"
    )
    published_code, published = _run(
        monkeypatch, capsys, "family", "publish", *common
    )

    assert [started_code, analyzed_code, reviewed_code, published_code] == [0, 0, 0, 0]
    assert started["status"] == "started"
    assert analyzed["status"] == "analyzed"
    assert reviewed["status"] == "approved"
    assert published["family_id"] == "ball-carrier"
    assert published["resumed"] is False
    assert len(published["assertion_ids"]) == 1


def test_publishing_before_approval_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    _bootstrap_knowledge(workspace)
    common = ["--workspace", str(workspace), "--onboarding-id", "ob-1"]
    _run(
        monkeypatch,
        capsys,
        "family",
        "start",
        *common,
        "--family-id",
        "ball-carrier",
        "--family-name",
        "Basketball Cradle",
    )

    code, result = _run(monkeypatch, capsys, "family", "publish", *common)

    assert code == 3
    assert "requires approval" in result["message"]


def test_family_status_reads_onboarding_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    _bootstrap_knowledge(workspace)
    common = ["--workspace", str(workspace), "--onboarding-id", "ob-1"]
    _run(
        monkeypatch,
        capsys,
        "family",
        "start",
        *common,
        "--family-id",
        "ball-carrier",
        "--family-name",
        "Basketball Cradle",
    )

    code, result = _run(monkeypatch, capsys, "family", "status", *common)

    assert code == 0
    assert result["onboarding_id"] == "ob-1"
    assert result["family_id"] == "ball-carrier"


def test_migrate_creates_the_local_store_and_reports_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)

    dry_code, dry = _run(
        monkeypatch, capsys, "migrate", "--workspace", str(workspace), "--dry-run"
    )
    code, result = _run(monkeypatch, capsys, "migrate", "--workspace", str(workspace))

    assert dry_code == 0
    assert dry["result"] == "dry_run"
    assert code == 0
    assert result["result"] == "migrated"
    assert (workspace / "data" / "knowledge.sqlite3").is_file()


def test_knowledge_bootstrap_reports_the_local_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)

    code, result = _run(
        monkeypatch, capsys, "knowledge", "bootstrap", "--workspace", str(workspace)
    )

    assert code == 0
    assert result["backend"] == "sqlite"
    assert result["knowledge_store"]["applied"] == ["001_knowledge.sql"]
    assert result["neo4j"] == {"status": "not_configured"}
    assert "postgresql" not in result
