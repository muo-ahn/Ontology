"""Validation helpers enforcing the required finding schema before graph upserts."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

__all__ = ["FindingSchema", "FindingValidationError", "validate_findings_payload"]


class FindingValidationError(ValueError):
    """Raised when a finding payload fails schema validation."""

    def __init__(self, index: int, errors: List[Dict[str, Any]]) -> None:
        detail = {"stage": "normalize", "msg": "finding_validation_failed", "index": index, "errors": errors}
        super().__init__(f"finding_validation_failed at index={index}")
        self.detail = detail


class FindingSchema(BaseModel):
    """Pydantic schema enforcing required fields and coercions."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str
    type: str
    location: str
    conf: float = Field(description="Model confidence score")
    size_cm: Optional[float] = Field(default=None, description="Optional lesion size in centimetres")

    @field_validator("id", "type", "location")
    @classmethod
    def _require_non_empty(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("cannot be blank")
        return cleaned

    @field_validator("conf")
    @classmethod
    def _coerce_conf(cls, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError("conf must be a float") from None

    @field_validator("size_cm")
    @classmethod
    def _coerce_size(cls, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError("size_cm must be a float") from None


def validate_findings_payload(findings: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate and normalise a sequence of finding payloads."""

    validated: List[Dict[str, Any]] = []
    for idx, finding in enumerate(findings or []):
        try:
            schema = FindingSchema.model_validate(finding)
        except ValidationError as exc:  # pragma: no cover - pydantic supplies detail
            raise FindingValidationError(index=idx, errors=exc.errors()) from exc
        validated.append(schema.model_dump())
    return validated

