"""RC1.1 output schema -- blind old<->new DR matching.

RC1.1 is a pure LLM agent (AGENTS.md Agent Type Classification): it reads only
the authoritative full-text DR payloads for both ESRS versions and proposes
its own matching, with no visibility into RD2's deterministic candidates. Its
matches are a non-binding proposal that RC1.2 reconciles against RD2's
proposal and the authoritative text. Pydantic validates structure and type
only -- `basis` free text is the LLM's content and is never checked against
expected values.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BlindMatch(BaseModel):
    """One row: RC1.1's own reading of how one DR relates across versions."""

    standard: str
    old_dr_id: str = ""  # "" if this is a 2026 DR with no 2025 counterpart
    new_dr_ids: list[str] = Field(default_factory=list)
    basis: str
    match_uncertain: bool = False
    confidence: float = 0.5


class RC11Result(BaseModel):
    registry_id: str
    rows: list[BlindMatch]
