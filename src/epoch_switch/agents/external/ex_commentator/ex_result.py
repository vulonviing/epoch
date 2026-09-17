"""EX1/EX2 output schema -- shared by both stage instances.

EX1 and EX2 differ only in prompt framing (EX1.md / EX2.md) and in which
gate payload they read, not in output shape, so they share this schema --
same reasoning as persona_assessor's PersonaVerdict (AGENTS.md: no
cross-agent content-equality checks; structured fields, not prose).

Non-binding by construction: this schema carries no field that could be
mistaken for a decision (no approve/reject, no status override). It is pure
commentary plus source attribution.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CommentarySource(BaseModel):
    institution: str
    url: str
    title: str
    published: str | None = None


class CommentaryBullet(BaseModel):
    point: str
    stance: Literal["supports", "challenges", "adds_context", "flags_gap"]
    relevance: Literal["high", "medium", "low"]
    grounding: Literal["web_verified", "model_knowledge"]
    sources: list[CommentarySource] = Field(default_factory=list)


class ExternalCommentary(BaseModel):
    registry_id: str
    stage_id: Literal["EX1", "EX2"]
    bullets: list[CommentaryBullet] = Field(min_length=5, max_length=15)
    institutions_consulted: list[str] = Field(default_factory=list)
    search_notes: list[str] = Field(default_factory=list)
