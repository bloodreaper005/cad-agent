from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .knowledge_records import KnowledgeScope
from .migrations import sqlite_migrations_directory
from .models import canonical_json
from .sqlite_knowledge_repository import (
    SqliteKnowledgeRepository,
    search_blob,
)


CURRENT_SCHEMA_QUERIES = {
    "product_families": (
        "SELECT id,organization_id,design_group_id,canonical_name,aliases,profile,"
        "search_terms,search_text,status FROM product_families "
        "WHERE organization_id=%s AND design_group_id=%s ORDER BY id"
    ),
    "knowledge_assertions": (
        "SELECT id,organization_id,design_group_id,product_family_id,subject,predicate,"
        "object_value,applicability,evidence,search_terms,search_text,status,"
        "supersedes_id FROM knowledge_assertions "
        "WHERE organization_id=%s AND design_group_id=%s ORDER BY id"
    ),
    "design_lessons": (
        "SELECT id,organization_id,design_group_id,product_family_id,content,"
        "applicability,provenance,search_terms,search_text,status,supersedes_id "
        "FROM design_lessons WHERE organization_id=%s AND design_group_id=%s ORDER BY id"
    ),
}

def read_postgres_knowledge(
    database_url: str,
    scope: KnowledgeScope,
    *,
    connect: Callable[..., Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Read the current knowledge tables for one scope in a single snapshot."""
    if not isinstance(database_url, str) or not database_url.strip():
        raise ValueError("database_url is required")
    if connect is None:
        import psycopg

        connect = psycopg.connect
    from psycopg.rows import dict_row

    export: dict[str, list[dict[str, Any]]] = {}
    with connect(database_url.strip(), row_factory=dict_row) as connection:
        with connection.transaction():
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            for name, query in CURRENT_SCHEMA_QUERIES.items():
                rows = connection.execute(
                    query, (scope.organization_id, scope.design_group_id)
                ).fetchall()
                export[name] = [dict(row) for row in rows]
    return export


def _encode(row: Mapping[str, Any], table: str, column: str) -> str:
    value = row[column]
    if isinstance(value, (str, bytes)):
        raise ValueError(
            f"{table}.{column} must arrive as decoded JSON, not an encoded string"
        )
    return canonical_json(value)


def import_into_sqlite(
    *,
    export: Mapping[str, list[dict[str, Any]]],
    database_path: Path,
    scope: KnowledgeScope,
) -> dict[str, object]:
    """Load an exported scope into the local SQLite knowledge store."""
    repository = SqliteKnowledgeRepository(database_path, scope)
    with sqlite_migrations_directory() as directory:
        repository.apply_migrations(directory)

    families = list(export.get("product_families", []))
    assertions = list(export.get("knowledge_assertions", []))
    lessons = list(export.get("design_lessons", []))
    for table, rows in (
        ("product_families", families),
        ("knowledge_assertions", assertions),
        ("design_lessons", lessons),
    ):
        for row in rows:
            if str(row.get("organization_id")) != scope.organization_id or str(
                row.get("design_group_id")
            ) != scope.design_group_id:
                raise ValueError(f"{table} contains a row outside the requested scope")

    imported = {"product_families": 0, "knowledge_assertions": 0, "design_lessons": 0}
    skipped = {"product_families": 0, "knowledge_assertions": 0, "design_lessons": 0}
    with repository.connection() as connection:
        with repository.transaction(connection):
            existing_families = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM product_families WHERE organization_id=? "
                    "AND design_group_id=?",
                    (scope.organization_id, scope.design_group_id),
                ).fetchall()
            }
            for row in families:
                if row["id"] in existing_families:
                    skipped["product_families"] += 1
                    continue
                connection.execute(
                    "INSERT INTO product_families"
                    "(id,organization_id,design_group_id,canonical_name,aliases,profile,"
                    "search_terms,search_text,search_blob,status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"],
                        scope.organization_id,
                        scope.design_group_id,
                        row["canonical_name"],
                        _encode(row, "product_families", "aliases"),
                        _encode(row, "product_families", "profile"),
                        _encode(row, "product_families", "search_terms"),
                        row["search_text"],
                        search_blob(str(row["search_text"])),
                        row["status"],
                    ),
                )
                imported["product_families"] += 1

            existing_assertions = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM knowledge_assertions WHERE organization_id=? "
                    "AND design_group_id=?",
                    (scope.organization_id, scope.design_group_id),
                ).fetchall()
            }
            for row in assertions:
                if row["id"] in existing_assertions:
                    skipped["knowledge_assertions"] += 1
                    continue
                connection.execute(
                    "INSERT INTO knowledge_assertions("
                    "id,organization_id,design_group_id,product_family_id,subject,"
                    "predicate,object_value,applicability,evidence,search_terms,"
                    "search_text,search_blob,status,supersedes_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"],
                        scope.organization_id,
                        scope.design_group_id,
                        row["product_family_id"],
                        row["subject"],
                        row["predicate"],
                        _encode(row, "knowledge_assertions", "object_value"),
                        _encode(row, "knowledge_assertions", "applicability"),
                        _encode(row, "knowledge_assertions", "evidence"),
                        _encode(row, "knowledge_assertions", "search_terms"),
                        row["search_text"],
                        search_blob(str(row["search_text"])),
                        row["status"],
                        row["supersedes_id"],
                    ),
                )
                imported["knowledge_assertions"] += 1

            existing_lessons = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM design_lessons WHERE organization_id=? "
                    "AND design_group_id=?",
                    (scope.organization_id, scope.design_group_id),
                ).fetchall()
            }
            for row in lessons:
                if row["id"] in existing_lessons:
                    skipped["design_lessons"] += 1
                    continue
                connection.execute(
                    "INSERT INTO design_lessons("
                    "id,organization_id,design_group_id,product_family_id,content,"
                    "applicability,provenance,search_terms,search_text,search_blob,"
                    "status,supersedes_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"],
                        scope.organization_id,
                        scope.design_group_id,
                        row["product_family_id"],
                        _encode(row, "design_lessons", "content"),
                        _encode(row, "design_lessons", "applicability"),
                        _encode(row, "design_lessons", "provenance"),
                        _encode(row, "design_lessons", "search_terms"),
                        row["search_text"],
                        search_blob(str(row["search_text"])),
                        row["status"],
                        row["supersedes_id"],
                    ),
                )
                imported["design_lessons"] += 1

    return {
        "schema_version": "KnowledgeImportResult/v1",
        "status": "ok",
        "database_path": str(Path(database_path)),
        "organization_id": scope.organization_id,
        "design_group_id": scope.design_group_id,
        "imported": imported,
        "skipped_existing": skipped,
    }


__all__ = [
    "CURRENT_SCHEMA_QUERIES",
    "import_into_sqlite",
    "read_postgres_knowledge",
]
