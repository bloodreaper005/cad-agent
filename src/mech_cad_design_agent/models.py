from __future__ import annotations

import json
import re


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)


def canonical_json(value: object) -> str:
    """Serialize finite JSON deterministically for hashing and atomic storage."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def require_safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} is unsafe")
    if value.rstrip(" .") != value:
        raise ValueError(f"{label} is unsafe")
    if value.split(".", 1)[0].casefold() in _WINDOWS_RESERVED:
        raise ValueError(f"{label} is unsafe")
    return value


__all__ = ["canonical_json", "require_safe_id"]
