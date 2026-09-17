"""Typed R2 scope-review output."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


RequirementType = Literal[
    "obligation",
    "threshold",
    "deadline",
    "method",
    "verification",
    "responsible_party",
    "scope",
]


class Citation(BaseModel):
    source_ref: str
    source_file: str
    page: int = Field(ge=1)
    evidence_excerpt: str


class ScopedFinding(BaseModel):
    r2_finding_id: str = Field(pattern=r"^R2F-\d{3}$")
    statement: str = Field(min_length=1)
    assessment_boundary: str = Field(min_length=1)
    requirement_type: RequirementType
    r1_finding_refs: list[str] = Field(min_length=1)
    approved_field_ids: list[str] = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)


class ClassifiedFinding(BaseModel):
    r1_finding_id: str = Field(pattern=r"^RF-\d{3}$")
    reason: str = Field(min_length=1)
    related_field_ids: list[str] = Field(default_factory=list)


class R2ScopeProfile(BaseModel):
    schema_version: Literal["1"] = "1"
    registry_id: str
    summary: str = Field(min_length=1)
    in_scope_findings: list[ScopedFinding] = Field(default_factory=list)
    blocked_findings: list[ClassifiedFinding] = Field(default_factory=list)
    excluded_findings: list[ClassifiedFinding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "R2ScopeProfile":
        r2_ids = [item.r2_finding_id for item in self.in_scope_findings]
        if len(r2_ids) != len(set(r2_ids)):
            raise ValueError("R2 scoped finding IDs must be unique")
        for label, values in (
            ("blocked", [item.r1_finding_id for item in self.blocked_findings]),
            ("excluded", [item.r1_finding_id for item in self.excluded_findings]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"R2 {label} finding IDs must be unique")
        return self
