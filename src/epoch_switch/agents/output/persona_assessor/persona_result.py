"""Persona assessor output schema -- shared by P4, P5, and P6.

Character (risk posture) is a prompt-only distinction (see P4.md/P5.md/P6.md);
all three personas share this exact schema so F2 compares structured fields,
not prose (AGENTS.md: no cross-agent content equality checks / LLM text is
never checked against expected values).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PersonaVerdict(BaseModel):
    """One persona's recommended action for one DR-level change+exposure finding."""

    character_id: str       # "P4" | "P5" | "P6"
    verdict_kind: Literal["primary", "supplemental"] = "primary"
    standard: str
    old_dr_ids: list[str] = Field(default_factory=list)
    new_dr_ids: list[str] = Field(default_factory=list)
    recommended_action: str
    effort_estimate: Literal["none", "low", "medium", "high"]
    effort_rationale: str = ""  # why this effort level -- kept separate from the enum
    risk_posture_note: str  # how this character's stance shaped the call
    rationale: str
    citations: list[str] = Field(default_factory=list)
    siemens_evidence_quotes: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    action_needed: str = ""  # carried from RD3's join row -- persona does not re-classify this
    action_priority: Literal["required_urgent", "required", "not_required"] = "required"


class PersonaResult(BaseModel):
    registry_id: str
    character_id: str
    rows: list[PersonaVerdict]
