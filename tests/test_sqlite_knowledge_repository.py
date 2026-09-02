from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from mech_cad_design_agent.knowledge_records import (
    KnowledgeDatabaseError,
    KnowledgeScope,
)
from mech_cad_design_agent.migrations import sqlite_migrations_directory
from mech_cad_design_agent.models import canonical_json
from mech_cad_design_agent.sqlite_knowledge_repository import (
    SqliteKnowledgeRepository,
    query_tokens,
    search_blob,
)


def _repository(tmp_path: Path, *, organization: str = "org", group: str = "grp"):
    repository = SqliteKnowledgeRepository(
        tmp_path / "knowledge.sqlite3", KnowledgeScope(organization, group)
    )
    with sqlite_migrations_directory() as migrations:
        repository.apply_migrations(migrations)
    return repository


def _knowledge() -> dict[str, object]:
    return {
        "retrieval_terms": ["cradle"],
        "assertions": [
            {
                "subject": "wall",
                "predicate": "min_thickness_mm",
                "object": 2.4,
                "applicability": {"conditions": {"material": "PLA"}},
                "search_terms": ["wall thickness"],
            }
        ],
    }


def _publish_family(repository, family_id: str = "ball-carrier") -> dict[str, object]:
    return repository.publish_product_family(
        family_id=family_id,
        family_name="Basketball Cradle",
        aliases=["ball carrier"],
        knowledge=_knowledge(),
        decision_text="approved",
    )


def _card(title: str = "Chamfer the rim") -> tuple[dict[str, object], str]:
    card: dict[str, object] = {
        "schema_version": "DesignLessonReviewCard/v1",
        "review_id": "review-1",
        "lessons": [
            {
                "product_family_id": "ball-carrier",
                "title": title,
                "detail": "Chamfer 0.6 mm to avoid elephant foot",
                "search_terms": ["chamfer", "rim"],
                "applicability": {"conditions": {"material": "PLA"}},
            }
        ],
    }
    return card, hashlib.sha256(canonical_json(card).encode("utf-8")).hexdigest()


def test_migrations_apply_once_and_are_skipped_on_reapply(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(
        tmp_path / "knowledge.sqlite3", KnowledgeScope("org", "grp")
    )

    with sqlite_migrations_directory() as migrations:
        first = repository.apply_migrations(migrations)
        second = repository.apply_migrations(migrations)

    assert first == {"applied": ["001_knowledge.sql"], "skipped": []}
    assert second == {"applied": [], "skipped": ["001_knowledge.sql"]}


def test_migration_drift_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    drifted = tmp_path / "drifted"
    drifted.mkdir()
    (drifted / "001_knowledge.sql").write_text("CREATE TABLE other(x text);\n", "utf-8")

    with pytest.raises(KnowledgeDatabaseError) as error:
        repository.apply_migrations(drifted)

    assert error.value.code == "KNOWLEDGE_MIGRATION_DRIFT"


def test_unrelated_existing_schema_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unrelated(id text)")
    connection.commit()
    connection.close()
    repository = SqliteKnowledgeRepository(database, KnowledgeScope("org", "grp"))

    with pytest.raises(KnowledgeDatabaseError) as error, sqlite_migrations_directory() as migrations:
        repository.apply_migrations(migrations)

    assert error.value.code == "KNOWLEDGE_DATABASE_REINITIALIZATION_REQUIRED"


def test_operations_require_an_initialized_schema(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(
        tmp_path / "knowledge.sqlite3", KnowledgeScope("org", "grp")
    )

    with pytest.raises(KnowledgeDatabaseError) as error:
        repository.search(query="cradle")

    assert error.value.code == "KNOWLEDGE_SCHEMA_MISSING"


def test_publishing_a_product_family_is_idempotent(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    first = _publish_family(repository)
    second = _publish_family(repository)

    assert first["resumed"] is False
    assert second["resumed"] is True
    assert first["assertion_ids"] == second["assertion_ids"]
    assert first["status"] == "active"


def test_republishing_a_family_with_different_content_is_rejected(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _publish_family(repository)

    with pytest.raises(ValueError, match="different content"):
        repository.publish_product_family(
            family_id="ball-carrier",
            family_name="Different Name",
            aliases=[],
            knowledge={"assertions": []},
            decision_text="approved",
        )


def test_json_columns_round_trip_as_native_types(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _publish_family(repository)

    match = repository.match_product_family(
        query="", design_features={}, requested_family_id="ball-carrier"
    )

    assert match is not None
    assert match["aliases"] == ["ball carrier"]
    assert match["profile"] == {"retrieval_terms": ["cradle"]}
    assert match["search_terms"] == ["ball carrier", "basketball cradle", "cradle"]


def test_match_reports_explicit_exact_and_full_text_kinds(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _publish_family(repository)

    explicit = repository.match_product_family(
        query="", design_features={}, requested_family_id="ball-carrier"
    )
    exact = repository.match_product_family(query="basketball cradle", design_features={})
    full_text = repository.match_product_family(query="basketball", design_features={})

    assert explicit is not None and explicit["match_kind"] == "explicit_id"
    assert exact is not None and exact["match_kind"] == "exact_term"
    assert full_text is not None and full_text["match_kind"] == "full_text"


def test_full_text_match_requires_every_query_token(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _publish_family(repository)

    assert (
        repository.match_product_family(
            query="basketball helicopter", design_features={}
        )
        is None
    )


def test_ambiguous_exact_term_matches_are_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    for family_id in ("family-a", "family-b"):
        repository.publish_product_family(
            family_id=family_id,
            family_name="Shared Name",
            aliases=[],
            knowledge={"assertions": []},
            decision_text="approved",
        )

    with pytest.raises(ValueError, match="ambiguous"):
        repository.match_product_family(query="shared name", design_features={})


def test_missing_requested_family_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="does not exist in this scope"):
        repository.match_product_family(
            query="", design_features={}, requested_family_id="absent"
        )


def test_search_returns_families_assertions_and_lessons(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _publish_family(repository)
    card, digest = _card()
    repository.publish_design_lesson_review(review_card=card, review_sha256=digest)

    families = repository.search(query="basketball cradle")
    lessons = repository.search(query="chamfer")

    assert families["status"] == "completed_matches"
    assert [row["id"] for row in families["families"]] == ["ball-carrier"]
    assert [row["id"] for row in lessons["lessons"]] == [f"lesson-{digest[:16]}-1"]
    assert lessons["lessons"][0]["kind"] == "design_lesson"
    assert lessons["lessons"][0]["applicability"] == {"conditions": {"material": "PLA"}}


def test_search_scoped_to_a_family_binds_every_filter(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _publish_family(repository)
    repository.publish_product_family(
        family_id="other-family",
        family_name="Chamfer Jig",
        aliases=[],
        knowledge={
            "assertions": [
                {
                    "subject": "jig",
                    "predicate": "note",
                    "object": "chamfer",
                    "search_terms": ["chamfer"],
                }
            ]
        },
        decision_text="approved",
    )
    card, digest = _card()
    repository.publish_design_lesson_review(review_card=card, review_sha256=digest)

    scoped = repository.search(query="chamfer", product_family_id="ball-carrier")

    assert [row["id"] for row in scoped["families"]] == ["ball-carrier"]
    assert [row["id"] for row in scoped["lessons"]] == [f"lesson-{digest[:16]}-1"]
    assert all(
        row["product_family_id"] in (None, "ball-carrier")
        for row in scoped["assertions"]
    )


def test_search_honors_the_limit(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    for index in range(3):
        repository.publish_product_family(
            family_id=f"family-{index}",
            family_name=f"Cradle {index}",
            aliases=["shared alias"],
            knowledge={"assertions": []},
            decision_text="approved",
        )

    assert len(repository.search(query="shared alias", limit=2)["families"]) == 2


def test_search_without_matches_reports_no_match(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _publish_family(repository)

    assert repository.search(query="helicopter")["status"] == "completed_no_match"


def test_search_rejects_blank_queries_and_out_of_range_limits(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="query is required"):
        repository.search(query="   ")
    with pytest.raises(ValueError, match="limit must be between"):
        repository.search(query="cradle", limit=0)
    with pytest.raises(ValueError, match="limit must be between"):
        repository.search(query="cradle", limit=101)


def test_publishing_a_lesson_review_is_idempotent(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _publish_family(repository)
    card, digest = _card()

    first = repository.publish_design_lesson_review(
        review_card=card, review_sha256=digest
    )
    second = repository.publish_design_lesson_review(
        review_card=card, review_sha256=digest
    )

    assert first["resumed"] is False
    assert second["resumed"] is True
    assert first["lesson_ids"] == second["lesson_ids"]


def test_lesson_review_sha256_must_match_the_card(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    card, _digest = _card()

    with pytest.raises(ValueError, match="SHA-256 does not match"):
        repository.publish_design_lesson_review(review_card=card, review_sha256="a" * 64)


def test_design_lesson_status_transitions_apply_once(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _publish_family(repository)
    card, digest = _card()
    lesson_id = repository.publish_design_lesson_review(
        review_card=card, review_sha256=digest
    )["lesson_ids"][0]

    revoked = repository.set_design_lesson_status(lesson_id=lesson_id, status="revoked")

    assert revoked == {"id": lesson_id, "status": "revoked", "supersedes_id": None}
    with pytest.raises(ValueError, match="active Design Lesson does not exist"):
        repository.set_design_lesson_status(lesson_id=lesson_id, status="revoked")


def test_superseding_requires_a_replacement(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="requires replacement_lesson_id"):
        repository.set_design_lesson_status(lesson_id="lesson-a", status="superseded")


def test_knowledge_is_isolated_between_scopes(tmp_path: Path) -> None:
    owner = _repository(tmp_path, organization="org", group="grp")
    _publish_family(owner)
    card, digest = _card()
    lesson_id = owner.publish_design_lesson_review(
        review_card=card, review_sha256=digest
    )["lesson_ids"][0]
    other = SqliteKnowledgeRepository(
        tmp_path / "knowledge.sqlite3", KnowledgeScope("other-org", "grp")
    )

    assert other.search(query="basketball cradle")["status"] == "completed_no_match"
    with pytest.raises(ValueError, match="does not exist in this scope"):
        other.get_design_lesson(lesson_id)


def test_projection_records_expose_every_aggregate(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _publish_family(repository)
    card, digest = _card()
    repository.publish_design_lesson_review(review_card=card, review_sha256=digest)

    records = repository.projection_records()

    assert [row["id"] for row in records["product_family"]] == ["ball-carrier"]
    assert len(records["assertion"]) == 1
    assert len(records["design_lesson"]) == 1
    assert repository.projection_record(
        aggregate_type="product_family", aggregate_id="ball-carrier"
    )["canonical_name"] == "Basketball Cradle"


def test_projection_record_rejects_unknown_aggregates(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="unsupported knowledge aggregate"):
        repository.projection_record(aggregate_type="unknown", aggregate_id="x")


def test_search_blob_normalizes_punctuation_into_word_tokens() -> None:
    assert search_blob("Wall-Thickness (2.4mm)") == " wall thickness 2 4mm "
    assert query_tokens("Wall-Thickness") == ["wall", "thickness"]
    assert search_blob("") == " "
