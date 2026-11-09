"""RD3: deterministic full outer join of RC1 (2025<->2026) and RM1 (2025<->Siemens).

No LLM call, no file access, never raises on malformed input (AGENTS.md
Validation Restraint) -- this is a pure join over two already-approved,
already-validated row lists. RC1's and RM1's decisions are not re-litigated
here; this module only combines them on the shared 2025-amended DR id and
derives `orphan_side` / `action_needed` from fields both already carry.

Join key: the 2025-amended DR id (`old_dr_id`). RC1 rows are fanned out one
join row per `old_dr_id` -- a Merged row with two old ids becomes two join
rows sharing the same `new_dr_ids`, so "every 2025 DR appears exactly once"
holds even for merges. An RC1 row with no old ids (title-only "new_only"
candidate, see candidate_mapper.py) becomes a single row with
`old_dr_id=""` and `orphan_side="no_2025_origin"`.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .rd3_result import RD3JoinResult, RD3JoinRow, RD3JoinSummary

# change_status values that represent an actual 2026 content/number/scope shift.
# "Retained" and "New" are deliberately excluded: Retained means nothing moved,
# New has no 2025 origin to have "changed" from.
CHANGED = {"Modified", "Merged", "Relocated", "Renumbered", "ApplicabilityChange", "Removed"}

# reported_status values that count as "Siemens already reports this DR".
# phase_in is included: Siemens built its phase-in plan against the 2025 text,
# so a 2026 change can affect that plan even though the DR isn't fully live yet.
REPORTED = {"reported", "partially_reported", "phase_in"}

# An omitted or unclear 2025 reporting status must be resolved before a
# reporting action can be finalized, regardless of whether the DR changed.
NEEDS_STATUS_REVIEW = {"omitted", "unclear"}


def _action_needed(orphan_side: str, change_status: str, reported_status: str) -> str:
    if orphan_side == "no_2025_origin":
        return "new_report_required"
    if reported_status in NEEDS_STATUS_REVIEW:
        return "status_review_required"
    reported = reported_status in REPORTED
    if orphan_side == "no_2026_counterpart" and reported:
        return "report_rewrite_required"
    if change_status == "Removed" and reported:
        return "report_rewrite_required"
    if change_status in CHANGED and reported:
        return "report_update_required"
    return "no_action"


def build_join(registry_id: str, rc1_rows: list[dict[str, Any]], rm1_rows: list[dict[str, Any]]) -> RD3JoinResult:
    rm1_by_dr_id = {row.get("dr_id"): row for row in rm1_rows}
    matched_dr_ids: set[str] = set()
    rows: list[RD3JoinRow] = []

    for rc1_row in rc1_rows:
        old_dr_ids = rc1_row.get("old_dr_ids") or []
        old_dr_titles = rc1_row.get("old_dr_titles") or []
        if not old_dr_ids:
            rows.append(_build_row(rc1_row, "", "", rm1_by_dr_id))
            continue
        for i, old_dr_id in enumerate(old_dr_ids):
            old_dr_title = old_dr_titles[i] if i < len(old_dr_titles) else ""
            matched_dr_ids.add(old_dr_id)
            rows.append(_build_row(rc1_row, old_dr_id, old_dr_title, rm1_by_dr_id))

    # RM1 rows with no RC1 counterpart at all -- not expected in current data,
    # a safety net rather than a guard (report-only, never raises).
    for dr_id, rm1_row in rm1_by_dr_id.items():
        if dr_id in matched_dr_ids:
            continue
        rows.append(_build_row({}, dr_id, rm1_row.get("dr_title", ""), rm1_by_dr_id))

    summary = _summarize(rows)
    return RD3JoinResult(registry_id=registry_id, rows=rows, summary=summary)


def _build_row(
    rc1_row: dict[str, Any], old_dr_id: str, old_dr_title: str, rm1_by_dr_id: dict[str, Any]
) -> RD3JoinRow:
    new_dr_ids = rc1_row.get("new_dr_ids") or []
    change_status = rc1_row.get("change_status", "")
    rm1_row = rm1_by_dr_id.get(old_dr_id, {})
    reported_status = rm1_row.get("reported_status", "")

    if not old_dr_id:
        orphan_side = "no_2025_origin"
    elif not new_dr_ids:
        orphan_side = "no_2026_counterpart"
    else:
        orphan_side = "none"

    return RD3JoinRow(
        standard=rc1_row.get("standard") or rm1_row.get("standard", ""),
        old_dr_id=old_dr_id,
        old_dr_title=old_dr_title or rm1_row.get("dr_title", ""),
        new_dr_ids=new_dr_ids,
        new_dr_titles=rc1_row.get("new_dr_titles") or [],
        change_status=change_status,
        change_description=rc1_row.get("change_description", ""),
        old_paragraph_refs=rc1_row.get("old_paragraph_refs") or [],
        new_paragraph_refs=rc1_row.get("new_paragraph_refs") or [],
        citations=rc1_row.get("citations") or [],
        mapping_uncertain=bool(rc1_row.get("mapping_uncertain", False)),
        reported_status=reported_status,
        report_section_refs=rm1_row.get("report_section_refs") or [],
        materiality_note=rm1_row.get("materiality_note", ""),
        omission_note=rm1_row.get("omission_note", ""),
        evidence_quotes=rm1_row.get("evidence_quotes") or [],
        orphan_side=orphan_side,
        action_needed=_action_needed(orphan_side, change_status, reported_status),
    )


def _summarize(rows: list[RD3JoinRow]) -> RD3JoinSummary:
    action_counts = Counter(row.action_needed for row in rows)
    return RD3JoinSummary(
        n_rows=len(rows),
        n_orphan_2026=sum(1 for row in rows if row.orphan_side == "no_2026_counterpart"),
        n_orphan_2025=sum(1 for row in rows if row.orphan_side == "no_2025_origin"),
        n_new_report_required=action_counts.get("new_report_required", 0),
        n_report_rewrite_required=action_counts.get("report_rewrite_required", 0),
        n_report_update_required=action_counts.get("report_update_required", 0),
        n_status_review_required=action_counts.get("status_review_required", 0),
        n_mapping_uncertain=sum(1 for row in rows if row.mapping_uncertain),
        change_status_counts=dict(Counter(row.change_status for row in rows)),
        reported_status_counts=dict(Counter(row.reported_status for row in rows)),
        action_needed_counts=dict(action_counts),
    )
