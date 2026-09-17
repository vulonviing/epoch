"""F2 output schema -- the UC4 deliverable: DR-level comparison table + summary.

F2 is a Hybrid agent: an LLM role (S1/F1-equivalent convergence/composition
function, not a P/T-style single-owner analyst -- AGENTS.md "Role agents"
note) that reconciles three independent PersonaVerdict sets over RD3's
change/exposure join into one final table, plus a deterministic
`build_standard_summary()` rollup with no LLM involvement. Persona/priority
disagreement is surfaced in `persona_divergence`, never silently resolved by
picking one persona.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

_PRIORITY_RANK = {"required_urgent": 2, "required": 1, "not_required": 0}


class ImpactRow(BaseModel):
    """One row of the final DR-level comparison table (the plan's requested schema)."""

    standard: str
    dr_id_2025: str = ""
    requirement_2025: str = ""
    dr_id_2026: str = ""
    requirement_2026: str = ""
    change_status: str
    change_description: str
    source_2025: list[str] = Field(default_factory=list)
    source_2026: list[str] = Field(default_factory=list)
    siemens_reported: str = ""       # RM1's reported_status, carried through
    siemens_source_ref: list[str] = Field(default_factory=list)
    siemens_impact: str = ""         # F2's own reconciled-impact statement
    confidence: float = Field(ge=0, le=1)
    confidence_note: str = Field(min_length=1)
    action_needed: str = ""      # carried from RD3's join row, not re-derived
    agreed_priority: str = ""    # F2's reconciled read of the three personas' action_priority
    agreed_effort: Literal["none", "low", "medium", "high"] = "none"
    effort_note: str = ""        # F2's reconciled read of the three personas' effort_rationale


class StandardImpactSummary(BaseModel):
    """Deterministic per-standard rollup of the final table (build_standard_summary)."""

    standard: str
    change_required: bool
    n_rows_action_required: int
    n_rows_total: int
    highest_priority: str = ""   # required_urgent | required | not_required | ""


class ESRSImpactReport(BaseModel):
    registry_id: str
    rows: list[ImpactRow]
    executive_summary: list[str] = Field(default_factory=list)
    top_impacts: list[str] = Field(default_factory=list)
    persona_divergence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    standard_summary: list[StandardImpactSummary] = Field(default_factory=list)


def build_standard_summary(rows: list[ImpactRow]) -> list[StandardImpactSummary]:
    """Deterministic per-standard rollup of the final table. No LLM.

    Groups rows by `standard` (alphabetical). `action_required` is
    `action_needed != "no_action"`. `highest_priority` is the highest of
    `required_urgent > required > not_required` among action-required rows
    only (empty string when no row in that standard needs action).
    """
    by_standard: dict[str, list[ImpactRow]] = {}
    for row in rows:
        by_standard.setdefault(row.standard, []).append(row)

    summaries: list[StandardImpactSummary] = []
    for standard in sorted(by_standard):
        std_rows = by_standard[standard]
        action_rows = [r for r in std_rows if r.action_needed != "no_action"]
        highest_priority = ""
        if action_rows:
            highest_priority = max(
                (r.agreed_priority for r in action_rows),
                key=lambda p: _PRIORITY_RANK.get(p, -1),
            )
        summaries.append(
            StandardImpactSummary(
                standard=standard,
                change_required=len(action_rows) > 0,
                n_rows_action_required=len(action_rows),
                n_rows_total=len(std_rows),
                highest_priority=highest_priority,
            )
        )
    return summaries
