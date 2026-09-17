"""RD3 join schema -- deterministic, no LLM call (AGENTS.md Agent Type Classification).

Pydantic here validates structure/type only, same discipline as RC1Result and
RM1Result -- see rc1_result.py / rm1_result.py module docstrings.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

OrphanSide = Literal["none", "no_2026_counterpart", "no_2025_origin"]
ActionNeeded = Literal[
    "new_report_required",
    "report_rewrite_required",
    "report_update_required",
    "status_review_required",
    "no_action",
]


class RD3JoinRow(BaseModel):
    """One 2025 Disclosure Requirement, its RC1 (2025<->2026) and RM1 (2025<->Siemens) findings joined."""

    standard: str
    # 2025 <-> 2026 side (RC1)
    old_dr_id: str = ""
    old_dr_title: str = ""
    new_dr_ids: list[str] = Field(default_factory=list)
    new_dr_titles: list[str] = Field(default_factory=list)
    change_status: str = ""
    change_description: str = ""
    old_paragraph_refs: list[str] = Field(default_factory=list)
    new_paragraph_refs: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    mapping_uncertain: bool = False
    # 2025 <-> Siemens side (RM1)
    reported_status: str = ""
    report_section_refs: list[str] = Field(default_factory=list)
    materiality_note: str = ""
    omission_note: str = ""
    evidence_quotes: list[str] = Field(default_factory=list)
    # join-derived
    orphan_side: OrphanSide = "none"
    action_needed: ActionNeeded = "no_action"


class RD3JoinSummary(BaseModel):
    n_rows: int = 0
    n_orphan_2026: int = 0
    n_orphan_2025: int = 0
    n_new_report_required: int = 0
    n_report_rewrite_required: int = 0
    n_report_update_required: int = 0
    n_status_review_required: int = 0
    n_mapping_uncertain: int = 0
    change_status_counts: dict[str, int] = Field(default_factory=dict)
    reported_status_counts: dict[str, int] = Field(default_factory=dict)
    action_needed_counts: dict[str, int] = Field(default_factory=dict)


class RD3JoinResult(BaseModel):
    registry_id: str
    rows: list[RD3JoinRow]
    summary: RD3JoinSummary
