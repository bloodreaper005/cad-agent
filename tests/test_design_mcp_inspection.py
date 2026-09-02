from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import zipfile

from mech_cad_design_agent.bootstrap_runtime import BootstrapRuntime
from mech_cad_design_agent.config import DesignSettings
from mech_cad_design_agent.design_session import DesignSessionService
from mech_cad_design_agent.secure_fs import FileIdentity
from mech_cad_design_agent.server import create_mcp
from mech_cad_design_agent.workspace_bootstrap import initialize_workspace


def _tool(server: object, name: str):
    return server._tool_manager._tools[name].fn


def _empty_fcstd() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "Document.xml",
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<Document SchemaVersion="4"><ObjectData/></Document>',
        )
    return output.getvalue()


def _workspace_with_design(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    initialize_workspace(
        workspace=workspace,
        actor_id="agent",
        organization_id="org-1",
        design_group_id="group-1",
        dry_run=False,
    )
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
        design_id="carrier",
        title="Ball Carrier",
        model_classification="new_design",
        requirements={"capacity": 4},
        proposal_summary="Printed carrier",
        approval_text="yes",
    )
    return workspace


def test_listing_designs_needs_no_configured_freecad(tmp_path: Path) -> None:
    workspace = _workspace_with_design(tmp_path)
    server = create_mcp(
        runtime=BootstrapRuntime.from_process(cwd=workspace, environ={})
    )

    listed = json.loads(_tool(server, "design_list")())

    assert listed["schema_version"] == "DesignList/v1"
    assert [row["design_id"] for row in listed["designs"]] == ["carrier"]
    assert listed["designs"][0]["title"] == "Ball Carrier"


def test_reading_design_status_needs_no_configured_freecad(tmp_path: Path) -> None:
    workspace = _workspace_with_design(tmp_path)
    server = create_mcp(
        runtime=BootstrapRuntime.from_process(cwd=workspace, environ={})
    )

    state = json.loads(_tool(server, "design_status")("carrier"))

    assert state["design_id"] == "carrier"
    assert state["model_status"] == "approved"


def test_starting_a_design_still_requires_freecad(tmp_path: Path) -> None:
    workspace = _workspace_with_design(tmp_path)
    server = create_mcp(
        runtime=BootstrapRuntime.from_process(cwd=workspace, environ={})
    )

    try:
        _tool(server, "design_start")(
            design_id="bracket",
            title="Wall Bracket",
            model_classification="new_design",
            requirements_json="{}",
            proposal_summary="Printed bracket",
            approval_text="yes",
        )
    except Exception as exc:  # ToolError carries the setup diagnostics as JSON
        response = json.loads(str(exc))
        assert response["code"] == "FREECADCMD_NOT_CONFIGURED"
        assert response["capability"] == "design"
    else:
        raise AssertionError("design_start ran without a configured FreeCADCmd")


def test_system_status_names_the_local_knowledge_store(tmp_path: Path) -> None:
    workspace = _workspace_with_design(tmp_path)
    server = create_mcp(
        runtime=BootstrapRuntime.from_process(cwd=workspace, environ={})
    )

    status = json.loads(_tool(server, "design_system_status")())

    knowledge = next(
        item for item in status["components"] if item["name"] == "knowledge"
    )
    assert knowledge["code"] == "KNOWLEDGE_SQLITE_NOT_INITIALIZED"
    assert str(workspace) in knowledge["message"]
