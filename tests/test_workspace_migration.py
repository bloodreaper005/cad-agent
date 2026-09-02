from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from mech_cad_design_agent.bootstrap_runtime import BootstrapRuntime
from mech_cad_design_agent.config import SQLITE_BACKEND
from mech_cad_design_agent.workspace_bootstrap import (
    BootstrapFailure,
    initialize_workspace,
)
from mech_cad_design_agent.workspace_migration import (
    migrate_workspace,
    plan_workspace_migration,
)


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


def _settings(workspace: Path):
    return BootstrapRuntime.from_process(
        cwd=workspace, environ={}
    ).knowledge_settings()


def test_plan_reports_missing_directories_and_changes_nothing(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    settings = _settings(workspace)

    plan = plan_workspace_migration(workspace=workspace, settings=settings)

    assert "designs" in plan["missing_directories"]
    assert plan["knowledge_store"]["backend"] == SQLITE_BACKEND
    assert plan["knowledge_store"]["initialized"] is False
    assert not (workspace / "designs").exists()
    assert not settings.effective_sqlite_path.exists()


def test_dry_run_migration_changes_nothing(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    settings = _settings(workspace)

    result = migrate_workspace(workspace=workspace, settings=settings, dry_run=True)

    assert result["result"] == "dry_run"
    assert result["created"] == []
    assert not (workspace / "designs").exists()
    assert not settings.effective_sqlite_path.exists()


def test_migration_creates_directories_and_the_local_store(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    settings = _settings(workspace)

    result = migrate_workspace(workspace=workspace, settings=settings)

    assert result["result"] == "migrated"
    assert "designs" in result["created"]
    assert result["migrations"]["applied"] == ["001_knowledge.sql"]
    assert settings.effective_sqlite_path.is_file()
    assert (workspace / "designs").is_dir()
    assert (workspace / "knowledge" / "product-families" / "onboarding").is_dir()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    settings = _settings(workspace)

    migrate_workspace(workspace=workspace, settings=settings)
    second = migrate_workspace(workspace=workspace, settings=settings)

    assert second["created"] == []
    assert second["migrations"] == {"applied": [], "skipped": ["001_knowledge.sql"]}


def test_migration_preserves_existing_design_jobs(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    settings = _settings(workspace)
    existing = workspace / "designs" / "carrier"
    existing.mkdir(parents=True)
    (existing / "design.json").write_text("{}", encoding="utf-8")

    migrate_workspace(workspace=workspace, settings=settings)

    assert (existing / "design.json").read_text(encoding="utf-8") == "{}"


def test_migrating_an_uninitialized_workspace_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    settings = _settings(workspace)
    shutil.rmtree(workspace / "config")

    with pytest.raises(BootstrapFailure):
        migrate_workspace(workspace=workspace, settings=settings)
