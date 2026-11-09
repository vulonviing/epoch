"""RC1.2 output schema — per-DR regulatory-change classification.

RC1.2 is a pure LLM agent (AGENTS.md Agent Type Classification): it reads two
non-binding proposals -- RC1.1's blind match and RD2's deterministic
candidates -- plus the authoritative baseline/target paragraph text, and
decides the final classification. Pydantic validates structure and type only
-- `change_description` and `rationale`-shaped free text are the LLM's
content and are never checked against expected values.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChangeStatus = Literal[
    "Removed", "New", "Modified", "Merged", "Relocated",
    "Renumbered", "Retained", "ApplicabilityChange",
]


class ChangeClassification(BaseModel):
    """One row: how one 2025-amended DR relates to the 2026-revised standard."""

    standard: str
    old_dr_ids: list[str] = Field(default_factory=list)
    old_dr_titles: list[str] = Field(default_factory=list)
    new_dr_ids: list[str] = Field(default_factory=list)
    new_dr_titles: list[str] = Field(default_factory=list)
    change_status: ChangeStatus
    change_description: str
    old_paragraph_refs: list[str] = Field(default_factory=list)
    new_paragraph_refs: list[str] = Field(default_factory=list)
    mapping_uncertain: bool = False
    citations: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    blind_agreement: Literal["agree", "partial", "disagree"] = "agree"
    deterministic_agreement: Literal["agree", "partial", "disagree", "no_candidate"] = "agree"
    divergence_note: str = ""


class RC1Result(BaseModel):
    registry_id: str
    rows: list[ChangeClassification]
