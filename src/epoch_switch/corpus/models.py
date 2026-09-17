"""Shared data model for the UC4 document pipeline (ESRS 2025->2026 diff).

These are deterministic-extraction records, not LLM output.  Pydantic here
validates structure only -- there is no LLM content to police (AGENTS.md
Agent Type Classification: RD1/RD2 are Deterministic agents).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProvisionKind = Literal["objective", "dr_body", "sub_point", "application_requirement"]
Version = Literal["2025_amended", "2026"]


class Provision(BaseModel):
    """One numbered paragraph or sub-point inside a Disclosure Requirement."""

    standard: str          # e.g. "E1"
    version: Version
    dr_id: str             # e.g. "E1-5"; carries the DR this paragraph belongs to
    paragraph_id: str       # marker text as printed, e.g. "37. (c) i." or "AR 18"
    kind: ProvisionKind
    text: str
    source_file: str
    source_line: int


class DisclosureRequirement(BaseModel):
    """One Disclosure Requirement, with its body and application-requirement text."""

    standard: str
    version: Version
    dr_id: str
    dr_title: str
    paragraphs: list[Provision] = Field(default_factory=list)
    ar_paragraphs: list[Provision] = Field(default_factory=list)
    source_file: str


class ReportSection(BaseModel):
    """One heading-delimited section of the Siemens FY2025 Sustainability Statement."""

    topic: str              # e.g. "E1", "00_context"
    section_no: str         # e.g. "2.2.6"
    title: str
    text: str
    source_file: str


class HelperHint(BaseModel):
    """A candidate lead pulled from a non-authoritative comparison_helpers document.

    Never a classification basis on its own (regulations/README.md hard rule).
    ``baseline_mismatch`` is always True here because the helper's own baseline
    is 2023-enacted, not our 2025-amended authoritative baseline.
    """

    standard: str
    token: Literal["AMENDED", "DELETED", "MOVED", "MERGED", "NEW", "UNCHANGED"]
    text: str
    source_file: str
    baseline_mismatch: bool = True
    authoritative: bool = False


class CandidateMapping(BaseModel):
    """A candidate old-DR <-> new-DR pairing, before RC1's verification."""

    standard: str
    old_dr_ids: list[str] = Field(default_factory=list)
    new_dr_ids: list[str] = Field(default_factory=list)
    basis: str              # short human-readable reason (title overlap, hint, ...)
    score: float
    hints: list[HelperHint] = Field(default_factory=list)
