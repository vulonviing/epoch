# rd3_join/ — deterministic RC1/RM1 join for UC4

RD3 is a Deterministic agent (AGENTS.md Agent Type Classification): no LLM call,
no file access, pure combination of two already human-approved row lists on their
shared join key, the 2025-amended Disclosure Requirement id.

## What it does

`build_join(registry_id, rc1_rows, rm1_rows)` performs a full outer join:

- **2025 <-> 2026** comes from RC1's rows. RC1 rows are fanned out one join row
  per `old_dr_id` (a Merged row with two old ids becomes two join rows sharing
  the same `new_dr_ids`), so every 2025 DR appears exactly once even across a
  merge. An RC1 row with no old ids (`candidate_mapper.py`'s `new_only` branch)
  becomes one row with `old_dr_id=""` and `orphan_side="no_2025_origin"`.
- **2025 <-> Siemens** comes from RM1's rows, matched on `dr_id == old_dr_id`.

RM1 is not joined to RC1 upstream of this module — see `AGENTS.md`'s UC4 section
for why RM1 answers "did Siemens report this 2025 DR" independently of whether
that DR later changed in 2026.

## `action_needed`

Derived, not asked of an LLM — RC1's `change_status` and RM1's `reported_status`
already contain the facts:

| `action_needed` | Rule |
|---|---|
| `new_report_required` | `orphan_side == "no_2025_origin"` |
| `status_review_required` | old-origin row with `reported_status` in `NEEDS_STATUS_REVIEW` |
| `report_rewrite_required` | (`orphan_side == "no_2026_counterpart"` or `change_status == "Removed"`) and reported |
| `report_update_required` | `change_status` in `CHANGED` and reported |
| `no_action` | otherwise |

Three fixed sets in `rd3_join.py` decide "changed", "reported", and "status review":

- `CHANGED = {Modified, Merged, Relocated, Renumbered, ApplicabilityChange, Removed}`
  — excludes `Retained` (nothing moved) and `New` (no 2025 origin to change from).
- `REPORTED = {reported, partially_reported, phase_in}` — includes `phase_in`
  because Siemens built its phase-in plan against the 2025 text, so a 2026 change
  can affect that plan even before the DR is fully live. Excludes `omitted`,
  `not_material`, `not_applicable`, `unclear`.

- `NEEDS_STATUS_REVIEW = {omitted, unclear}` applies to every old-origin row,
  including `Retained`, because the reporting status must be resolved before a
  final report action can be assigned. `not_material` and `not_applicable` remain
  outside both sets and therefore keep `no_action`.

Downstream agents (CP1, P4/P5/P6, F2) consume `action_needed` as a fixed input
fact and never re-derive or re-classify it — see AGENTS.md's Agent Type
Classification and Validation Restraint rules.

## What this package does NOT do

- It does not decide `change_status` or `reported_status` — those are RC1's and
  RM1's job, already locked by the human approval gate this module runs before.
- It does not call an LLM, read a file, or raise on malformed rows (report-only
  per AGENTS.md Validation Restraint).
