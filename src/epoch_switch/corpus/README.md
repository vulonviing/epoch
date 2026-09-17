# corpus/ — deterministic document extraction for UC4

This package is the RD1/RD2 layer of the UC4 (ESRS 2025->2026 impact) document
pipeline. Everything here is pure code — no LLM calls, no `agent_id`, no
shelf writes. It exists to turn Markdown regulation and report text into
structured candidates that the LLM agents (`RC1.2`, `RM1.2` in
`agents/regulation/`) can verify and classify.

RD2's candidates and index lookup are a **non-binding proposal for the X.2
agent only**. Each blind-pass pair's X.1 half (`RC1.1`, `RM1.1`) never
receives this package's output at all — it reads only RD1's authoritative
text and proposes its own matching independently, so X.2 can compare two
non-binding proposals against the authoritative text instead of anchoring on
RD2's grouping alone (see AGENTS.md's "Blind-pass agent pairs" section).

## Modules

- `corpus_bundle.py` — the single call site for this package. `extract_corpus`
  (RD1) parses every standard's 2025-amended and 2026-revised text, its helper
  hints, and the Siemens report once into a `CorpusBundle`; `map_candidates`
  (RD2) fills each standard's `candidates` list via `build_candidates`. RD1
  and RD2 are now visible, numbered steps in the UC4 pipeline (`Step 1/13`,
  `Step 2/13` in `regulation_cli.py`) — all four LLM agents (`RC1.1`, `RC1.2`,
  `RM1.1`, `RM1.2`) consume the resulting `CorpusBundle` instead of
  re-parsing the same files themselves, but `RC1.1`/`RM1.1` only read the
  DR/report text RD1 extracted, never RD2's `candidates` / `index_candidates`.
  RD1/RD2 are deterministic and reproducible from tracked files, so they
  write a run artifact only, no shelf and no human gate.
- `models.py` — the shared Pydantic record types: `Provision`,
  `DisclosureRequirement`, `ReportSection`, `HelperHint`, `CandidateMapping`.
- `esrs_parser.py` — `parse_standard(path, standard, version)` splits one ESRS
  standard Markdown file (`regulations/esrs/baseline_2025_amended/` or
  `regulations/esrs/2026/`) into its Disclosure Requirements, with each
  numbered paragraph and Application Requirement attached to the DR it
  belongs to (regardless of whether the source repeats the DR heading under
  a separate `## Appendix A`, as the 2025 files do).
- `report_parser.py` —
  - `parse_report(dir)` splits Siemens's FY2025 Sustainability Statement
    Markdown files (`data/siemens_sustainability_2025/`) into heading-numbered
    sections (e.g. "2.2.6 Metrics").
  - `parse_esrs_index(path)` extracts a **best-effort, high-recall / low-
    precision** DR-code -> candidate-section lookup from the ESRS index file.
    That file is documented as an irregular, non-reconstructable table
    (`data/siemens_sustainability_2025/README.md`) — treat every result as a
    lead for `RM1.2` to verify, never a confirmed mapping.
- `helper_hints.py` — `extract_hints(standard, path)` pulls
  `[DELETED]`/`[AMENDED]`/`[MOVED]`/`[MERGED]`/`[NEW]`/`[UNCHANGED]` tags out
  of the December-2025 Log of Amendments files. Every `HelperHint` carries
  `authoritative=False, baseline_mismatch=True` because those helper
  documents compare 2023-enacted text against the December-2025 Draft
  Amended text — a different pair than our authoritative
  2025-amended -> 2026-revised comparison (see `regulations/README.md`'s
  source-hierarchy rule). A hint is a lead, never a classification basis.
- `candidate_mapper.py` — `build_candidates(old_drs, new_drs, hints)` proposes
  old-DR <-> new-DR pairings by weighted title-token overlap (DR numbers
  shift between versions, so IDs cannot be trusted — see module docstring).
  Ambiguity is preserved on purpose: a candidate row can carry more than one
  `new_dr_ids` entry, or be empty, rather than forcing a false 1:1 pick.
  Boilerplate titles (e.g. "Anticipated financial effects from material
  X-related risks and opportunities", which collapses to a single generic
  topic word after stopword removal) are flagged explicitly rather than
  silently scored, since a bare topic word matches everything in the same
  standard. The "anticipated financial effects" phrase is retained as a
  structural title-family anchor: if the target version has no member of that
  family, the old DR receives an empty target candidate instead of unrelated
  Policies/Actions/Targets suggestions.

## What this package does NOT do

- It does not decide `change_status` (Removed/New/Modified/...). That is
  `RC1.2`'s job, reading the actual authoritative paragraph text.
- It does not decide whether Siemens reported a DR. That is `RM1.2`'s job.
- It never treats a helper hint or an index candidate as ground truth.

## Known limitations

- Title-only matching cannot always separate "genuinely removed" from
  "renamed beyond recognition" (e.g. 2025 E3-4 "Water consumption" -> 2026
  E3-4 "Water metrics" scores low on title alone). These land in the
  `"ambiguous title match"` bucket with a low score and multiple candidates —
  by design, not a bug — so RC1.2 reads the paragraph text instead of trusting
  the title score.
- `parse_esrs_index` cannot recover real DR-to-section alignment from the
  source table's broken layout; its candidate lists are a starting point for
  RM1.2's search, not a lookup table.
