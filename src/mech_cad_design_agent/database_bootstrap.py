from __future__ import annotations

from .config import SQLITE_BACKEND, KnowledgeSettings
from .knowledge_repository import KnowledgeRepository, KnowledgeScope
from .migrations import postgres_migrations_directory, sqlite_migrations_directory
from .projection import Neo4jProjection
from .sqlite_knowledge_repository import SqliteKnowledgeRepository


def bootstrap_knowledge_database(settings: KnowledgeSettings) -> dict[str, object]:
    """Initialize the selected knowledge store and optional Neo4j constraints."""
    scope = KnowledgeScope(settings.organization_id, settings.design_group_id)
    backend = settings.effective_backend
    if backend == SQLITE_BACKEND:
        sqlite_path = settings.effective_sqlite_path
        sqlite_repository = SqliteKnowledgeRepository(sqlite_path, scope)
        with sqlite_migrations_directory() as migrations:
            store = sqlite_repository.apply_migrations(migrations)
        store = {**store, "database_path": str(sqlite_path)}
    else:
        repository = KnowledgeRepository(settings.database_url, scope)
        with postgres_migrations_directory() as migrations:
            store = repository.apply_migrations(migrations)
    neo4j: dict[str, object] = {"status": "not_configured"}
    if all(
        value.strip()
        for value in (
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
        )
    ):
        projection = Neo4jProjection(
            settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password
        )
        projection_status = projection.status()
        if projection_status.get("status") == "healthy":
            projection.initialize_constraints()
            neo4j = {"constraints": "initialized"}
        else:
            neo4j = projection_status
    result: dict[str, object] = {
        "schema_version": "KnowledgeDatabaseBootstrap/v1",
        "status": "ready",
        "backend": backend,
        "knowledge_store": store,
        "neo4j": neo4j,
    }
    if backend != SQLITE_BACKEND:
        result["postgresql"] = store
    return result


__all__ = ["bootstrap_knowledge_database"]
