# Development Log

A weekly-summary overview of the development process behind EPOCH, condensed
from a much more granular internal step log. This is a curated digest, not a
verbatim record.

## 2026-06-08 – 2026-06-14

- Reorganized the repository into a dated, documented project structure and
  introduced `AGENTS.md` as the shared, tool-independent instruction source.
- Refactored the agent codebase into a self-contained, agent-per-folder
  layout (each agent owns its own prompt, engine, and README).
- Implemented the core deterministic and human-gated pipeline: R1 (regulation
  discovery), DA1 (data mapping), R2 (scope review), D1 (deterministic data
  execution), D2 (data-quality preflight), and CP1 (case profiling), wired
  behind a two-gate human-approval CLI.
- Ran an early topology-selector stress test across several use cases,
  diagnosed and fixed an over-escalation bug in the selector's decision logic.
- Built a colloquium presentation walking through one use case's pipeline
  end to end, including its provenance chain.

## 2026-06-15 – 2026-06-21

- Added a second, single-year variant of an existing use case and fixed a
  chain of related data-quality-preflight bugs it exposed (empty-filter
  handling, qualified vs. bare column resolution, prompt-template artifacts
  being treated as real requirements).
- Added a plain-language "what this means" legend to every pipeline stage's
  CLI output, and made it a standing rule for new agents.
- Upgraded the internal runtime data catalog to a richer, backward-compatible
  schema (structured relationships, lineage, quality-check metadata) shared
  across the three agents that consume it.
- Migrated the LLM backend to a modular provider architecture supporting an
  Azure-hosted Anthropic endpoint alongside the original provider, with
  Azure AD authentication, empty-response handling, and automatic retry.
- Traced and fixed several root causes surfaced by the new use case: a data
  mapping decision that over-constrained a downstream statutory-average
  requirement, a case-profiling signal/schema mismatch, and a data-window
  clamping edge case now surfaced as an explicit limitation.
- Began literature grounding for the thesis's research-question framing.

## 2026-06-22 – 2026-06-28

- Extended the multi-backend LLM infrastructure to four named presets across
  two providers, with Azure AD authentication required by the organization's
  policy on all of them.
- Built a model-comparison evaluation framework from scratch: gold scope
  definitions, a benchmark runner, and an LLM-as-judge scoring protocol with
  a calibration gate and anchor answer keys.
- Ran a 320-run benchmark (4 use cases × 4 models × 20 runs) across the first
  two pipeline stages, then scored three of the four models with the judge
  protocol (the fourth was excluded for structural reliability issues) and
  wrote per-use-case and cross-model comparison reports.
- Scaffolded the next benchmark layer (the scope-review stage) for all four
  use cases, pending human review of its draft gold.
- Removed legacy code no longer part of the active surface (an old demo
  entry point, an executable selector/orchestrator prototype, and unused
  downstream-agent stubs), narrowing the active pipeline to its current
  human-gated stages.
- Iterated the colloquium presentation and thesis exposé: research-question
  framing, literature grounding for the measurement and topology-selection
  claims, and several rounds of content and typography passes on the exposé
  PDF.

## 2026-06-29 – 2026-07-05

- Formalized the project's core design philosophy: three topologies (Direct,
  Debate, Coalition), an assurance-artifact axis, and the rule that the two
  human approval gates form an "isolated memory" that is the sole source of
  truth for every downstream stage — propagated across the relevant agents
  and into the standing project rules.
- Connected the pipeline to the organization's live data source for the
  first time, validating joins across its site, region, and business-unit
  dimension tables end to end; briefly moved to a real-data-only
  configuration (later reintroduced a synthetic mode).
- Split the registry's "scope" and "demand" concepts and tightened what each
  downstream agent is allowed to see, closing several boundary leaks where
  raw registry content or evaluation-only fields reached an agent that
  should only see the human-approved result.
- Built and integrated the topology selector (TS1): a closed three-topology
  library, a discriminator-based selection agent, and a human-gated CLI
  step, plus several rounds of aligning its selection logic with the case
  profiler's signals.
- Added the "Design Philosophy: Minimal Validation" rule and removed about a
  dozen defensive checks across three agents that duplicated what the
  Pydantic schema already enforced.
- Fixed a cluster of data-scoping and filter bugs (a regional composite
  filter, a business-division code, a country-join mismatch) found while
  reshaping two use cases so their registered requests would reliably
  produce the intended topology choice.
- Did a large repository cleanup pass: pruned stale planning/audit archives,
  removed dead code left over from an earlier architecture, and rebuilt the
  project's internal knowledge-graph tooling.

## 2026-07-06 – 2026-07-12

- Built D3, the deterministic (non-LLM) computation agent that runs after
  topology selection, with three interchangeable calculation recipes:
  threshold comparison, dual-method reconciliation, and per-division
  consolidation.
- Found and fixed a fiscal-year windowing bug that had been misclassifying
  a handful of real sites as compliant when they were not.
- Refreshed the real-data connection for the new fiscal year and resolved a
  source-schema change on the vendor side.
- Started writing the thesis itself: LaTeX skeleton and the full Theory
  chapter, plus a progress audit mapping remaining sections to
  implementation status.
- Corrected the Direct topology's definition from a single-agent result to
  a sequential, dependent chain, and built its first chain-lead agent
  (later renamed P1).
- Removed a redundant status signal that had been computed and re-derived
  in three different places across the pipeline.
- Started building a visual interface (FastAPI backend + a React frontend)
  as a companion to the CLI, including an interactive drag-and-drop board
  for reviewing the data-mapping step.

## 2026-07-13 – 2026-07-19

- Extended the Direct-chain interpretation agent with concurrent per-site
  batching and web-search grounding for location- and current-event-specific
  claims, and hardened it against malformed model output.
- Built the Debate topology's output chain (two independent interpretation
  reads plus a reconciler and memo composer), then unified both topologies'
  finalization agents into a single, topology-agnostic finalizer.
- Fixed a chain of count-consistency bugs by having the deterministic
  computation agent compute portfolio-level totals once and pass them
  through, rather than letting each downstream agent re-count independently.
- Renamed the output-stage agents to their current scheme (P1-P3 for
  single-owner interpretation, S1 for the Debate synthesizer) and documented
  the prompt-style contract between single-owner and convergence agents.
- Wrote an internal development report covering the agent library, topology
  library, and use cases built so far, ahead of a first outside expert
  review.

## 2026-07-20 – 2026-07-26

- Built the full synthetic-data generator suite (six tables, deterministic,
  validated statistically against the real data's structure and null-rate
  patterns) and added the data-mode switch that lets the pipeline run on
  either data source.
- Did a large viewer-interface overhaul: rebuilt every pipeline stage's
  screen to render only what the stage's own artifact actually contains,
  removing frontend-invented interpretation, cross-fetches from other
  stages, and fabricated content, then added interactive filtering and
  navigation on top of that faithful baseline.

## 2026-07-27 – 2026-08-02

- Continued the viewer overhaul: a Registry landing page, dedicated
  human-approval pages for each gate, and a plain-language explanation of
  each use case's underlying calculation.
- Retitled the thesis to its final title, "Audit-Aware Topology Selection
  for LLM Agents," and dropped the requirement to maintain a parallel
  Turkish translation of every section.
- Restructured the Methods chapter's section ordering and wrote the Stage 1
  methodology in full: new figures for the Stage 1 pipeline, the agent
  library's reuse philosophy, and the Direct topology; documented R1, the
  registry, DA1, R2, D1, and D2 each with a worked artifact excerpt moved
  into the appendix.

## 2026-08-17 – 2026-08-23

- Finalized the title page and did a hedging pass over the theory and
  methods chapters, softening several overconfident or legally imprecise
  claims (including an EU AI Act timeline correction) to accurately scoped
  phrasing.

## 2026-08-24 – 2026-08-30

- Built the document-family pipeline for the fourth use case, which reads
  public regulation text and Siemens' own published sustainability report
  instead of tabular data: deterministic parsing, a blind-pass matching
  design (two independent agents per comparison, reconciled by a third),
  and three persona-based readers feeding a final synthesis agent.
- Pulled and structured the underlying public source texts (EU sustainability
  reporting standards and their EFRAG comparison materials, and Siemens'
  published FY2025 sustainability statement) via deterministic, API-based
  extraction scripts.
- Ran nine rounds of an independent LLM-as-judge audit against this new
  pipeline, each round finding and then verifying the fix for a handful of
  defects (citation grounding, schema consistency, an inverted provenance
  reference, an internal consistency rule), reaching a 9/10 score by the
  final round.
- Extended the viewer to support this new pipeline family end to end.

## 2026-08-31 – 2026-09-06

- Added a pair of external-commentary agents that give the human reviewer a
  Siemens-independent, source-grounded second opinion at every approval gate
  across both pipeline families, wired through the backend, CLI, and viewer,
  and made mandatory by standing rule.
- Built an educational "Foundations" page in the viewer explaining the
  agent types, topologies, shelves, and isolated-memory architecture to a
  non-technical audience, through several rounds of illustration and
  wording refinement.
- Finalized the third topology (Coalition) end to end for its use case, with
  per-division isolated attestation converging into one synthesis.
- Built a gate-centered process for freezing evaluation "gold" packages from
  real pipeline runs, then ran repeated read-only audits against all four
  tabular use cases' outputs. The audits found and confirmed real defects
  (a stale-shelf data leak, a mismatched time window, a P1 caveat rule
  producing a false compliance-risk claim) that were fixed and re-verified
  before all four use cases' gold packages were approved and frozen.
- Wrote the thesis's CP1 and TS1 subsections and several new architecture
  figures (the document-pipeline chain, the Debate execution figure, a
  registry-relationship figure), retired the parallel Turkish thesis draft,
  and added the figure/table ordering rule that governs later thesis edits.

## 2026-09-07 – 2026-09-13

- Defined a five-test evaluation taxonomy (selection, process, fidelity,
  stability, negative control) and used it to score every use case's runs
  against frozen gold packages, twice: once before and once after fixing a
  bug where a stale record from an earlier run could leak into scoring.
- Reviewed the gold packages' actual content (not just their structure),
  found and fixed several self-contradicting fields, and re-froze all five
  as a second, human-and-judge-reviewed version.
- Wired full LLM-provenance recording (model, reasoning configuration, token
  usage) into every LLM and hybrid agent's shelf record, closed the
  remaining architecture-audit rules this enabled, and switched the default
  backend.
- Built a fillable, five-criterion peer-review PDF generator for external
  domain-expert review, through many rounds of layout, content, and
  provenance-disclosure refinement.
- Built an internal architecture-compliance audit tool checking the
  project's own standing rules against the actual codebase.
- Did an intensive, source-verified revision pass over the Theory and
  Methods chapters: grounding every claim against its cited paper's actual
  finding, replacing jargon with concrete explanations, and aligning
  terminology across the prose and its figures.

## 2026-09-14 – 2026-09-20

- Finished drafting the thesis: wrote the Introduction, Abstract, and
  Discussion/Conclusion chapters, then closed a long backlog of citation
  audits, dangling cross-references, and duplicate content flagged by a
  dedicated repeat-finder tool, and trimmed the body toward its target word
  count by relocating supporting detail into the appendix without cutting
  content.
- Wrote up the results of the peer-review evaluation, including an honest
  accounting of its lowest-scoring criterion and what the review data does
  and does not support.
- Masked and published the returned peer-review documents for the thesis
  appendix, fixed two rounds of incomplete masking found during that
  process (an internal system name and a site-name leak in the reviewer's
  own free-text comments), and anonymized reviewer identities to
  Reviewer A/B/C throughout the thesis text.
- Iteratively rewrote the Abstract and added an Acknowledgments chapter.
- Added several new architecture figures (a topology-scaling comparison, a
  five-shelf provenance-read diagram, a complete document-pipeline diagram)
  and fixed a rendering bug that had been silently breaking a margin
  annotation across an entire chapter section.
- Started this public code-repository release: created the empty public
  repository and began the file-by-file confidentiality audit and scrub
  that this log entry is itself part of.
- Added deterministic synthetic data and source-attributed official legal PDFs.
- Published disclosure-safe evaluation documentation and four masked gold packages.
- Withheld UC4 source-bearing artifacts and added automated release-boundary checks.
- Extended gold masking to data-derived numbers, catalog statistics, and private metadata identifiers.
- Scrubbed numeric prose, source-run identifiers, and overlapping synthetic site labels; re-audited history.
- Completed the CLI-only thesis release with indexed agents and packaged prompts.
- Reduced public gold to structure-only payloads and documented the withheld UC4 boundary.
- Published only the peer-review rubric and anonymous aggregate results.
- Added release, distribution, synthetic-integrity, and static architecture verification.
- Isolated CLI tests from local backend configuration for reproducible CI execution.
