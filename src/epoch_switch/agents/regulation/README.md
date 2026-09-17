# Regulation Agents

## Active Runtime Agents

**R1 - Active Regulation Reader** and **R2 - Regulation Scope Reviewer** are
implemented and registered.

R1 is hybrid:

1. `r1_document_loader.py` loads every non-empty page from the configured
   regulation PDFs.
2. The R1 LLM reads the complete document and returns a source-cited regulation
   profile with a `sufficient` or `insufficient` verdict.
3. Python validates the output schema and confirms that every citation points
   to one of the supplied document pages.
4. An `insufficient` verdict stops the pipeline before case profiling, data
   preparation, and topology selection.

One persistent profile is maintained per registry ID under the R1 agent folder.
It is reused only while the registry definition, PDFs, R1 prompt, model, loader,
and profile schema signature remain unchanged. Run-scoped copies are also
written to EvidenceStore for downstream consumers.

R2 runs after DA1 human approval. It receives the complete R1 profile and the
human-approved DA1 mapping scope, classifies every R1 finding as in-scope,
blocked, or excluded, and writes new scoped assessment statements for the
in-scope portion. Every R2 outcome is retained in the R2 agent-local archive,
while the approved current output lives in `active.json`.

## UC4 document chain

`uc4_esrs_2026_impact` runs a separate document-family chain instead of
DA1/D1/D2/D3 (see AGENTS.md's "UC4 — document pipeline" for the routing and
gate shape). This section covers the LLM/deterministic agents in that chain
that live under `agents/regulation/`; RD1/RD2 are covered in
`src/epoch_switch/corpus/README.md`, and P4/P5/P6, F2 in
`src/epoch_switch/agents/output/` (see their own module docstrings).

- **RC1.1 / RC1.2** (`rc1_change_classifier/`) are a blind-pass agent pair
  (LLM, wiring under AGENTS.md's "Blind-pass agent pairs") that together
  decide each candidate's regulatory `change_status` from the authoritative
  2025-amended and 2026-revised DR text. The December-2025 EFRAG comparison
  helpers compare a *different* version pair (2023-enacted vs. Draft Amended)
  and are leads, never evidence (see `regulations/README.md`'s
  source-hierarchy rule).
- **RM1.1 / RM1.2** (`rm1_exposure_mapper/`) are a blind-pass agent pair
  (LLM, same wiring) that together decide each 2025 DR's `reported_status` —
  whether and how Siemens reported it — from the DR text and Siemens's report
  sections, against the deterministic ESRS-index candidate-section lookup.
  RM1.1/RM1.2 do not consume RC1.1/RC1.2's output — Siemens' FY2025 report was
  written against the 2025 standard, so the 2025<->2026 classification adds
  nothing to "did Siemens report this 2025 DR", and chaining the two would
  serialize the stages and let RC1's verdicts anchor RM1's reading.
- **RD3** (Deterministic, `rd3_join/`) performs the full outer join of RC1.2's
  rows and RM1.2's rows on the 2025 DR id, producing one flat `RD3JoinRow` per
  2025 DR (fanned out for Merged, or for any duplicate `old_dr_id` RC1.2
  emits) plus one row per orphan 2026 DR with no 2025 origin. It derives
  `orphan_side` (`none` / `no_2026_counterpart` / `no_2025_origin`) and a fixed
  `action_needed` classification — `new_report_required`,
  `report_rewrite_required`, `report_update_required`, `status_review_required`,
  or `no_action` — from `change_status` and `reported_status` alone; no LLM
  call, no `raise`. Every old-origin row whose `reported_status` is `omitted`
  or `unclear` receives `status_review_required`. RD3 runs right after RM1.2
  and before the human gate, so the gate reviews the joined table, not four
  separate lists.
- **CP1** and **TS1** are reused as-is (same classes, adapted inputs). CP1
  reads RD3's join summary, not RC1.2/RM1.2 separately, and is prompted to
  treat orphan and action findings as the deliverable substance the registry
  asked for (compare old vs. new reporting, strengthen or produce a report)
  rather than a limitation.
- **P4/P5/P6** (`agents/output/persona_assessor/`) are three independent
  risk-posture reads of the same RD3 join table — Conservative, Balanced, and
  Maximum-Assurance. All three see the exact same join rows (no persona
  re-derives its own RC1/RM1 join) and only assign a persona-level
  `action_priority` (`required_urgent` / `required` / `not_required`) to
  RD3's fixed `action_needed`; they never reclassify `action_needed` itself.
  All three **always** run, regardless of which topology TS1 selects; TS1's
  choice is a measured signal about whether it recognized this "three
  independent reads + one synthesis" shape, not a switch that changes persona
  count. See `selection/stage2_binding.py`'s `DOCUMENT_TOPOLOGY_AGENTS`.
- **F2** reconciles the three persona verdicts into the final DR-level
  comparison table + business summary, surfacing both `recommended_action`
  and `action_priority` disagreement in `persona_divergence` rather than
  silently picking one persona, and reports its own reconciled
  `agreed_priority` per row.

## Future Concepts

The following identifiers are conceptual only. They have no runtime folders,
imports, registry entries, or selectable implementations:

- **R3**: specialized deadline interpretation.
- **R5**: specialized numeric value extraction.

Historical project documents may describe R2 as a specialized long-term target
reader. That concept is deprecated; the active R2 identity is Regulation Scope
Reviewer.
