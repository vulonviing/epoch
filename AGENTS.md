# EPOCH Thesis — Agent Instructions

## Current Project State

The current implementation target is the human-reviewed regulation CLI:

```text
epoch-regulation:
R1 -> DA1 -> human -> R2 -> human -> D1 -> D2 -> derived_case_facts -> CP1 -> human
```

This foundation feeds the completed topology-selection and output pipeline.
Executable topology selection and downstream output construction are live: TS1 selects
among Direct/Debate/Coalition, and `selection/stage2_binding.py`'s `TOPOLOGY_AGENTS`
(`Direct: [D3, P1, F1]`, `Debate: [D3, [P2, P3], S1, F1]`, `Coalition: [D3, C1, C2, F1]`)
and `DOCUMENT_TOPOLOGY_AGENTS` (`[[P4, P5, P6], F2]` for all three) are actually invoked
from `regulation_cli.py` — they are not thesis/evaluation scaffold.

The current research framing is:

```text
pi(case, output_profile, assurance_profile) -> topology
```

Old proposal, planning, and 26/27-agent topology-library descriptions are
historical context. Use local README files for detailed folder-specific status.

## Data Convention

Tabular use cases (UC1–UC3) run against `EPOCH_DATA_MODE`-selected data;
document-family use cases (UC4 and later) read only public regulation/report
text regardless of data mode.

| Path | Content | Git |
|------|---------|-----|
| `data/synthetic/` | Fully synthetic tabular dataset, same schema as the tabular pipeline's source data | yes, committed (default mode) |
| `data/real/` | Your own real data, if you have any to point the pipeline at | no, gitignored, not shipped |
| `experiments/<EPOCH_DATA_MODE>/` | Tabular-pipeline dated run artifacts | no, gitignored |
| `experiments/public/` | Document-pipeline dated run artifacts | no, gitignored in this release |
| `public/gold/` | Curated structure-only evaluation disclosures | yes, committed |

Rules:

- Every local run directory is dated, contains a README, and is never overwritten.
- Never hardcode absolute data paths.
- All data access goes through `epoch_switch.config.DATA_DIR`
  (`data/<EPOCH_DATA_MODE>/`, `synthetic` by default).
- Anything under `*/real/` is gitignored and never shipped in this
  repository.
- Runtime runs are never committed from this public repository; publication
  happens only through the staged `public/gold/` builder and verifier.
- `config.create_experiment_dir(..., visibility=...)` is the single place that
  chooses the run root: `visibility="public"` for `pipeline_family=="document"`
  use cases, `"real"` (default) for everything else — despite the name, this
  routes to `experiments/<EPOCH_DATA_MODE>/`, not literally `experiments/real/`.
  Do not add a second routing point or hardcode a use-case id to decide the
  root.

## Workflow Logging

Notable development steps for this repository are logged under `ai_usage/`.
Keep entries short — a title and a few bullet points per step — rather than
reproducing full conversation transcripts.

## Primary Development Pipeline

- `epoch-regulation` / `python -m epoch_switch.regulation_cli` is the primary
  development interface.
- The older `epoch run` entry point has been removed. Do not reintroduce it for
  registry, regulation-field discovery, profile, or evaluation work.
- Registry definitions contain the user request and scope, not a hardcoded answer
  key of required data fields.
- R1 discovers candidate operational fields from the registry and regulation.
- DA1 compares those fields with the runtime data catalog.
- Human approval makes the DA1 handoff final.
- R2 is the regulatory boundary for downstream agents.
- D1 executes the approved handoff deterministically.
- D2 performs data-quality preflight before derived facts and CP1.
- CP1 produces the case profile artifact that can anchor future
  topology-selection evaluation.

### UC4 — document pipeline (content, not CSV)

`uc4_esrs_2026_impact` (registry.py) evaluates the 2025-amended -> 2026-revised
ESRS E1-E5 impact on Siemens' FY2025 Sustainability Statement. Its data is
Markdown regulation/report text, not tabular rows, so it does **not** go
through DA1/D1/D2/D3 — those agents assume a CSV catalog
(`UsecaseSeed.pipeline_family == "tabular"`, the default for UC1-UC3).
UC4 sets `pipeline_family="document"` and runs a parallel chain instead:

```text
RD1 -> RD2 -> RC1.1 -> RC1.2 -> RM1.1 -> RM1.2 -> RD3 -> human -> CP1 -> TS1 -> P4/P5/P6 -> F2 -> human
```

The chain is RD1/RD2 (deterministic parsing, `src/epoch_switch/corpus/README.md`)
feeding two blind-pass agent pairs — RC1.1/RC1.2 (regulatory `change_status`)
and RM1.1/RM1.2 (Siemens's `reported_status`) — into deterministic RD3's join,
then CP1/TS1 reused as-is, then P4/P5/P6's three persona reads, then F2's
reconciliation. Full per-agent detail lives in
`src/epoch_switch/agents/regulation/README.md`'s "UC4 document chain" section;
the wiring shared by both blind-pass pairs is "Blind-pass agent pairs" below.

- The tabular pipeline (`_run_post_r2_tail`, `TOPOLOGY_AGENTS`) is untouched by
  this addition; `build_document_pipeline` in `regulation_cli.py` is a
  separate function reached by an early branch in `build_profile_pipeline`.
- UC4 runs write locally to `experiments/public/`, not `experiments/<EPOCH_DATA_MODE>/`,
  because no stage in `build_document_pipeline` touches `DATA_DIR` — see Data
  Convention.

## Agent Type Classification

Every agent in this pipeline is exactly one of three types. The type determines
what validation and checking code is appropriate for that agent's outputs.

| Type | Description | Examples |
|------|-------------|---------|
| **LLM** | Pure language model reasoning. Output is validated by Pydantic schema only — no code-level business-logic checks on the *content* of LLM-generated fields. Trust the model to reason correctly within its validated schema. | R1, DA1, R2, D2, CP1, TS1, P4, P5, P6, RC1.1, RC1.2, RM1.1, RM1.2, EX1, EX2 |
| **Deterministic** | No LLM. Pure code logic. Output is always reproducible and auditable. | D1, DataQualityEngine (D2 sub-component), `corpus/` (RD1/RD2), RD3 |
| **Hybrid** | Orchestration layer that calls both LLM and deterministic sub-components. The LLM part follows LLM rules; the deterministic part follows deterministic rules. | D2 (LLM planning + deterministic engine + LLM verdict), F2 (LLM report generation + deterministic `standard_summary` rollup) |

### Blind-pass agent pairs (X.1 / X.2)

`RC1.1/RC1.2` and `RM1.1/RM1.2` are **blind-pass agent pairs** — both agents
in a pair are pure LLM agents (no deterministic sub-component of their own),
but they are wired so the first (X.1) never sees the deterministic candidate
proposal that the second (X.2) uses, removing anchoring bias:

- **X.1** (`RC1.1`, `RM1.1`) receives only RD1's authoritative text and
  proposes its own matching independently. Its payload physically contains no
  deterministic-candidate keys (`candidates`, `basis`, `title_match_score`,
  `non_authoritative_hints`, `index_candidate_sections`,
  `deterministic_index_candidates`) — this is enforced by what the payload
  builder puts in the dict, not by a prompt instruction. X.1 never assigns
  the pair's final verdict field (`change_status` / `reported_status`).
- **X.2** (`RC1.2`, `RM1.2`) receives X.1's blind rows *and* RD2's
  deterministic candidates side by side, plus the same authoritative text,
  and makes the binding decision. Neither proposal is treated as ground
  truth; the authoritative text can override either or both.
- Both halves of a pair share one shelf folder for code organization, but
  X.1 gets its own shelf directory (`rc1_1_registry_profiles/`,
  `rm1_1_registry_profiles/`) distinct from X.2's
  (`registry_profiles/<registry_id>/`), following the agent-local-shelves
  rule below.
- X.1 is an intermediate step in the topology-final-agent-gate sense: it does
  not open its own human gate. The human gate for this stretch of the
  pipeline is one atomic decision over all five document-matching shelves
  (`rc1_1`, `rc1_2`, `rm1_1`, `rm1_2`, `rd3`), but the gate *payload* shown to
  the human contains only `rc1_2`, `rm1_2`, and `rd3` — X.1's rows are
  written to their shelf for audit, not displayed at the gate.

Hard rules:
- An LLM agent's text fields belong to the LLM; Pydantic validates structure and
  type, not content equality. The prohibitions this implies — no verbatim-
  reproduction checks, no cross-agent content checks — are stated once under
  "Design Philosophy: Minimal Validation" below. If two agents must share a value exactly, pass it
  through code; do not ask the LLM to copy it and then verify the copy.
- Deterministic agents must never call an LLM.
- LLM agents must never embed hard-coded lookup tables that replicate catalog or
  registry data already available in the pipeline.

## LLM Provenance and Reasoning Configuration (hard rule)

Every agent classified **LLM** or **Hybrid** in the Agent Type Classification table
above must record, in its own shelf record, which LLM produced the output, whether
reasoning was enabled, at what effort, and how many tokens the call used. No
exceptions — DA1 is included, and EX1/EX2 are included. This is metadata about the
call, not content: it never enters another agent's input, so it does not weaken
EX1/EX2's advisory-only rule or the isolated-memory boundary.

The record lives at the **top level of the shelf record, sibling to `payload`**
(`llm_provenance`), never nested inside `payload` — nesting it inside `payload`
would change `payload_sha256` and let it flow into whatever consumes that agent's
payload downstream, which the post-R2 data boundary forbids.

Requirements:
- **Model identity.** `provider`, `backend_preset`, and the `model` string actually
  sent on the wire — not the configured default, what the request carried.
- **Reasoning must be on and explicit.** `reasoning.enabled` must be `true` and
  `reasoning.effort` must be a concrete value. **`none`, `null`, and an empty string
  are never acceptable.** A backend preset that cannot express an effort level is not
  a valid backend for an LLM agent.
- **Token accounting, read from the provider's own usage object, never estimated.**
  Recorded split (`input`/`output`) when the provider reports them separately,
  combined (`total`) when it does not, with `reported_as` naming which.
- Deterministic agents (D1, D3, RD3) record `"llm_provenance": null` — explicit
  null, not a missing key, so "no LLM was involved" is itself on the record. Hybrid
  agents (D2, F2) record only their LLM half; the deterministic sub-component
  contributes nothing to this block.

This is provider-specific — every API spells these concerns differently. See
`.agents/llm-backend-reference.md` for the record shape and the wire-level
field mapping per provider (`max_tokens` vs `max_completion_tokens`, the
`thinking`/`reasoning_effort` switch, token-usage field names).

**Mandatory for new use cases.** Any new agent classified LLM or Hybrid must
record `llm_provenance` from its first run.

This section is implemented: every LLM/Hybrid agent builds a `ProvenanceCollector`
(`core/llm_provenance.py`) around its `complete_json`/`complete_json_websearch`
calls and exposes the result as `self.last_llm_provenance`; `regulation_cli.py`
passes it into that agent's `promote_active`/`archive_candidate` call as the
top-level `llm_provenance` kwarg. `/system-check`'s R22–R24 hold the measured
implementation status.

## Person (P) vs Coalition prompt-style contract

Output-stage agents use one of two prompt styles depending on their topology
role — Person (single-owner, isolated scope: P1/P2/P3/P4-P6) or Coalition
(per-division isolated calls + convergence: C1/C2). This contract governs how
prompt `.md` files are written; it does not affect `BaseAgent` — there is only
one base class. Full contract, section layout, and framing markers:
`src/epoch_switch/agents/output/README.md`.

Coalition (per-division isolated calls + convergence: C1/C2) is best
understood as a set of small, independent debate-like islands rather than
one big committee. **C1** fans out into one isolated call per division found
in D3's data — the division count is data-driven, not fixed in code — and
each call is a self-contained read: no division sees another's figures or
attests to them. **C2** then does not re-run any division's analysis; it is
the single point that sees every C1 output side by side and produces the
synthesis of those independent syntheses — structurally the same convergence
role S1 plays in the Debate chain, but over N division-owned results instead
of two rival reads.

## External commentary agents (EX1/EX2)

`src/epoch_switch/agents/external/ex_commentator/` — one class,
`ExternalCommentatorAgent`, two instances (`EX1`, `EX2`), following the same
"one class, N `agent_id` instances" pattern as `PersonaAssessorAgent`
(P4/P5/P6). EX1 and EX2 give the human reviewer, at every human gate in every
use case, a Siemens-external, source-grounded second read of the exact payload
about to be approved — drawn only from a fixed allowlist of official
institutions (`ex_sources.py`), never from open web search.

Gate map (both pipeline families, always both stages):

| Pipeline | Stage-1 gate (EX1 runs immediately before) | Stage-2 gate (EX2 runs immediately before) |
|---|---|---|
| Tabular (UC1/UC2/UC2.1/UC3) | R2 | F1 |
| Document (UC4) | RC1.2/RM1.2/RD3 atomic mapping gate | F2 |

Hard rules:
- **Advisory only, never binding.** EX1/EX2 output must never feed any
  downstream agent, must never be written into another agent's shelf record,
  and must never change the isolated memory. It is added to the gate *payload
  dict* the human reviewer sees (under `"ex1"`/`"ex2"`) via a separately-named
  local variable (for example `r2_gate_payload = {**r2_profile, "ex1": ...}`)
  — the agent's own payload variable (`r2_profile`, `f1_payload`, ...) is
  never mutated, so nothing promoted to that agent's shelf ever contains EX
  content.
- EX1/EX2 run unconditionally, including in `--auto` mode — they have no gate
  of their own to skip.
- If the active LLM backend does not implement web search
  (`call_json_websearch`, only `foundry_anthropic` today), the CLI catches the
  resulting exception, prints a yellow "EX skipped" panel, and continues the
  pipeline — EX is never allowed to crash a run.
- `ex_sources.py` is the single source of truth for the allowed institution
  domains; the prompt files do not repeat that list, and it is the same list
  passed as `allowed_domains` to the web-search tool call.
- **Mandatory for new use cases.** Every new use case, regardless of
  `pipeline_family`, must run EX1 immediately before its Stage-1 human gate
  and EX2 immediately before its Stage-2 human gate. Adding a human gate
  without EX is an architecture violation.
- **First-class agent-local shelf.** EX1 and EX2 each have their own shelf
  under `registry_profiles/<agent_id>/<registry_id>/{active.json, archive/}`,
  following the same multi-instance layout as P4/P5/P6, accessed only through
  `ex_profile_store.py`. `regulation_cli.py`'s `snapshot_active_set` and
  `snapshot_document_active_set` archive EX1/EX2's active record on
  `--refresh` exactly like every other chain shelf — EX history is not lost on
  refresh. EX1/EX2's presence as a scored evaluation-gold checkpoint (still
  non-binding inside the pipeline) is documented in
  `agents/output/README.md`.
- `input_refs` candidate variant: because EX reads a gate payload that has not
  yet been promoted to its own shelf, EX's `input_refs` may include
  `{"artifact_id": None, "payload_sha256": <hash>, "state": "candidate"}` for
  that payload, alongside standard `{artifact_id, payload_sha256}` refs for
  any already-active upstream shelves it also reads.

## Design Philosophy: Minimal Validation

This is a research codebase, not a production system, and its validation is
deliberately light: Pydantic schemas enforce structure and type on every
agent's output, but there are no business-logic checks on LLM-generated
*content* (no equality/substring/verbatim checks against another agent's
output), no repair-or-retry loops driven by validation failure, and no
cross-agent content checks. An LLM agent's text fields belong to that
agent's judgment; if two agents must share a value exactly, it is passed
through code rather than asked of the model twice and compared.

## Critical Architecture Rules

### Topology execution philosophy

The three topologies in Λ differ by *execution shape*, not agent count:

| Topology | Shape | Distinguishing feature |
|---|---|---|
| **Direct** | Sequential, dependent chain (1..N steps) | Each step receives the prior step's output + isolated memory; single ownership. |
| **Debate** | 2..N independent parallel reads → reconciliation | No reader sees another before reconciliation; requires genuine interpretive ambiguity. |
| **Coalition** | Parallel domain agents → synthesis | Each agent owns a distinct sub-result; multi-domain ownership required. |

Hard rules:
- **Direct steps are dependent, not independent.** A step that deepens or completes a prior
  step's result is a Direct step. If two or more agents read the same scope without seeing
  each other's work and their views must be reconciled, that is Debate, not Direct.
- **Every Direct step receives both** the prior step's output **and** the isolated memory
  (approved R2 findings, DA1 handoff, registry demand). Neither is optional.
- **Direct's step count is static today, not yet a general binding
  mechanism.** The original design intent was for TS1 to determine the
  number of Direct steps at stage-2 binding time — the topology library
  defines the *shape*, the binding fills in the concrete chain length.
  Today `TOPOLOGY_AGENTS["Direct"]` in `selection/stage2_binding.py` is
  hardcoded to `[D3, P1, F1]` (exactly one P1 step); TS1 chooses only
  *which* topology, never *how many* Direct steps. **If a future task
  changes the Direct topology's design** (a real variable-length step
  mechanism, a second Direct-chain agent, etc.), flag this note to the user
  before proceeding — it may need to change with it.

### Agent-local shelves

Persistent outputs keyed by `registry_id` live under the owning agent folder:

```text
registry_profiles/<registry_id>/active.json
registry_profiles/<registry_id>/archive/
```

Access persisted artifacts through the owning agent's `*_profile_store` module.
Do not hardcode paths into another agent's folder.

The CLI is the only layer allowed to import multiple agent store modules in one
run. Individual agents do not call another agent's store module directly.

Whenever an artifact is consumed, the consuming stored record must declare
`input_refs` with the upstream `artifact_id` and `payload_sha256`.

### Run identity and cross-run provenance

The pipeline's identifiers form a strict hierarchy (`registry_id -> run_id ->
agent_id -> artifact_id`), each generated in a different place; see
`skills/system-check/RULES.md`'s "F. Run identity & cross-run provenance" for
the field-by-field definitions. Every shelf record carries its own `run_id` +
`agent_id` + `artifact_id` together, and every `input_refs` entry carries the
upstream record's `artifact_id` + `payload_sha256` **and** `run_id`
(`_active_ref()` in `regulation_cli.py`) — recorded on every write today.

**Same-run consumption (hard rule).** A gate-final or stage-2 agent (CP1, TS1,
P1/P2/P3/S1/C1/C2/F1, P4/P5/P6/F2, EX1/EX2, and their document-family
equivalents) must only consume an upstream input whose `input_refs[...].run_id`
equals its own `run_id`. Pre-topology agents (R1, DA1, R2, D1, D2, CP1, TS1, D3)
are resumable across runs by design — a pre-topology agent legitimately carrying
over an older input is not itself a violation, but the record that does so must
be marked `carried_over: true` with `carried_from_run_id` set to the origin run,
so the carry-over is visible on the record rather than silent.

**Completion is a `run_id` match, not file existence.** A run is end-to-end
complete only when its terminal agent's (tabular F1 / document F2) own `run_id`
equals the run directory name — the snapshot file
(`active_profile_set.json` / `active_document_set.json`) is written on
human-rejection and post-gate exception paths too, so its mere presence proves
nothing about completion.

**Dropping a stale record must stay visible.** When a run's snapshot is scoped
down to what that run actually produced, any stage-2 record excluded for
carrying a foreign `run_id` must be surfaced to the operator (for example, in a
CLI panel naming the dropped agent), not silently omitted — the audit must be
able to tell "this agent never ran" apart from "this agent's stale record was
dropped".

**Mandatory for new use cases.** Any new agent added to a gate-final or stage-2
role must carry a same-run-checkable `run_id` in both its own shelf record and
every `input_refs` entry it writes, from its first run.

R25 is enforced: `regulation_cli.py`'s `_checked_refs()` raises `RuntimeError` if
a stage-2/gate-final agent's `input_refs` names a `run_id` other than its own
(pre-topology agents and `state: "candidate"` refs are exempt, per the rules
above). R26 needs no dedicated check — `_scoped_snapshot()`'s existing stale-record
drop-and-panel logic already satisfies it. `/system-check`'s R25–R26 hold the
measured status of where `run_id` is actually verified.

### DA1 approval is final

D1 executes exactly the human-approved `handoff_data_request`. It must not
silently correct the handoff toward the registry's natural-language intent. If
the approved handoff diverges from the request, surface that downstream as a CP1
limitation rather than changing the data request.

### Post-R2 data boundary

After human R2 approval, CP1 and later stages receive only:

- R2 `in_scope_findings`
- DA1 mapped/approved fields and final handoff
- the registry **demand** (`natural_request`, `expected_output`, `output_profile`,
  `assurance_profile`) — not raw registry scope

Raw registry scope (`site_filter`, `time_window`, `regulation_refs`) is NOT
re-forwarded to CP1; finalized scope reaches CP1 through
`approved_scope.handoff_data_request` and `r2_in_scope_findings`.

They do not receive R1 raw findings, R1 `possible_fields`, blocked/excluded R2
findings, or per-field human decision history. Those remain in the owning shelves
for audit.

**P1 input boundary:** The Direct chain lead_interpreter (P1) receives only
`R2 in_scope_findings` + `D3 payload`. It does not receive CP1 (CP1's job ended
at TS1), DA1 handoff (already consumed by D1/D3; D3 payload is the observable
result), or registry demand (R2 is the approved, refined form of the original
scope; re-forwarding the raw registry could contradict the approved R2 boundary).

**F1 input boundary:** The Direct chain dependent_step (F1) receives P1's full
output (rows + chain-level carried_caveats), `R2 in_scope_findings` (for citation
references only), and the registry **demand** (`demand_snapshot` — `natural_request`,
`expected_output`, `output_profile`, `assurance_profile`). The demand tells F1 what
deliverable shape to produce; it is the only registry channel permitted at this stage
(raw scope fields — `site_filter`, `time_window`, `regulation_refs` — are NOT
forwarded). F1 does not receive CP1, DA1 handoff, or raw D3 — the sole
permitted D3 channel is D3's filtered `deterministic_summary` (portfolio-level
counts, not row-level data), passed through so F1 can state aggregate figures
consistent with P1's row-level findings without re-deriving them itself.
D3 now passes through site-level geographic attributes (`country`, `country_code`,
`country_name`, `cdp_region`, `city`, `address`, `zip_code`, `latitude`,
`longitude`) sourced from D1 dimension joins (including the `dist_ie_locations.csv`
join keyed by `location_id`), enabling P1 to produce geographically specific
interpretations down to street level. P1 batches by **location** (all of a site's
rule-rows arrive in the same concurrent batch), not by raw row index, so each
batch sees the site's complete regulatory picture across all threshold rules.
P1 uses a web-search tool (Anthropic
`web_search_20250305` via Azure Foundry) to ground location-specific and current-
event claims; any claim that cannot be verified via search must be flagged in the
row's `carried_caveats` as "unverified / model-knowledge".

### Topology final-agent gate rule

Every topology chain must have a **human review gate after its final agent**.
Intermediate Direct steps (P1, when the final agent is F1) are promoted
automatically without a human gate. Only the last agent in the chain (F1 for the
Direct topology) produces a `[a]pprove / [r]eject` prompt. This keeps the human
decision point at the fully assembled output, not at each step of the interpretation
chain.

### Isolated memory is the single source of truth for assurance

The two human approval gates (DA1 → human, R2 → human) lock an **isolated memory**
that is the sole epistemic reality for all downstream stages. This memory consists of:

- The human-approved **R2 in-scope findings** — the regulation boundary.
- The human-approved **DA1 data mapping and handoff** — the data boundary.
- The **approval act itself** — the expert's deliberate choice of scope and mapping.

**Assurance IS that approved choice.** The registry `assurance_profile` and `demand`
describe the original request; they are context for R1 orientation, not assurance
authority. Once the expert has approved a scope + mapping, D1/D2/CP1 and all
downstream agents operate on the isolated memory alone.

This is the architecture of EPOCH's epistemic pipeline: R1 surfaces candidate fields
and warns the expert of regulatory obligations; the expert decides; the approved
decision becomes immutable isolated memory; downstream agents interpret only that.

Hard rules:
- D1/D2/CP1 and later agents must not re-consult or re-weight raw registry scope
  (`site_filter`, `time_window`, `regulation_refs`) as an assurance authority.
- CP1 must not treat `demand.assurance_profile` as a floor that can override the
  approved R2+DA1 evidence; the isolated memory IS the floor.
- Any conflict between the registry demand and the approved isolated memory is
  surfaced as a CP1 `limitations` entry, never resolved by downgrading the
  isolated memory toward the registry request.

### Plain-language CLI disclosure

Every agent CLI render function must explain displayed signals/statuses in
plain, non-technical language. Do not show opaque labels without a short meaning
for the reader.

### Evaluation isolation

Every evaluation test is self-contained under `evaluation/<framework>/.../<test>/`.
Its runner/scoring scripts, `gold/`, `runs/`, reports, manifests, raw LLM records,
and logs live inside that test folder. Do not write benchmark outputs only to a
shared or external location.

## Planning-Mode User Decisions (hard rule)

- Do not use auto-resolution timers for user decision requests in this
  repository. If a user decision is requested, wait for an explicit answer
  unless the user has already authorized a default.
- In Plan Mode, every critical decision must be asked before it is locked into
  a plan. This includes scope, citation/source choices, examples or analogies,
  methodology framing, output locations, and repository rules.
- Do not finalize a plan while critical user choices are still unanswered.

## Code Style

- Python 3.11+
- Type hints
- Pydantic v2 models
- Config via `.env` and `epoch_switch.config`
