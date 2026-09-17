"""RM1.1 output schema -- blind DR-to-report-section matching.

RM1.1 is a pure LLM agent (AGENTS.md Agent Type Classification): it reads only
the 2025-amended DR previews and the full Siemens report section text, with
no visibility into the deterministic ESRS-index candidate lookup. Its matches
are a non-binding proposal that RM1.2 reconciles against the deterministic
index and the full report text. Pydantic validates structure and type only --
`basis` free text is the LLM's content and is never checked against expected
values.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BlindExposureMatch(BaseModel):
    """One row: RM1.1's own reading of which report sections address a DR."""

    standard: str
    dr_id: str
    candidate_section_nos: list[str] = Field(default_factory=list)
    basis: str
    match_uncertain: bool = False
    confidence: float = 0.5


class RM11Result(BaseModel):
    registry_id: str
    rows: list[BlindExposureMatch]
