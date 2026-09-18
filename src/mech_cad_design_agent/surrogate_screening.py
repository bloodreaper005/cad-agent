from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .workspace_bootstrap import BootstrapFailure


SCREENING_SCHEMA = "SurrogateScreening/v1"
SCREENING_ATTESTATION = "screening_estimate"

_TOP_KEYS = {
    "schema_version",
    "screened_sha256",
    "model",
    "coverage",
    "calibration",
    "domain",
    "predictions",
    "assumptions",
    "limitations",
    "attestation",
}
_OPTIONAL_TOP_KEYS = {"fields"}
_MODEL_KEYS = {"name", "version", "sha256"}
_CALIBRATION_KEYS = {"method", "set_size", "set_sha256"}
_PREDICTION_KEYS = {"quantity", "lower", "upper", "units"}
_FIELD_KEYS = {
    "quantity",
    "mesh_sha256",
    "values_relative_path",
    "interval_relative_path",
    "image_relative_path",
    "image_sha256",
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _invalid(message: str) -> BootstrapFailure:
    return BootstrapFailure("SURROGATE_SCREENING_INVALID", message)


def _freeze_value(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {key: _freeze_value(item) for key, item in value.items()}
    )


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{label} must be a nonblank string")
    return value


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise _invalid(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_number(value: object, label: str) -> float:
    # bool is an int subclass, and a boolean stress value is a malformed
    # document rather than a number to coerce.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise _invalid(f"{label} must be finite")
    return number


def _require_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{label} must be a nonblank string")
    if "\\" in value or value.startswith("/") or ":" in value:
        raise _invalid(f"{label} must be a relative POSIX path")
    segments = value.split("/")
    if not all(_PATH_SEGMENT.fullmatch(segment) for segment in segments):
        raise _invalid(f"{label} must be a safe relative path")
    return value


def _require_text_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _invalid(f"{label} must be a nonempty list")
    return tuple(_require_text(item, f"{label} entry") for item in value)


def _parse_model(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _MODEL_KEYS:
        raise _invalid("model identity is incomplete")
    return {
        "name": _require_text(value["name"], "model name"),
        "version": _require_text(value["version"], "model version"),
        "sha256": _require_digest(value["sha256"], "model sha256"),
    }


def _parse_calibration(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _CALIBRATION_KEYS:
        raise _invalid("calibration record is incomplete")
    set_size = value["set_size"]
    if isinstance(set_size, bool) or not isinstance(set_size, int) or set_size < 1:
        raise _invalid("calibration set_size must be a positive integer")
    return {
        "method": _require_text(value["method"], "calibration method"),
        "set_size": set_size,
        "set_sha256": _require_digest(value["set_sha256"], "calibration set_sha256"),
    }


def _parse_predictions(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not value:
        # An empty prediction list would otherwise satisfy every downstream
        # reader vacuously, which is the failure 0.10.0 closed for validation
        # reports.
        raise _invalid("predictions must be a nonempty list")
    parsed: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != _PREDICTION_KEYS:
            # A document carrying a bare point estimate fails here, because
            # `lower` and `upper` are both required. A surrogate that cannot
            # state an interval has nothing admissible to say.
            raise _invalid("prediction entry must carry quantity, lower, upper, units")
        quantity = _require_text(item["quantity"], "prediction quantity")
        if quantity in seen:
            raise _invalid("prediction quantities must be unique")
        seen.add(quantity)
        lower = _require_number(item["lower"], "prediction lower bound")
        upper = _require_number(item["upper"], "prediction upper bound")
        if lower > upper:
            raise _invalid("prediction lower bound must not exceed its upper bound")
        parsed.append(
            {
                "quantity": quantity,
                "lower": lower,
                "upper": upper,
                "units": _require_text(item["units"], "prediction units"),
            }
        )
    return tuple(parsed)


def _parse_fields(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        raise _invalid("fields must be a list")
    parsed: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != _FIELD_KEYS:
            # `interval_relative_path` is required alongside the values, so a
            # full field without per-node bands cannot be stored at all. A
            # rendered contour plot with no uncertainty behind it is the one
            # artifact this subsystem must never produce.
            raise _invalid("field entry is incomplete")
        quantity = _require_text(item["quantity"], "field quantity")
        if quantity in seen:
            raise _invalid("field quantities must be unique")
        seen.add(quantity)
        parsed.append(
            {
                "quantity": quantity,
                "mesh_sha256": _require_digest(item["mesh_sha256"], "field mesh_sha256"),
                "values_relative_path": _require_relative_path(
                    item["values_relative_path"], "field values_relative_path"
                ),
                "interval_relative_path": _require_relative_path(
                    item["interval_relative_path"], "field interval_relative_path"
                ),
                "image_relative_path": _require_relative_path(
                    item["image_relative_path"], "field image_relative_path"
                ),
                "image_sha256": _require_digest(item["image_sha256"], "field image_sha256"),
            }
        )
    return tuple(parsed)


@dataclass(frozen=True)
class SurrogateScreening:
    """One calibrated screening estimate, bound to the geometry it describes.

    This is an estimate with declared coverage, not evidence. Nothing in this
    record may gate completion, and `design_session` is responsible for keeping
    it out of the validation path.
    """

    screened_sha256: str
    model: Mapping[str, object]
    coverage: float
    calibration: Mapping[str, object]
    domain: Mapping[str, object]
    predictions: tuple[Mapping[str, object], ...]
    fields: tuple[Mapping[str, object], ...]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCREENING_SCHEMA,
            "screened_sha256": self.screened_sha256,
            "model": _json_value(self.model),
            "coverage": self.coverage,
            "calibration": _json_value(self.calibration),
            "domain": _json_value(self.domain),
            "predictions": [_json_value(item) for item in self.predictions],
            "fields": [_json_value(item) for item in self.fields],
            "assumptions": list(self.assumptions),
            "limitations": list(self.limitations),
            "attestation": SCREENING_ATTESTATION,
        }


def parse_surrogate_screening(value: object) -> SurrogateScreening:
    """Validate one `SurrogateScreening/v1` document handed in from outside.

    Every rejection here is deliberate: the document arrives self-reported from
    a process this package does not control, so the parse is the only place its
    shape is established. A prediction without an interval, an interval without
    a calibration record, or a field without per-node bands is refused rather
    than stored in a weakened form.
    """
    if not isinstance(value, dict):
        raise _invalid("screening document must be an object")
    keys = set(value)
    if not _TOP_KEYS <= keys or not keys <= (_TOP_KEYS | _OPTIONAL_TOP_KEYS):
        raise _invalid("screening document has an unexpected key set")
    if value["schema_version"] != SCREENING_SCHEMA:
        raise _invalid("screening document schema_version is incompatible")
    if value["attestation"] != SCREENING_ATTESTATION:
        raise _invalid(
            f"screening attestation must be {SCREENING_ATTESTATION!r}"
        )

    coverage = _require_number(value["coverage"], "coverage")
    if not 0.0 < coverage < 1.0:
        raise _invalid("coverage must lie strictly between 0 and 1")

    domain = value["domain"]
    if not isinstance(domain, dict) or not domain:
        # An empty envelope is a claim to cover everything, which is exactly
        # the confident extrapolation the domain gate exists to refuse.
        raise _invalid("domain envelope must be a nonempty object")

    return SurrogateScreening(
        screened_sha256=_require_digest(value["screened_sha256"], "screened_sha256"),
        model=MappingProxyType(_parse_model(value["model"])),
        coverage=coverage,
        calibration=MappingProxyType(_parse_calibration(value["calibration"])),
        domain=_freeze_mapping(domain),
        predictions=tuple(
            MappingProxyType(item) for item in _parse_predictions(value["predictions"])
        ),
        fields=tuple(
            MappingProxyType(item)
            for item in _parse_fields(value.get("fields", []))
        ),
        assumptions=_require_text_list(value["assumptions"], "assumptions"),
        limitations=_require_text_list(value["limitations"], "limitations"),
    )


__all__ = [
    "SCREENING_ATTESTATION",
    "SCREENING_SCHEMA",
    "SurrogateScreening",
    "parse_surrogate_screening",
]
