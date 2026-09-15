from __future__ import annotations

from pathlib import Path

import pytest

from mech_cad_design_agent.bootstrap_runtime import BootstrapRuntime
from mech_cad_design_agent.config import (
    POSTGRES_BACKEND,
    SQLITE_BACKEND,
    KnowledgeSettings,
    default_sqlite_path,
    is_postgres_url,
    normalize_backend,
)
from mech_cad_design_agent.knowledge_backend import build_repository, describe_backend
from mech_cad_design_agent.knowledge_repository import KnowledgeRepository
from mech_cad_design_agent.sqlite_knowledge_repository import (
    SqliteKnowledgeRepository,
)
from mech_cad_design_agent.workspace_bootstrap import initialize_workspace


def _settings(tmp_path: Path, **overrides: object) -> KnowledgeSettings:
    values: dict[str, object] = {
        "workspace": tmp_path,
        "database_url": "",
        "neo4j_uri": "",
        "neo4j_user": "",
        "neo4j_password": "",
        "organization_id": "org-1",
        "design_group_id": "group-1",
    }
    values.update(overrides)
    return KnowledgeSettings(**values)  # type: ignore[arg-type]


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


def test_postgres_urls_are_recognized() -> None:
    assert is_postgres_url("postgresql://host/db")
    assert is_postgres_url("postgres://host/db")
    assert not is_postgres_url("")
    assert not is_postgres_url("/var/lib/knowledge.sqlite3")


def test_unknown_backend_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        normalize_backend("mysql")


def test_settings_default_to_sqlite_without_a_database_url(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert settings.effective_backend == SQLITE_BACKEND
    assert settings.effective_sqlite_path == default_sqlite_path(tmp_path)


def test_settings_infer_postgres_from_an_explicit_url(tmp_path: Path) -> None:
    settings = _settings(tmp_path, database_url="postgresql://host/db")

    assert settings.effective_backend == POSTGRES_BACKEND


def test_explicit_backend_overrides_the_inferred_one(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, database_url="postgresql://host/db", backend=SQLITE_BACKEND
    )

    assert settings.effective_backend == SQLITE_BACKEND


def test_build_repository_selects_the_matching_implementation(tmp_path: Path) -> None:
    sqlite_repository = build_repository(_settings(tmp_path))
    postgres_repository = build_repository(
        _settings(tmp_path, database_url="postgresql://host/db")
    )

    assert isinstance(sqlite_repository, SqliteKnowledgeRepository)
    assert isinstance(postgres_repository, KnowledgeRepository)


def test_describe_backend_reports_the_local_store_without_credentials(
    tmp_path: Path,
) -> None:
    described = describe_backend(_settings(tmp_path))
    postgres = describe_backend(
        _settings(tmp_path, database_url="postgresql://user:secret@host/db")
    )

    assert described["backend"] == SQLITE_BACKEND
    assert described["initialized"] is False
    assert described["requires_service"] is False
    assert postgres["requires_service"] is True
    assert "secret" not in str(postgres)


def test_runtime_defaults_to_a_workspace_local_sqlite_store(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = BootstrapRuntime.from_process(cwd=workspace, environ={})

    settings = runtime.knowledge_settings()

    assert settings.effective_backend == SQLITE_BACKEND
    # Compared against the workspace the runtime resolved, not the one handed in.
    # On Windows the runtime canonicalises to the extended-length \\?\C:\... form
    # for long-path support, so a hand-built path names the same file and is not
    # an equal Path.
    assert (
        settings.effective_sqlite_path
        == settings.workspace / "data" / "knowledge.sqlite3"
    )


def test_runtime_honors_an_explicit_sqlite_path(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = BootstrapRuntime.from_process(
        cwd=workspace,
        environ={"MECH_DESIGN_SQLITE_PATH": "data/custom.sqlite3"},
    )

    settings = runtime.knowledge_settings()
    assert (
        settings.effective_sqlite_path
        == settings.workspace / "data" / "custom.sqlite3"
    )


def test_runtime_uses_postgres_when_a_database_url_is_configured(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = BootstrapRuntime.from_process(
        cwd=workspace,
        environ={"MECH_DESIGN_DATABASE_URL": "postgresql://host/db"},
    )

    assert runtime.knowledge_settings().effective_backend == POSTGRES_BACKEND


def test_requesting_postgres_without_a_url_is_reported(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = BootstrapRuntime.from_process(
        cwd=workspace, environ={"MECH_DESIGN_KNOWLEDGE_BACKEND": "postgres"}
    )

    with pytest.raises(RuntimeError, match="requires MECH_DESIGN_DATABASE_URL"):
        runtime.knowledge_settings()

    knowledge = next(
        item for item in runtime.status()["components"] if item["name"] == "knowledge"
    )
    assert knowledge["status"] == "setup_required"
    assert knowledge["code"] == "KNOWLEDGE_BACKEND_INVALID"


def test_status_reports_the_local_store_once_it_exists(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = BootstrapRuntime.from_process(cwd=workspace, environ={})
    settings = runtime.knowledge_settings()

    before = next(
        item for item in runtime.status()["components"] if item["name"] == "knowledge"
    )
    build_repository(settings).apply_migrations()
    after = next(
        item for item in runtime.status()["components"] if item["name"] == "knowledge"
    )

    assert before["code"] == "KNOWLEDGE_SQLITE_NOT_INITIALIZED"
    assert before["status"] == "warning"
    assert after["code"] == "KNOWLEDGE_SQLITE_READY"
    assert after["status"] == "ok"
