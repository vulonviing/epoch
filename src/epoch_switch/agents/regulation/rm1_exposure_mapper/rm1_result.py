"""RM1.2 output schema -- per-DR Siemens FY2025 exposure finding.

RM1.2 is a pure LLM agent (AGENTS.md Agent Type Classification): it reads two
non-binding proposals -- RM1.1's blind section match and the deterministic
ESRS-index candidate-section lookup -- plus the full topic report text, and
decides whether/how Siemens actually reported that requirement. Pydantic
validates structure and type only.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ReportedStatus = Literal[
    "reported", "partially_reported", "omitted",
    "not_material", "not_applicable", "phase_in", "unclear",
]


class ExposureRecord(BaseModel):
    """One row: did Siemens report the corresponding 2025-amended DR."""

    standard: str
    dr_id: str
    dr_title: str
    reported_status: ReportedStatus
    report_section_refs: list[str] = Field(default_factory=list)
    materiality_note: str = ""
    omission_note: str = ""
    evidence_quotes: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    blind_agreement: Literal["agree", "partial", "disagree"] = "agree"
    index_agreement: Literal["agree", "partial", "disagree", "no_candidate"] = "agree"
    divergence_note: str = ""


class RM1Result(BaseModel):
    registry_id: str
    rows: list[ExposureRecord]
