from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mech_cad_design_agent.knowledge_import import (
    CURRENT_SCHEMA_QUERIES,
    import_into_sqlite,
    read_postgres_knowledge,
)
from mech_cad_design_agent.knowledge_records import KnowledgeScope
from mech_cad_design_agent.sqlite_knowledge_repository import (
    SqliteKnowledgeRepository,
)


SCOPE = KnowledgeScope("org-1", "group-1")


def _family_row() -> dict[str, Any]:
    return {
        "id": "ball-carrier",
        "organization_id": "org-1",
        "design_group_id": "group-1",
        "canonical_name": "Basketball Cradle",
        "aliases": ["ball carrier"],
        "profile": {"retrieval_terms": ["cradle"]},
        "search_terms": ["ball carrier", "basketball cradle", "cradle"],
        "search_text": "Basketball Cradle ball carrier cradle",
        "status": "active",
    }


def _assertion_row() -> dict[str, Any]:
    return {
        "id": "ball-carrier-assertion-1",
        "organization_id": "org-1",
        "design_group_id": "group-1",
        "product_family_id": "ball-carrier",
        "subject": "wall",
        "predicate": "min_thickness_mm",
        "object_value": 2.4,
        "applicability": {"conditions": {"material": "PLA"}},
        "evidence": [],
        "search_terms": ["wall thickness"],
        "search_text": "wall min_thickness_mm 2.4 wall thickness",
        "status": "active",
        "supersedes_id": None,
    }


def _lesson_row() -> dict[str, Any]:
    return {
        "id": "lesson-1",
        "organization_id": "org-1",
        "design_group_id": "group-1",
        "product_family_id": "ball-carrier",
        "content": {"title": "Chamfer the rim"},
        "applicability": {"conditions": {"material": "PLA"}},
        "provenance": {"source_review_sha256": "a" * 64},
        "search_terms": ["chamfer"],
        "search_text": "Chamfer the rim chamfer",
        "status": "active",
        "supersedes_id": None,
    }


def _export() -> dict[str, list[dict[str, Any]]]:
    return {
        "product_families": [_family_row()],
        "knowledge_assertions": [_assertion_row()],
        "design_lessons": [_lesson_row()],
    }


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Transaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exception: object) -> bool:
        return False


class _Connection:
    def __init__(self, export: dict[str, list[dict[str, Any]]]) -> None:
        self.export = export
        self.statements: list[str] = []
        self.parameters: list[tuple[object, ...]] = []

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_exception: object) -> bool:
        return False

    def transaction(self) -> _Transaction:
        return _Transaction()

    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> _Result:
        self.statements.append(query)
        if parameters:
            self.parameters.append(parameters)
        for name, expected in CURRENT_SCHEMA_QUERIES.items():
            if query == expected:
                return _Result(self.export[name])
        return _Result([])


def test_read_postgres_knowledge_reads_one_scoped_snapshot() -> None:
    connection = _Connection(_export())

    export = read_postgres_knowledge(
        "postgresql://host/db", SCOPE, connect=lambda *_a, **_k: connection
    )

    assert set(export) == set(CURRENT_SCHEMA_QUERIES)
    assert export["product_families"] == [_family_row()]
    assert all(
        parameters == ("org-1", "group-1") for parameters in connection.parameters
    )
    assert any("REPEATABLE READ READ ONLY" in item for item in connection.statements)


def test_read_postgres_knowledge_requires_a_url() -> None:
    with pytest.raises(ValueError, match="database_url is required"):
        read_postgres_knowledge("  ", SCOPE, connect=lambda *_a, **_k: None)


def test_import_loads_every_collection_into_the_local_store(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.sqlite3"

    result = import_into_sqlite(
        export=_export(), database_path=database, scope=SCOPE
    )

    assert result["imported"] == {
        "product_families": 1,
        "knowledge_assertions": 1,
        "design_lessons": 1,
    }
    repository = SqliteKnowledgeRepository(database, SCOPE)
    found = repository.search(query="basketball cradle")
    assert [row["id"] for row in found["families"]] == ["ball-carrier"]
    assert repository.get_design_lesson("lesson-1")["content"] == {
        "title": "Chamfer the rim"
    }


def test_imported_rows_keep_native_json_types(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.sqlite3"
    import_into_sqlite(export=_export(), database_path=database, scope=SCOPE)
    repository = SqliteKnowledgeRepository(database, SCOPE)

    match = repository.match_product_family(
        query="", design_features={}, requested_family_id="ball-carrier"
    )

    assert match is not None
    assert match["aliases"] == ["ball carrier"]
    assert match["profile"] == {"retrieval_terms": ["cradle"]}


def test_reimporting_skips_rows_that_already_exist(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.sqlite3"
    import_into_sqlite(export=_export(), database_path=database, scope=SCOPE)

    second = import_into_sqlite(export=_export(), database_path=database, scope=SCOPE)

    assert second["imported"] == {
        "product_families": 0,
        "knowledge_assertions": 0,
        "design_lessons": 0,
    }
    assert second["skipped_existing"] == {
        "product_families": 1,
        "knowledge_assertions": 1,
        "design_lessons": 1,
    }


def test_rows_outside_the_requested_scope_are_rejected(tmp_path: Path) -> None:
    export = _export()
    export["design_lessons"][0]["organization_id"] = "other-org"

    with pytest.raises(ValueError, match="outside the requested scope"):
        import_into_sqlite(
            export=export, database_path=tmp_path / "knowledge.sqlite3", scope=SCOPE
        )


def test_preencoded_json_columns_are_rejected(tmp_path: Path) -> None:
    export = _export()
    export["product_families"][0]["profile"] = '{"retrieval_terms": ["cradle"]}'

    with pytest.raises(ValueError, match="decoded JSON"):
        import_into_sqlite(
            export=export, database_path=tmp_path / "knowledge.sqlite3", scope=SCOPE
        )
