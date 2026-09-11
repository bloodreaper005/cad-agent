from __future__ import annotations

from typing import Any, Mapping


class GearSizingError(ValueError):
    """Reject a sizing request that violates the input contract.

    Engineering non-convergence is not an error: a drive that no module in the
    series can satisfy returns a rejected result carrying its full check ledger,
    because a failed attempt is evidence the design record should keep.
    """

    def __init__(
        self, code: str, message: str, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = dict(detail or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "GearSizingError/v1",
            "status": "blocked",
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }


__all__ = ["GearSizingError"]
