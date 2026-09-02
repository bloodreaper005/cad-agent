from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping

from .knowledge_matching import collect_design_terms, normalize_search_term
from .knowledge_records import (
    KnowledgeDatabaseError,
    KnowledgeScope,
    build_family_rows,
    build_lesson_rows,
)
from .migrations import discover_sqlite_migrations
from .models import canonical_json, require_safe_id


_EXPECTED_MIGRATIONS = ("001_knowledge.sql",)
_JSON_COLUMNS = frozenset(
    {
        "aliases",
        "applicability",
        "content",
        "evidence",
        "object_value",
        "profile",
        "provenance",
        "search_terms",
    }
)


def search_blob(value: str) -> str:
    """Normalize searchable text into space-delimited tokens for word matching."""
    normalized = normalize_search_term(value)
    tokens = "".join(
        character if character.isalnum() else " " for character in normalized
    ).split()
    return f" {' '.join(tokens)} " if tokens else " "


def query_tokens(value: str) -> list[str]:
    return search_blob(value).split()


def _split_statements(sql: str) -> list[str]:
    """Split a migration into complete statements without an implicit commit."""
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise ValueError("migration ends with an incomplete statement")
    return statements


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key in _JSON_COLUMNS and isinstance(value, str):
            decoded[key] = json.loads(value)
        else:
            decoded[key] = value
    return decoded


class SqliteKnowledgeRepository:
    """Embedded SQLite authority for Product Family Knowledge and Design Lessons."""

    def __init__(self, database_path: str | Path, scope: KnowledgeScope) -> None:
        if isinstance(database_path, Path):
            resolved = database_path
        elif isinstance(database_path, str) and database_path.strip():
            resolved = Path(database_path.strip())
        else:
            raise ValueError("database_path is required")
        self.database_path = resolved.expanduser()
        self.scope = scope

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.database_path, isolation_level=None, timeout=30.0
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, connection: sqlite3.Connection) -> Iterator[None]:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    # -- schema ---------------------------------------------------------------

    def apply_migrations(self, root: Path | None = None) -> dict[str, list[str]]:
        if root is None:
            from .migrations import sqlite_migrations_directory

            with sqlite_migrations_directory() as directory:
                return self.apply_migrations(directory)
        migrations = discover_sqlite_migrations(root)
        if tuple(path.name for path in migrations) != _EXPECTED_MIGRATIONS:
            raise ValueError("knowledge migration inventory is incomplete or unexpected")
        applied: list[str] = []
        skipped: list[str] = []
        with self.connection() as connection:
            self._reject_incompatible_database(connection)
            for path in migrations:
                version = int(path.name[:3])
                sql_bytes = path.read_bytes()
                try:
                    sql = sql_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"migration is not UTF-8: {path.name}") from exc
                digest = hashlib.sha256(sql_bytes).hexdigest()
                existing = (
                    connection.execute(
                        "SELECT filename,sha256 FROM knowledge_schema_migrations "
                        "WHERE version=?",
                        (version,),
                    ).fetchone()
                    if self._knowledge_schema_exists(connection)
                    else None
                )
                if existing:
                    if existing["filename"] != path.name or existing["sha256"] != digest:
                        raise KnowledgeDatabaseError(
                            "KNOWLEDGE_MIGRATION_DRIFT",
                            f"knowledge migration {version:03d} does not match",
                        )
                    skipped.append(path.name)
                    continue
                with self.transaction(connection):
                    for statement in _split_statements(sql):
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO knowledge_schema_migrations"
                        "(version,filename,sha256) VALUES (?,?,?)",
                        (version, path.name, digest),
                    )
                applied.append(path.name)
        return {"applied": applied, "skipped": skipped}

    @staticmethod
    def _knowledge_schema_exists(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='knowledge_schema_migrations'"
        ).fetchone()
        return row is not None

    def _reject_incompatible_database(self, connection: sqlite3.Connection) -> None:
        if self._knowledge_schema_exists(connection):
            return
        row = connection.execute(
            "SELECT count(*) AS existing_tables FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
        if int(row["existing_tables"] or 0) > 0:
            raise KnowledgeDatabaseError(
                "KNOWLEDGE_DATABASE_REINITIALIZATION_REQUIRED",
                "the database contains an unsupported schema; "
                "initialize a new knowledge database",
            )

    def _require_schema(self, connection: sqlite3.Connection) -> None:
        if not self._knowledge_schema_exists(connection):
            raise KnowledgeDatabaseError(
                "KNOWLEDGE_SCHEMA_MISSING",
                "knowledge schema is not initialized; run knowledge bootstrap",
            )

    # -- publication ----------------------------------------------------------

    def publish_design_lesson_review(
        self,
        *,
        review_card: Mapping[str, object],
        review_sha256: str,
        decision_text: str = "approved",
    ) -> dict[str, object]:
        expected_rows = build_lesson_rows(
            review_card=review_card,
            review_sha256=review_sha256,
            decision_text=decision_text,
        )
        with self.connection() as connection:
            self._require_schema(connection)
            with self.transaction(connection):
                existing = [
                    _decode_row(row)
                    for row in connection.execute(
                        "SELECT id,product_family_id,content,applicability,provenance,"
                        "search_terms,search_text,status,supersedes_id "
                        "FROM design_lessons WHERE organization_id=? AND design_group_id=? "
                        "AND json_extract(provenance,'$.source_review_sha256')=? "
                        "ORDER BY id",
                        (
                            self.scope.organization_id,
                            self.scope.design_group_id,
                            review_sha256,
                        ),
                    ).fetchall()
                ]
                if existing:
                    if canonical_json(existing) != canonical_json(expected_rows):
                        raise ValueError(
                            "Design Lessons already exist with different canonical content"
                        )
                    return {
                        "publication_id": review_sha256,
                        "review_sha256": review_sha256,
                        "lesson_ids": [row["id"] for row in expected_rows],
                        "resumed": True,
                    }
                for row in expected_rows:
                    connection.execute(
                        "INSERT INTO design_lessons("
                        "id,organization_id,design_group_id,product_family_id,content,"
                        "applicability,provenance,search_terms,search_text,search_blob,"
                        "status,supersedes_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
                        (
                            row["id"],
                            self.scope.organization_id,
                            self.scope.design_group_id,
                            row["product_family_id"],
                            canonical_json(row["content"]),
                            canonical_json(row["applicability"]),
                            canonical_json(row["provenance"]),
                            canonical_json(row["search_terms"]),
                            row["search_text"],
                            search_blob(str(row["search_text"])),
                            row["status"],
                        ),
                    )
        return {
            "publication_id": review_sha256,
            "review_sha256": review_sha256,
            "lesson_ids": [row["id"] for row in expected_rows],
            "resumed": False,
        }

    def publish_product_family(
        self,
        *,
        family_id: str,
        family_name: str,
        aliases: list[object],
        knowledge: Mapping[str, object],
        decision_text: str,
    ) -> dict[str, object]:
        family_row, assertion_rows = build_family_rows(
            family_id=family_id,
            family_name=family_name,
            aliases=aliases,
            knowledge=knowledge,
            decision_text=decision_text,
        )
        with self.connection() as connection:
            self._require_schema(connection)
            with self.transaction(connection):
                existing = connection.execute(
                    "SELECT id,canonical_name,aliases,profile,search_terms,search_text,"
                    "status FROM product_families WHERE organization_id=? "
                    "AND design_group_id=? AND id=?",
                    (
                        self.scope.organization_id,
                        self.scope.design_group_id,
                        family_id,
                    ),
                ).fetchone()
                if existing:
                    if canonical_json(_decode_row(existing)) != canonical_json(
                        family_row
                    ):
                        raise ValueError(
                            "Product Family already exists with different content"
                        )
                    existing_assertions = [
                        _decode_row(row)
                        for row in connection.execute(
                            "SELECT id,product_family_id,subject,predicate,object_value,"
                            "applicability,evidence,search_terms,search_text,status,"
                            "supersedes_id FROM knowledge_assertions "
                            "WHERE organization_id=? AND design_group_id=? "
                            "AND product_family_id=? ORDER BY id",
                            (
                                self.scope.organization_id,
                                self.scope.design_group_id,
                                family_id,
                            ),
                        ).fetchall()
                    ]
                    if canonical_json(existing_assertions) != canonical_json(
                        assertion_rows
                    ):
                        raise ValueError(
                            "Product Family assertions already exist with different content"
                        )
                    return {
                        "family_id": family_id,
                        "assertion_ids": [row["id"] for row in assertion_rows],
                        "status": existing["status"],
                        "resumed": True,
                    }
                connection.execute(
                    "INSERT INTO product_families"
                    "(id,organization_id,design_group_id,canonical_name,aliases,profile,"
                    "search_terms,search_text,search_blob,status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        family_id,
                        self.scope.organization_id,
                        self.scope.design_group_id,
                        family_row["canonical_name"],
                        canonical_json(family_row["aliases"]),
                        canonical_json(family_row["profile"]),
                        canonical_json(family_row["search_terms"]),
                        family_row["search_text"],
                        search_blob(str(family_row["search_text"])),
                        family_row["status"],
                    ),
                )
                for row in assertion_rows:
                    connection.execute(
                        "INSERT INTO knowledge_assertions("
                        "id,organization_id,design_group_id,product_family_id,subject,"
                        "predicate,object_value,applicability,evidence,search_terms,"
                        "search_text,search_blob,status,supersedes_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                        (
                            row["id"],
                            self.scope.organization_id,
                            self.scope.design_group_id,
                            family_id,
                            row["subject"],
                            row["predicate"],
                            canonical_json(row["object_value"]),
                            canonical_json(row["applicability"]),
                            canonical_json(row["evidence"]),
                            canonical_json(row["search_terms"]),
                            row["search_text"],
                            search_blob(str(row["search_text"])),
                            row["status"],
                        ),
                    )
        return {
            "family_id": family_id,
            "assertion_ids": [row["id"] for row in assertion_rows],
            "status": "active",
            "resumed": False,
        }

    # -- retrieval ------------------------------------------------------------

    @staticmethod
    def _family_match(row: Mapping[str, object], match_kind: str) -> dict[str, object]:
        return {
            "knowledge_id": row["id"],
            "kind": "product_family",
            **dict(row),
            "match_kind": match_kind,
        }

    @staticmethod
    def _term_overlap_clause(column_owner: str, terms: tuple[str, ...]) -> tuple[str, list[object]]:
        placeholders = ",".join("?" for _ in terms)
        clause = (
            f"EXISTS (SELECT 1 FROM json_each({column_owner}.search_terms) "
            f"WHERE json_each.value IN ({placeholders}))"
        )
        return clause, list(terms)

    @staticmethod
    def _text_match_clause(column_owner: str, tokens: list[str]) -> tuple[str, list[object]]:
        if not tokens:
            return "0", []
        clause = " AND ".join(f"{column_owner}.search_blob LIKE ?" for _ in tokens)
        return f"({clause})", [f"% {token} %" for token in tokens]

    def match_product_family(
        self,
        *,
        query: str,
        design_features: Mapping[str, object],
        requested_family_id: str | None = None,
    ) -> dict[str, object] | None:
        terms = collect_design_terms(query, design_features)
        columns = "id,canonical_name,aliases,profile,search_terms,search_text,status"
        with self.connection() as connection:
            self._require_schema(connection)
            if requested_family_id:
                require_safe_id(requested_family_id, "requested_family_id")
                row = connection.execute(
                    f"SELECT {columns} FROM product_families "
                    "WHERE organization_id=? AND design_group_id=? "
                    "AND status='active' AND id=?",
                    (
                        self.scope.organization_id,
                        self.scope.design_group_id,
                        requested_family_id,
                    ),
                ).fetchone()
                if not row:
                    raise ValueError("Product Family does not exist in this scope")
                return self._family_match(_decode_row(row), "explicit_id")
            if not terms:
                return None
            overlap, overlap_parameters = self._term_overlap_clause(
                "product_families", terms
            )
            exact = connection.execute(
                f"SELECT {columns} FROM product_families AS product_families "
                "WHERE organization_id=? AND design_group_id=? AND status='active' "
                f"AND {overlap} ORDER BY id LIMIT 2",
                (
                    self.scope.organization_id,
                    self.scope.design_group_id,
                    *overlap_parameters,
                ),
            ).fetchall()
            if len(exact) > 1:
                raise ValueError("Product Family exact-term match is ambiguous")
            if exact:
                return self._family_match(_decode_row(exact[0]), "exact_term")
            tokens = query_tokens(" ".join(terms))
            text_clause, text_parameters = self._text_match_clause(
                "product_families", tokens
            )
            rows = connection.execute(
                f"SELECT {columns} FROM product_families AS product_families "
                "WHERE organization_id=? AND design_group_id=? AND status='active' "
                f"AND {text_clause} ORDER BY id LIMIT 1",
                (
                    self.scope.organization_id,
                    self.scope.design_group_id,
                    *text_parameters,
                ),
            ).fetchall()
        return self._family_match(_decode_row(rows[0]), "full_text") if rows else None

    def search(
        self,
        *,
        query: str,
        product_family_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        terms = collect_design_terms(query, {})
        if not terms:
            raise ValueError("query is required")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        tokens = query_tokens(" ".join(terms))

        def matcher(table: str) -> tuple[str, list[object]]:
            overlap, overlap_parameters = self._term_overlap_clause(table, terms)
            text_clause, text_parameters = self._text_match_clause(table, tokens)
            return (
                f"({overlap} OR {text_clause})",
                [*overlap_parameters, *text_parameters],
            )

        with self.connection() as connection:
            self._require_schema(connection)
            if product_family_id:
                families = connection.execute(
                    "SELECT id,canonical_name,aliases,profile,search_terms,search_text,"
                    "status FROM product_families WHERE organization_id=? "
                    "AND design_group_id=? AND status='active' AND id=? LIMIT ?",
                    (
                        self.scope.organization_id,
                        self.scope.design_group_id,
                        product_family_id,
                        limit,
                    ),
                ).fetchall()
            else:
                clause, parameters = matcher("product_families")
                families = connection.execute(
                    "SELECT id,canonical_name,aliases,profile,search_terms,search_text,"
                    "status FROM product_families AS product_families "
                    "WHERE organization_id=? AND design_group_id=? AND status='active' "
                    f"AND {clause} ORDER BY id LIMIT ?",
                    (
                        self.scope.organization_id,
                        self.scope.design_group_id,
                        *parameters,
                        limit,
                    ),
                ).fetchall()

            family_filter = (
                "AND (product_family_id=? OR product_family_id IS NULL) "
                if product_family_id
                else ""
            )

            def scoped(table: str) -> tuple[str, list[object]]:
                clause, parameters = matcher(table)
                values: list[object] = [
                    self.scope.organization_id,
                    self.scope.design_group_id,
                ]
                if product_family_id:
                    values.append(product_family_id)
                values.extend(parameters)
                values.append(limit)
                return clause, values

            assertion_clause, assertion_parameters = scoped("knowledge_assertions")
            assertions = connection.execute(
                "SELECT id,product_family_id,subject,predicate,object_value,"
                "applicability,evidence,search_terms,status,supersedes_id "
                "FROM knowledge_assertions AS knowledge_assertions "
                "WHERE organization_id=? AND design_group_id=? AND status='active' "
                f"{family_filter}AND {assertion_clause} ORDER BY id LIMIT ?",
                tuple(assertion_parameters),
            ).fetchall()

            lesson_clause, lesson_parameters = scoped("design_lessons")
            lessons = connection.execute(
                "SELECT id,product_family_id,content,applicability,provenance,"
                "search_terms,status,supersedes_id "
                "FROM design_lessons AS design_lessons "
                "WHERE organization_id=? AND design_group_id=? AND status='active' "
                f"{family_filter}AND {lesson_clause} ORDER BY id LIMIT ?",
                tuple(lesson_parameters),
            ).fetchall()

        family_matches = [
            {"knowledge_id": row["id"], "kind": "product_family", **_decode_row(row)}
            for row in families
        ]
        assertion_matches = [
            {
                "assertion_id": row["id"],
                "knowledge_id": row["id"],
                "kind": "knowledge_assertion",
                **_decode_row(row),
            }
            for row in assertions
        ]
        lesson_matches = [
            {
                "design_lesson_ref": row["id"],
                "knowledge_id": row["id"],
                "kind": "design_lesson",
                **_decode_row(row),
            }
            for row in lessons
        ]
        return {
            "schema_version": "KnowledgeSearchResult/v1",
            "status": (
                "completed_matches"
                if family_matches or assertion_matches or lesson_matches
                else "completed_no_match"
            ),
            "families": family_matches,
            "assertions": assertion_matches,
            "lessons": lesson_matches,
            "matches": [*family_matches, *assertion_matches, *lesson_matches],
        }

    def get_design_lesson(self, lesson_id: str) -> dict[str, object]:
        require_safe_id(lesson_id, "lesson_id")
        with self.connection() as connection:
            self._require_schema(connection)
            row = connection.execute(
                "SELECT id,content,applicability,provenance,search_terms,"
                "product_family_id,status,supersedes_id,created_at "
                "FROM design_lessons WHERE id=? AND organization_id=? "
                "AND design_group_id=?",
                (
                    lesson_id,
                    self.scope.organization_id,
                    self.scope.design_group_id,
                ),
            ).fetchone()
        if not row:
            raise ValueError("Design Lesson does not exist in this scope")
        return _decode_row(row)

    def set_design_lesson_status(
        self,
        *,
        lesson_id: str,
        status: str,
        replacement_lesson_id: str | None = None,
    ) -> dict[str, object]:
        if status not in {"superseded", "revoked"}:
            raise ValueError("status must be superseded or revoked")
        if status == "superseded" and not replacement_lesson_id:
            raise ValueError("superseded status requires replacement_lesson_id")
        require_safe_id(lesson_id, "lesson_id")
        if replacement_lesson_id:
            require_safe_id(replacement_lesson_id, "replacement_lesson_id")
        with self.connection() as connection:
            self._require_schema(connection)
            with self.transaction(connection):
                row = connection.execute(
                    "UPDATE design_lessons SET status=?,supersedes_id=? "
                    "WHERE id=? AND organization_id=? AND design_group_id=? "
                    "AND status='active' RETURNING id,status,supersedes_id",
                    (
                        status,
                        replacement_lesson_id,
                        lesson_id,
                        self.scope.organization_id,
                        self.scope.design_group_id,
                    ),
                ).fetchone()
                if not row:
                    raise ValueError("active Design Lesson does not exist in this scope")
                result = _decode_row(row)
        return result

    # -- projection -----------------------------------------------------------

    def projection_record(
        self, *, aggregate_type: str, aggregate_id: str
    ) -> dict[str, object]:
        queries = {
            "product_family": (
                "SELECT id,canonical_name,status,organization_id,design_group_id "
                "FROM product_families WHERE id=?"
            ),
            "assertion": (
                "SELECT id,subject,predicate,status,organization_id,design_group_id,"
                "product_family_id FROM knowledge_assertions WHERE id=?"
            ),
            "design_lesson": (
                "SELECT id,status,organization_id,design_group_id,product_family_id "
                "FROM design_lessons WHERE id=?"
            ),
        }
        query = queries.get(aggregate_type)
        if query is None:
            raise ValueError(f"unsupported knowledge aggregate: {aggregate_type}")
        with self.connection() as connection:
            self._require_schema(connection)
            row = connection.execute(query, (aggregate_id,)).fetchone()
        if not row:
            raise ValueError("knowledge record does not exist")
        return _decode_row(row)

    def projection_records(self) -> dict[str, list[dict[str, object]]]:
        with self.connection() as connection:
            self._require_schema(connection)
            return {
                "product_family": [
                    _decode_row(row)
                    for row in connection.execute(
                        "SELECT id,canonical_name,status,organization_id,design_group_id "
                        "FROM product_families ORDER BY id"
                    ).fetchall()
                ],
                "assertion": [
                    _decode_row(row)
                    for row in connection.execute(
                        "SELECT id,subject,predicate,status,organization_id,"
                        "design_group_id,product_family_id "
                        "FROM knowledge_assertions ORDER BY id"
                    ).fetchall()
                ],
                "design_lesson": [
                    _decode_row(row)
                    for row in connection.execute(
                        "SELECT id,status,organization_id,design_group_id,"
                        "product_family_id FROM design_lessons ORDER BY id"
                    ).fetchall()
                ],
            }


__all__ = [
    "SqliteKnowledgeRepository",
    "query_tokens",
    "search_blob",
]
