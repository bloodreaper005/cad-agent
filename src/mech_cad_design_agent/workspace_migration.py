from __future__ import annotations

from pathlib import Path

from .config import (
    POSTGRES_BACKEND,
    SQLITE_BACKEND,
    KnowledgeSettings,
)
from .knowledge_records import KnowledgeScope
from .migrations import sqlite_migrations_directory
from .sqlite_knowledge_repository import SqliteKnowledgeRepository
from .workspace_bootstrap import read_workspace_manifest


_MANAGED_DIRECTORIES = (
    ("config",),
    ("config", "product_families"),
    ("data",),
    ("data", "artifacts"),
    ("designs",),
    ("knowledge", "product-families", "onboarding"),
)


def plan_workspace_migration(
    *, workspace: Path, settings: KnowledgeSettings
) -> dict[str, object]:
    """Report what an existing workspace still needs, changing nothing."""
    root = Path(workspace)
    missing_directories = [
        str(Path(*parts))
        for parts in _MANAGED_DIRECTORIES
        if not root.joinpath(*parts).is_dir()
    ]
    backend = settings.effective_backend
    actions: list[str] = []
    for relative in missing_directories:
        actions.append(f"create directory {relative}")
    knowledge_store: dict[str, object]
    if backend == SQLITE_BACKEND:
        sqlite_path = settings.effective_sqlite_path
        initialized = sqlite_path.is_file()
        knowledge_store = {
            "backend": SQLITE_BACKEND,
            "database_path": str(sqlite_path),
            "initialized": initialized,
        }
        actions.append(
            "apply knowledge schema migrations"
            if initialized
            else f"create local knowledge store at {sqlite_path}"
        )
    else:
        knowledge_store = {
            "backend": POSTGRES_BACKEND,
            "database_path": None,
            "initialized": None,
        }
        actions.append("apply knowledge schema migrations to the configured PostgreSQL")
    return {
        "schema_version": "MechanicalDesignWorkspaceMigrationPlan/v1",
        "status": "ok",
        "workspace": str(root),
        "missing_directories": missing_directories,
        "knowledge_store": knowledge_store,
        "planned_actions": actions,
    }


def migrate_workspace(
    *, workspace: Path, settings: KnowledgeSettings, dry_run: bool = False
) -> dict[str, object]:
    """Bring an existing workspace up to the current layout and schema."""
    root = Path(workspace)
    read_workspace_manifest(root)
    plan = plan_workspace_migration(workspace=root, settings=settings)
    if dry_run:
        return {
            **plan,
            "schema_version": "MechanicalDesignWorkspaceMigration/v1",
            "result": "dry_run",
            "created": [],
            "migrations": {"applied": [], "skipped": []},
        }

    created: list[str] = []
    for parts in _MANAGED_DIRECTORIES:
        target = root.joinpath(*parts)
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            created.append(str(Path(*parts)))

    backend = settings.effective_backend
    if backend == SQLITE_BACKEND:
        repository = SqliteKnowledgeRepository(
            settings.effective_sqlite_path,
            KnowledgeScope(settings.organization_id, settings.design_group_id),
        )
        with sqlite_migrations_directory() as directory:
            migrations = repository.apply_migrations(directory)
        knowledge_store = {
            "backend": SQLITE_BACKEND,
            "database_path": str(settings.effective_sqlite_path),
            "initialized": True,
        }
    else:
        from .knowledge_repository import KnowledgeRepository
        from .migrations import postgres_migrations_directory

        repository = KnowledgeRepository(
            settings.database_url,
            KnowledgeScope(settings.organization_id, settings.design_group_id),
        )
        with postgres_migrations_directory() as directory:
            migrations = repository.apply_migrations(directory)
        knowledge_store = {
            "backend": POSTGRES_BACKEND,
            "database_path": None,
            "initialized": True,
        }

    return {
        "schema_version": "MechanicalDesignWorkspaceMigration/v1",
        "status": "ok",
        "result": "migrated",
        "workspace": str(root),
        "created": created,
        "knowledge_store": knowledge_store,
        "migrations": migrations,
        "next_steps": [
            "run 'mech-cad-design status' to confirm readiness",
        ],
    }


__all__ = ["migrate_workspace", "plan_workspace_migration"]
