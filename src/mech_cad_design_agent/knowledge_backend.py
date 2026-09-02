from __future__ import annotations

from .config import POSTGRES_BACKEND, SQLITE_BACKEND, KnowledgeSettings
from .knowledge_records import KnowledgeScope


def build_repository(settings: KnowledgeSettings) -> object:
    """Construct the durable knowledge repository the settings select."""
    scope = KnowledgeScope(settings.organization_id, settings.design_group_id)
    backend = settings.effective_backend
    if backend == SQLITE_BACKEND:
        from .sqlite_knowledge_repository import SqliteKnowledgeRepository

        return SqliteKnowledgeRepository(settings.effective_sqlite_path, scope)
    from .knowledge_repository import KnowledgeRepository

    return KnowledgeRepository(settings.database_url, scope)


def describe_backend(settings: KnowledgeSettings) -> dict[str, object]:
    """Summarize the selected backend without exposing credentials."""
    backend = settings.effective_backend
    if backend == SQLITE_BACKEND:
        path = settings.effective_sqlite_path
        return {
            "backend": SQLITE_BACKEND,
            "database_path": str(path),
            "initialized": path.is_file(),
            "requires_service": False,
        }
    return {
        "backend": POSTGRES_BACKEND,
        "database_path": None,
        "initialized": None,
        "requires_service": True,
    }


__all__ = ["build_repository", "describe_backend"]
