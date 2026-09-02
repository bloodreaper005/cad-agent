from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from .knowledge_matching import normalize_search_term
from .models import canonical_json, require_safe_id


SHA256_PATTERN = "^[0-9a-f]{64}$"


class KnowledgeDatabaseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class KnowledgeScope:
    organization_id: str
    design_group_id: str

    def __post_init__(self) -> None:
        require_safe_id(self.organization_id, "organization_id")
        require_safe_id(self.design_group_id, "design_group_id")


def json_copy(value: object, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain finite JSON values") from exc


def search_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Mapping):
        result: list[str] = []
        for key in sorted(value, key=str):
            result.extend(search_values(str(key)))
            result.extend(search_values(value[key]))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(search_values(item))
        return result
    if isinstance(value, (bool, int, float)):
        return [str(value)]
    return []


def search_text(*values: object) -> str:
    text = " ".join(search_values(values))
    normalized = " ".join(text.split())
    if not normalized:
        raise ValueError("knowledge record has no searchable text")
    return normalized


def normalized_strings(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return sorted(
        {
            normalized
            for item in value
            if (normalized := normalize_search_term(item))
        }
    )


def build_lesson_rows(
    *,
    review_card: Mapping[str, object],
    review_sha256: str,
    decision_text: str,
) -> list[dict[str, object]]:
    """Validate a Design Lesson review card and shape its durable rows."""
    if not isinstance(review_sha256, str) or len(review_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in review_sha256
    ):
        raise ValueError("review_sha256 must be lowercase 64-hex")
    card = json_copy(dict(review_card), "review card")
    if not isinstance(card, dict) or card.get("schema_version") != (
        "DesignLessonReviewCard/v1"
    ):
        raise ValueError("review card schema is invalid")
    if hashlib.sha256(canonical_json(card).encode("utf-8")).hexdigest() != (
        review_sha256
    ):
        raise ValueError("review card SHA-256 does not match")
    lessons = card.get("lessons")
    if not isinstance(lessons, list) or not lessons:
        raise ValueError("review card has no publishable lessons")
    if not isinstance(decision_text, str) or not decision_text.strip():
        raise ValueError("decision_text is required")

    rows: list[dict[str, object]] = []
    for index, lesson in enumerate(lessons, start=1):
        if not isinstance(lesson, Mapping):
            raise ValueError("review card lessons must be objects")
        copied = json_copy(dict(lesson), "review card lesson")
        lesson_id = f"lesson-{review_sha256[:16]}-{index}"
        family_id = copied.pop("product_family_id", None)
        copied.pop("scope", None)
        terms = normalized_strings(copied.pop("search_terms", []), "lesson search_terms")
        raw_applicability = copied.pop("applicability", {})
        if isinstance(raw_applicability, Mapping):
            applicability = json_copy(dict(raw_applicability), "lesson applicability")
        elif isinstance(raw_applicability, str):
            applicability = (
                {"summary": raw_applicability.strip()}
                if raw_applicability.strip()
                else {}
            )
        else:
            raise ValueError("lesson applicability must be an object or string")
        evidence = copied.pop("evidence", [])
        provenance = {
            "source_review_sha256": review_sha256,
            "decision_text": decision_text.strip(),
            "source_review_id": card["review_id"],
            "evidence": evidence,
        }
        rows.append(
            {
                "id": lesson_id,
                "product_family_id": family_id,
                "content": copied,
                "applicability": applicability,
                "provenance": provenance,
                "search_terms": terms,
                "search_text": search_text(copied, applicability, provenance, terms),
                "status": "active",
                "supersedes_id": None,
            }
        )
    return rows


def build_family_rows(
    *,
    family_id: str,
    family_name: str,
    aliases: list[object],
    knowledge: Mapping[str, object],
    decision_text: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Validate Product Family Knowledge and shape its family and assertion rows."""
    require_safe_id(family_id, "family_id")
    if not family_name.strip() or not decision_text.strip():
        raise ValueError("family_name and decision_text are required")
    if not all(isinstance(value, str) and value.strip() for value in aliases):
        raise ValueError("aliases must contain nonblank strings")
    copied_knowledge = json_copy(dict(knowledge), "Product Family Knowledge")
    raw_assertions = copied_knowledge.pop("assertions", [])
    if not isinstance(raw_assertions, list):
        raise ValueError("knowledge.assertions must be a list")
    normalized_aliases = sorted({str(value).strip() for value in aliases})
    family_terms = sorted(
        {
            normalize_search_term(family_name),
            *[normalize_search_term(value) for value in normalized_aliases],
            *normalized_strings(
                copied_knowledge.get("retrieval_terms"), "knowledge.retrieval_terms"
            ),
        }
        - {""}
    )
    family_row = {
        "id": family_id,
        "canonical_name": family_name.strip(),
        "aliases": normalized_aliases,
        "profile": copied_knowledge,
        "search_terms": family_terms,
        "search_text": search_text(
            family_name, normalized_aliases, family_terms, copied_knowledge
        ),
        "status": "active",
    }

    assertion_rows: list[dict[str, object]] = []
    for raw in raw_assertions:
        if not isinstance(raw, Mapping):
            raise ValueError("knowledge assertions must be objects")
        assertion = json_copy(dict(raw), "knowledge assertion")
        subject = assertion.get("subject")
        predicate = assertion.get("predicate")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("knowledge assertion subject is required")
        if not isinstance(predicate, str) or not predicate.strip():
            raise ValueError("knowledge assertion predicate is required")
        if "object" not in assertion and "object_value" not in assertion:
            raise ValueError("knowledge assertion object is required")
        object_value = assertion.get("object", assertion.get("object_value"))
        applicability = json_copy(
            assertion.get("applicability") or {}, "assertion applicability"
        )
        evidence = json_copy(assertion.get("evidence") or [], "assertion evidence")
        terms = normalized_strings(
            assertion.get("search_terms"), "assertion search_terms"
        )
        digest_input = {
            "subject": subject.strip(),
            "predicate": predicate.strip(),
            "object_value": object_value,
            "applicability": applicability,
            "evidence": evidence,
            "search_terms": terms,
        }
        digest = hashlib.sha256(canonical_json(digest_input).encode("utf-8")).hexdigest()
        assertion_rows.append(
            {
                "id": f"{family_id[:101]}-assertion-{digest[:16]}",
                "product_family_id": family_id,
                **digest_input,
                "search_text": search_text(
                    subject,
                    predicate,
                    object_value,
                    applicability,
                    evidence,
                    terms,
                ),
                "status": "active",
                "supersedes_id": None,
            }
        )
    assertion_rows.sort(key=lambda row: str(row["id"]))
    if len({str(row["id"]) for row in assertion_rows}) != len(assertion_rows):
        raise ValueError("duplicate Product Family assertions are not allowed")
    return family_row, assertion_rows


__all__ = [
    "KnowledgeDatabaseError",
    "KnowledgeScope",
    "SHA256_PATTERN",
    "build_family_rows",
    "build_lesson_rows",
    "json_copy",
    "normalized_strings",
    "search_text",
    "search_values",
]
