# system-check rule catalog

Canonical, tool-neutral rule catalog for the `/system-check` audit skill. Read this
fresh each time — do not copy these rules into `.claude/skills/system-check/SKILL.md`
or `.codex/skills/system-check/SKILL.md`; those are thin stubs that route here.

Each rule has a fixed shape so `check.py` and the judgment pass in `SKILL.md` can
address the same rule id in the same report:

```
### R<NN> — <name>
**Rule.** <one-sentence statement>
**Source.** <AGENTS.md section and/or file:line>
**Check.** script | read | both
**Failure signature.** <what a violation actually looks like on disk / in code>
**Not a violation.** <the common false positive, if one exists>
```

No rule in this catalog is `script`-only — every rule is either `read` or `both`.
A rule's `check.py` finding, where one exists, is the PASS/FAIL of what the script
mechanically looked at; it is never the PASS/FAIL of the rule's full statement. Do
not re-derive a `both` rule's mechanical half (take the script's finding verbatim),
but never skip its judgment half on the strength of a script PASS — the judgment
half exists precisely because the script's coverage is narrower than the rule.
`Check: read` rules require judgment only — reading run artifacts, prompts, or code
— and `check.py` does not attempt them.

---

## A. Pipeline & topology

### R01 — Active pipeline shape
**Rule.** The tabular chain (`R1 -> DA1 -> human -> R2 -> human -> D1 -> D2 ->
derived_case_facts -> CP1 -> human`) and the UC4 document chain (`RD1 -> RD2 -> RC1.1 ->
RC1.2 -> RM1.1 -> RM1.2 -> RD3 -> human -> CP1 -> TS1 -> P4/P5/P6 -> F2 -> human`) are
the only two pipeline shapes; `build_document_pipeline` is reached only through an early
branch in `build_profile_pipeline` for `pipeline_family == "document"`.
**Source.** AGENTS.md "Current Project State", "UC4 — document pipeline"; `regulation_cli.py`.
**Check.** both — script confirms a completed run's artifact set matches its detected
family; judgment confirms the branch itself has not been bypassed or duplicated.
**Failure signature.** A document-family run missing `rd1`/`rd2`/`rd3` artifacts, or a
tabular run producing document-only artifacts (`rc1_2`, `rm1_2`, `f2`).
**Not a violation.** EX1/EX2 artifacts may legitimately be absent (see R15).

### R02 — Agent-type classification
**Rule.** Every agent is exactly one of LLM, Deterministic, or Hybrid. Deterministic
agents never call an LLM. LLM agents never embed hardcoded lookup tables that replicate
catalog or registry data already available elsewhere in the pipeline.
**Source.** AGENTS.md "Agent Type Classification".
**Check.** both — script greps a fixed list of known-deterministic agent modules
(`data/d1_loader`, `regulation/rd3_join`, `corpus/`) for five literal markers
(`BaseAgent`, `call_json`, `call_json_websearch`, ...); judgment covers what the
fixed list and literal-marker grep cannot: (1) confirm the deterministic-module list
itself is still exhaustive — a newly added deterministic agent not yet appended to
`DETERMINISTIC_AGENT_DIRS` in `check.py` is invisible to the script; (2) check for
indirect LLM access the markers miss (a helper function, subprocess call, or raw
HTTP request to a model endpoint); (3) spot-check that an LLM agent's code has no
hardcoded lookup table replicating catalog/registry data — the script never looks
at LLM agents at all.
**Failure signature.** A deterministic module importing `BaseAgent` or calling an LLM
backend function; a deterministic agent added to the codebase but absent from
`check.py`'s scan list; an LLM agent's prompt-building code embedding a static
dict that duplicates registry or catalog data available elsewhere in the pipeline.
**Not a violation.** Hybrid agents (D2, F2) legitimately mix an LLM sub-call with a
deterministic sub-component — the rule targets agents documented as pure Deterministic.

### R03 — Topology execution philosophy
**Rule.** Direct is a sequential dependent chain where every step receives both the
prior step's output and the isolated memory; Debate is independent parallel reads with
no reader seeing another before reconciliation; Coalition is parallel domain agents with
distinct sub-results synthesized by one agent. Direct step count is bound by TS1 at
stage-2 binding time, not fixed a priori in the topology library.
**Source.** AGENTS.md "Topology execution philosophy"; `selection/stage2_binding.py`.
**Check.** read — inspect `TOPOLOGY_AGENTS` / `DOCUMENT_TOPOLOGY_AGENTS` bindings and
the agent code that consumes prior-step output plus isolated memory.
**Failure signature.** A Debate reader agent's prompt or payload construction exposes
another reader's output before reconciliation; a Direct step agent missing either the
prior step's output or the isolated memory in its input payload.
**Not a violation.** The tabular Direct chain is concretely bound to `[D3, P1, F1]`
today — a single P1 step is expected, not a defect (see AGENTS.md's "Step count for
Direct" note).

### R04 — Blind-pass agent pairs (X.1 / X.2)
**Rule.** X.1 (`RC1.1`, `RM1.1`) never receives RD2's deterministic candidate proposal;
its payload physically contains none of `candidates`, `basis`, `title_match_score`,
`non_authoritative_hints`, `index_candidate_sections`, `deterministic_index_candidates`.
X.1 never assigns the pair's binding verdict field (`change_status` / `reported_status`).
X.2 receives both X.1's blind rows and RD2's candidates side by side and makes the
binding decision.
**Source.** AGENTS.md "Blind-pass agent pairs (X.1 / X.2)".
**Check.** both — script scans the X.1 payload-builder function for the forbidden keys;
judgment confirms X.2's prompt and payload genuinely present both sources without
anchoring instructions that defeat the blind design.
**Failure signature.** Any of the six forbidden keys present in the dict built for
X.1's LLM call; X.1's output schema containing `change_status` / `reported_status` as
anything other than absent/proposal-only.
**Not a violation.** X.1's shelf record (written for audit) may reference RD2 metadata
in `input_refs` bookkeeping — the rule is about what reaches the LLM call, not what the
audit trail records.

---

## B. Shelf & provenance

### R05 — Agent-local shelf layout
**Rule.** Persistent outputs live at `registry_profiles/<registry_id>/active.json` +
`archive/`, owned by the producing agent's folder. Multi-instance agents (EX1/EX2,
P4/P5/P6) insert an `<agent_id>/` level first:
`registry_profiles/<agent_id>/<registry_id>/active.json`.
**Source.** AGENTS.md "Agent-local shelves".
**Check.** both — script globs every `registry_profiles/` under
`src/epoch_switch/agents/` and matches each *existing* `active.json` path against
one of the two allowed shapes, and each archive filename against
`<ISO-compact UTC>_<artifact_id>.json`; judgment covers what a glob over existing
files structurally cannot: an agent that is supposed to own a shelf for a given
registry but has none at all (silently missing, not misplaced), or a store module
that writes its state somewhere other than under its own `registry_profiles/` (the
glob would simply never see it).
**Failure signature.** An `active.json` at a path that is neither shape, or an archive
file that isn't stamped with a UTC timestamp prefix; a gate-final or stage-2 agent
for a use case that has run to completion but has no shelf record at all.
**Not a violation.** An agent legitimately not yet run for a given registry (no
shelf expected until its first run).

### R06 — Store-access boundary
**Rule.** Persisted artifacts are accessed only through the owning agent's
`*_profile_store` module. Only `regulation_cli.py` may import more than one agent's
store module in a single run. No agent hardcodes a path into another agent's folder.
**Source.** AGENTS.md "Agent-local shelves".
**Check.** both — script (`check_r06_r08` in `check.py`) does a **static** AST
import-graph scan of `src/epoch_switch/agents/` only, flagging a module that
imports a different agent folder's module (leading-underscore shared-infra folders
exempted); it does **not** grep for literal
`registry_profiles` path strings (RULES.md previously claimed this — it is not
implemented) and does not look outside `agents/`. Judgment covers: (1) a literal
path string into another agent's shelf folder (e.g. an f-string built path) that
the AST scan cannot see; (2) a dynamic import (`importlib.import_module`,
`__import__`) of a store module; (3) whether any module outside
`src/epoch_switch/agents/` (for example, `selection/`) imports more than one agent's
store module — only `regulation_cli.py` is the sanctioned exemption, and the
script never checks those folders at all.
**Failure signature.** `agents/output/f1_finalizer/f1_agent.py` importing
`p1_profile_store`, for example; a hardcoded string like
`Path("agents/p1_.../registry_profiles")` inside a different agent's module; a
a non-CLI module importing several `*_profile_store` modules directly.
**Not a violation.** Test files under `tests/` legitimately import multiple stores to
set up fixtures; scope the scan to `src/epoch_switch/agents/`.

### R07 — `input_refs` provenance
**Rule.** Every consuming shelf record declares `input_refs` with the upstream
`artifact_id` and `payload_sha256`. EX1/EX2 may additionally use the candidate variant
`{"artifact_id": None, "payload_sha256": <hash>, "state": "candidate"}` when the payload
being read is a gate payload not yet promoted to its own shelf.
**Source.** AGENTS.md "Agent-local shelves"; `ex_profile_store.py`.
**Check.** both — script (`check_r07`) checks only the *shape* of each `input_refs`
entry (either an identity key + `payload_sha256`, or the candidate-variant shape); it
does not verify presence (an empty `input_refs` dict passes silently — RULES.md
previously claimed the script checks non-emptiness, it does not), and it never
compares a declared `payload_sha256` against the upstream record it actually points
to — `_sha256_of` in `check.py` is defined but never called. Judgment covers: (1) a
consuming agent (not the pipeline's first shelf-writer) with an empty `input_refs`
dict, meaning it declares no upstream at all; (2) spot-checking a sample record's
`payload_sha256` against a fresh hash of the referenced upstream `active.json`'s
`payload` to confirm the recorded hash is real, not copy-pasted or stale; (3) an
agent that consumed more upstream data than its `input_refs` declares.
**Failure signature.** An `input_refs` entry missing `payload_sha256`, or a candidate
entry missing `"state": "candidate"`; an empty `input_refs` dict on a non-first
shelf-writer; a `payload_sha256` that does not match a fresh hash of the referenced
upstream payload.
**Not a violation.** The very first agent to write a shelf in a fresh pipeline
legitimately has no upstream shelf artifact to reference yet.

### R08 — Agent self-sufficiency
**Rule.** Each agent's engine and prompt live in its own folder; agents do not import
another agent's engine module. Cross-agent sharing happens only through
`epoch_switch.core.*` — not through a dependency on a sibling agent folder.
Where sharing would otherwise happen, deliberate duplication is preferred over a new
cross-agent import.
**Source.** repo convention (agent folder layout); `epoch_switch/core/`.
**Check.** both — same static AST scan as R06 (`check_r06_r08` emits one shared
`R06/R08` finding); the script catches a static cross-agent-engine import but not a
dynamic one, and it does not evaluate whether new sharing was genuinely
unavoidable versus deliberate duplication being the better call. Judgment covers:
(1) confirming a flagged (or newly added) cross-folder dependency could not
reasonably have been solved by duplicating the small piece of logic instead; (2) a
dynamic import the AST scan misses; (3) new code reaching into
`epoch_switch.core` in a way that smuggles in
another agent's business logic through a shared module rather than genuinely
common infrastructure.
**Failure signature.** `import epoch_switch.agents.regulation.r1_active_reader.r1_agent`
from inside a different agent's module; for example, `d2_missing_value.py` importing
`epoch_switch.agents.data.d1_loader.d1_data_request_planner` directly instead of going
through D1's shelf/store or a CLI-layer orchestration step; a new "shared utility"
added to `epoch_switch.core` that is really one agent's logic relocated to dodge R08.
**Not a violation.** Imports of shared infrastructure such as the runtime data
catalog from `epoch_switch.core`; imports of Pydantic schema base classes or
`BaseAgent`; imports of a
leading-underscore shared-infra folder such as `agents/output/_debate_common` (P2/P3's
shared Debate-topology row schema and batching helper — deliberately shared, carries no
`agent_id` or profile store of its own).

### R09 — One-class-N-instances pattern
**Rule.** Multi-instance agents (P4/P5/P6 via `PersonaAssessorAgent`, EX1/EX2 via
`ExternalCommentatorAgent`) are one Python class instantiated per `agent_id`, with
`BaseAgent.role_prompt_path` resolving the prompt file from the class's folder plus the
`agent_id`. This is the sole sanctioned exception to one-agent-one-class.
**Source.** AGENTS.md "External commentary agents (EX1/EX2)".
**Check.** read — confirm no new agent introduces a second class to represent an
instance variant instead of parameterizing by `agent_id`.
**Failure signature.** A new `class P7Agent` duplicating `PersonaAssessorAgent` instead
of adding `"P7"` to its instantiation set.
**Not a violation.** none known.

### R10 — EvidenceStore key pattern
**Rule.** `evidence_store.json` keys follow one of two coexisting conventions: most
agents key by `<slug>_<8-hex case hash>` (for example
`rc1_2_change_classification_ccdcbc8e`); D1/D2's data-quality keys instead key by the
registry id itself (for example `data_catalog_uc1_enefg_threshold_check`). Both are
legitimate; a key matching neither is the violation.
**Source.** observed in `experiments/<visibility-or-data-mode>/*/run_*/evidence_store.json`.
**Check.** both — script regex-validates every top-level key against both patterns
in the run being audited; it says nothing about whether a key that matches the
regex actually belongs to the case it names, or whether a *third* keying
convention has quietly appeared across several runs and should be documented
here rather than left to widen the regex ad hoc. Judgment covers: (1) spot-check
that a sampled key's case-hash or registry-id suffix corresponds to the
`payload` actually stored under it; (2) if a key fails both regexes, check
whether it is a genuine violation or an emerging third convention that RULES.md
itself needs to be updated to describe.
**Failure signature.** A key with neither a trailing 8-hex suffix nor an embedded
registry id (`uc<N>...`); a key that matches the hash pattern but whose hash does not
correspond to the case its `payload` actually describes.
**Not a violation.** The registry-id keying style used by D1/D2's data-quality entries.

---

## C. Epistemic boundary

### R11 — Isolated memory is the single source of truth for assurance
**Rule.** D1/D2/CP1 and all downstream agents operate only on the isolated memory —
approved R2 in-scope findings, approved DA1 mapping/handoff, and the approval act
itself. They never re-consult or re-weight raw registry scope (`site_filter`,
`time_window`, `regulation_refs`) as assurance authority. CP1 never treats
`demand.assurance_profile` as a floor overriding approved R2+DA1 evidence. Any conflict
between the registry demand and the approved isolated memory is a CP1 `limitations`
entry, never a downgrade of the isolated memory.
**Source.** AGENTS.md "Isolated memory is the single source of truth for assurance".
**Check.** read — inspect CP1's input payload construction and prompt for raw scope
fields; inspect CP1's output for whether conflicts are surfaced as `limitations`.
**Failure signature.** CP1's payload builder forwarding `site_filter` / `time_window` /
`regulation_refs`; CP1 output text that resolves a demand-vs-isolated-memory conflict by
silently adjusting the isolated-memory reading instead of logging a limitation.
**Not a violation.** none known.

### R12 — DA1 approval is final
**Rule.** D1 executes exactly the human-approved `handoff_data_request`. It never
silently corrects the handoff toward the registry's natural-language intent. Divergence
between the approved handoff and the registry request surfaces downstream as a CP1
limitation, not a change to the data request.
**Source.** AGENTS.md "DA1 approval is final".
**Check.** read — inspect D1's execution code for any branch that adjusts the approved
handoff based on registry text.
**Failure signature.** D1 code with a conditional that substitutes or extends the
approved field list using registry `natural_request` text.
**Not a violation.** none known.

### R13 — Post-R2 data boundary
**Rule.** After R2 human approval, CP1 and later stages receive only R2
`in_scope_findings`, DA1's mapped/approved fields and final handoff, and the registry
demand (`natural_request`, `expected_output`, `output_profile`, `assurance_profile`) —
never raw registry scope, R1 raw findings, `possible_fields`, blocked/excluded R2
findings, or per-field decision history. P1 receives only R2 `in_scope_findings` + D3
payload. F1 receives P1's output, R2 `in_scope_findings` (citation only), the registry
demand, and only D3's filtered `deterministic_summary` — never row-level D3 data, CP1,
or raw DA1 handoff.
**Source.** AGENTS.md "Post-R2 data boundary".
**Check.** read — inspect the payload-construction call sites for CP1, P1, and F1 in
`regulation_cli.py` / `_run_post_r2_tail`.
**Failure signature.** F1's payload containing row-level D3 output instead of
`deterministic_summary`; P1's payload containing CP1 or DA1 handoff data.
**Not a violation.** none known.

### R14 — Downstream sees only approved/filtered output
**Rule.** No downstream agent payload contains R1 raw findings, R1 `possible_fields`,
blocked/excluded R2 findings, or per-field human decision history — those remain in
their owning shelves for audit only.
**Source.** AGENTS.md "Post-R2 data boundary".
**Check.** read — spot-check payload dict construction for these key names leaking past
their originating agent's shelf.
**Failure signature.** A CP1 or later payload dict containing a `possible_fields` key.
**Not a violation.** The owning shelf (R1's, R2's) legitimately stores these fields for
its own record.

### R15 — EX1/EX2 advisory-only
**Rule.** EX1/EX2 output must never feed any downstream agent, never be written into
another agent's shelf record, and never change the isolated memory. It is added to the
gate payload dict the human sees under `"ex1"`/`"ex2"` via a separately-named local
variable (e.g. `r2_gate_payload = {**r2_profile, "ex1": ...}`); the agent's own payload
variable is never mutated. EX1 always runs immediately before the Stage-1 human gate
(tabular: R2; document: the atomic RC1.2/RM1.2/RD3 gate), EX2 immediately before
Stage-2 (tabular: F1; document: F2) — in every use case, including `--auto` mode. If the
active backend lacks web search, the CLI catches the exception, prints a skip panel, and
continues.
**Source.** AGENTS.md "External commentary agents (EX1/EX2)".
**Check.** both — script scans non-EX shelf records and run artifacts for stray
`ex1`/`ex2` keys; judgment confirms the gate-payload variable is separately named (not
a mutation of the agent's own payload) and that both EX stages are present for the
detected use case's pipeline.
**Failure signature.** An `ex1` key found inside `f1_output_round_*.json`'s own shelf
payload (not just the CLI-only gate-display dict); a new use case's human gate reached
with no preceding EX call in the code path.
**Not a violation.** A completed run legitimately missing `ex1_output_round_*.json` /
`ex2_output_round_*.json` files — report as WARN, not FAIL, and check for the
skip-panel code path before concluding it's a bug.

---

## D. Gates & conventions

### R16 — Topology final-agent gate rule
**Rule.** Every topology chain has a human review gate after its final agent only.
Intermediate Direct steps promote automatically with no gate of their own. UC4's five
document-matching shelves (`rc1_1`, `rc1_2`, `rm1_1`, `rm1_2`, `rd3`) share one atomic
human decision, but the gate payload shown to the human contains only `rc1_2`, `rm1_2`,
and `rd3` — X.1's rows are written to their shelf for audit but not displayed at the
gate.
**Source.** AGENTS.md "Topology final-agent gate rule"; "Blind-pass agent pairs".
**Check.** read — inspect the gate-payload dict built immediately before each
`[a]pprove/[r]eject` prompt.
**Failure signature.** An intermediate step (P1, RC1.1) opening its own approve/reject
prompt; the UC4 atomic gate payload including `rc1_1` or `rm1_1` keys.
**Not a violation.** none known.

### R17 — P vs Coalition prompt-style contract
**Rule.** P-style prompts (P1/P2/P3, P4/P5/P6) use singular address, isolated-memory
framing, and the 10-section layout described in `agents/output/README.md`. Coalition
agents (C1/C2, UC3) are real, not speculative: C1 does N independent isolated LLM
calls, one per division (no separate T1/T2/T3 classes); C2 is a role agent that
converges C1's results, structurally analogous to S1. `c1_portfolio_assembler/` and
`division_team/` are empty legacy folders from an earlier, unbuilt design — not
partial implementation. S1, F1, F2, EX1, EX2 are role agents that follow neither
P nor Coalition template.
**Source.** AGENTS.md "Person (P) vs Coalition prompt-style contract";
`src/epoch_switch/agents/output/README.md` for the full contract.
**Check.** read — for any new or edited P-style prompt file, confirm the section order
and singular framing; for a Coalition agent, confirm per-division call isolation
(not a shared payload); for role agents, confirm no P/Coalition template markers
were copied in.
**Failure signature.** A P-agent prompt using collective ("Your team owns") language;
a Coalition agent's payload leaking another division's rows into one call; a
role-agent prompt with a P-style isolated-memory section it doesn't need; new code
treating `c1_portfolio_assembler/` or `division_team/` as active.
**Not a violation.** none known.

### R18 — Validation restraint
**Rule.** No new `raise`/`assert`/guard clause rejecting an agent's output, no LLM-field
equality/substring/verbatim check, no cross-agent content check, no validation-driven
repair/retry loop, and no `@field_validator`/`@model_validator` beyond plain type/enum
format, unless the user explicitly requested it in the task that introduced it.
**Source.** AGENTS.md "Validation Restraint (thesis context — hard rule)".
**Check.** read — diff review of new/changed agent code for these five patterns.
**Failure signature.** A new `@field_validator` comparing an LLM text field against
another agent's field for equality.
**Not a violation.** Plain Pydantic field declarations (type, enum, pattern, min/max)
and parsing that raises naturally on malformed input.

### R19 — Data convention and visibility routing
**Rule.** `data/real/`, runtime experiment directories, and agent-local shelves
are gitignored and never committed. Curated disclosure artifacts live only under
`public/gold/`.
`config.create_experiment_dir(visibility=...)` is the single routing point — chosen by
`pipeline_family`, never by hardcoding a use-case id. All data access goes through
`epoch_switch.config.DATA_DIR`, never an absolute path.
**Source.** AGENTS.md "Data Convention".
**Check.** both — script (`check_r19`) finds every `create_experiment_dir(` call
site and checks only the one narrow shape
`visibility="public" if <cond> else ...` for a `pipeline_family` mention in
`<cond>`; a non-ternary `if/else` block routing the same decision is invisible to
it, and it never greps for hardcoded use-case-id comparisons or absolute data
path literals despite RULES.md previously claiming it does — neither check is
implemented. Judgment covers: (1) an `if registry_id == "uc4_..."`-shaped
non-ternary visibility decision anywhere in `src/`; (2) any read of registry/report
data that bypasses `epoch_switch.config.DATA_DIR` via a hardcoded absolute or
relative path; (3) confirming `data/real/`, `experiments/*/`, and agent shelf
directories are listed in `.gitignore` and absent from the public history.
**Failure signature.** A second function computing the experiment root by checking
`registry_id == "uc4_..."` instead of `pipeline_family`, in `if/else` form or
otherwise; a file path literal pointing into `data/` that does not go through
`DATA_DIR`; a committed real-data, runtime-run, or agent-shelf file.
**Not a violation.** none known.

### R20 — Plain-language CLI disclosure
**Rule.** Every agent CLI render function explains displayed signals/statuses in plain,
non-technical language — no opaque label shown without a short meaning for the reader.
**Source.** AGENTS.md "Plain-language CLI disclosure".
**Check.** read — spot-check `render_*` functions in `regulation_cli.py` for bare status
codes or enum values with no accompanying explanation.
**Failure signature.** A `render_rc1` panel printing `change_status: MERGED` with no
plain-language gloss.
**Not a violation.** none known.

### R21 — Documentation restraint
**Rule.** No new micro-level README files for ordinary source subfolders; new READMEs
only for durable public surfaces, subsystem contracts, isolated evaluation tests,
generated run artifacts, or historical folders needing provenance. Thesis figure/table
order is locked once committed; bold lead-in statements are reserved for genuinely
load-bearing claims.
**Source.** AGENTS.md "Documentation Status Rules"; "Figure and table order is locked";
"Bold lead-in statements".
**Check.** read — only relevant when the audited change touched `thesis/` or added a
new README; otherwise `N/A`.
**Failure signature.** A new README in an ordinary agent subfolder with no durability
justification; a reordered figure/table with no explicit user request.
**Not a violation.** A README added to `evaluation/<test>/` (isolated evaluation test)
or a generated run artifact folder.

## E. LLM provenance

`llm_provenance` is a 14th top-level key in a shelf record, sibling to `payload` —
never nested inside `payload`, which would change `payload_sha256` and let it leak
downstream. Deterministic agents (D1, D3, RD3) record it as explicit `null`.

```json
"llm_provenance": {
  "provider": "foundry_anthropic", "backend_preset": "opus48",
  "model": "claude-opus-4-8",
  "reasoning": {"enabled": true, "mode": "adaptive", "effort": "high"},
  "tokens": {"reported_as": "split", "input": 18412, "output": 3907},
  "calls": 1
}
```

Provider field mapping (see AGENTS.md "LLM Provenance and Reasoning Configuration"
for the full table): Anthropic (`foundry_anthropic`) reasoning switch is
`thinking={"type":"adaptive"}`, effort is `output_config.effort`, tokens are
`usage.input_tokens`/`usage.output_tokens` (split). OpenAI-family
(`azure_openai`/`openai`) reasoning and effort are both `reasoning_effort=`, tokens
are `usage.prompt_tokens`/`usage.completion_tokens`/`usage.total_tokens` (split plus
total). A plain `openai` backend sends no reasoning parameter at all and cannot back
an LLM agent under R23.

**Closed as of 2026-09-11:** R22–R24 are implemented — every adapter in
`core/llm_client.py` reads `resp.usage` into a `call_meta` dict, the Foundry
adapter sends `thinking`/`output_config` per the active backend's
`reasoning_effort`, every LLM/Hybrid agent accumulates its calls into a
`ProvenanceCollector` (`core/llm_provenance.py`) exposed as
`self.last_llm_provenance`, and `regulation_cli.py` writes it into that agent's
shelf record via each `*_profile_store.py`'s `llm_provenance` parameter.
Deterministic agents (D1, D3, RD3) record explicit `null`. Runs predating this
change still carry no `llm_provenance` key — treat those as WARN (pre-fix
historical data), not FAIL; only a *post-fix* run missing the key is a FAIL.

### R22 — LLM identity recorded
**Rule.** Every LLM/Hybrid agent's `active.json` carries a non-null `llm_provenance`
with `provider`, `backend_preset`, and `model`; every Deterministic agent's carries
`llm_provenance: null`.
**Source.** AGENTS.md "LLM Provenance and Reasoning Configuration"; AGENTS.md "Agent
Type Classification" table for which agents are LLM/Hybrid/Deterministic.
**Check.** both — script counts records with/without the key across all shelves;
judgment confirms the values look like real request parameters, not placeholders.
**Failure signature.** An LLM agent's `active.json` with no `llm_provenance` key, or
one present but `null`, or a Deterministic agent's record carrying a populated block.
**Not a violation.** A `model` string that differs between two agents of the same
type — per-agent backend overrides are legitimate; the rule requires it recorded,
not uniform.

### R23 — Reasoning enabled, effort never `none`
**Rule.** `llm_provenance.reasoning.enabled` is `true`, `mode` is non-empty, and
`effort` is a concrete value — never `none`, `null`, or empty — for every LLM/Hybrid
agent; the adapter that produced the record actually sent the provider's reasoning
parameter.
**Source.** AGENTS.md "LLM Provenance and Reasoning Configuration".
**Check.** both — script checks `reasoning_effort`/`thinking`/`output_config`
wiring in `core/llm_client.py` and `config.py`'s `BACKENDS`; judgment confirms the
recorded effort matches what the backend preset can actually produce.
**Failure signature.** `reasoning.effort` missing/`none`/empty; a backend preset
reachable by an LLM agent with no way to express an effort level (for example a
`foundry_anthropic` preset with no `thinking`/`output_config` sent, or a plain
`openai` backend with no `reasoning_effort`).
**Not a violation.** Different effort levels across agents, or a provider-native
mode name that is not the literal string `"adaptive"`.

### R24 — Per-agent token accounting
**Rule.** `llm_provenance.tokens.reported_as` is `"split"` or `"combined"`; split
requires `input` and `output` integers, combined requires `total`, sourced from the
provider's own usage object — never estimated or omitted.
**Source.** AGENTS.md "LLM Provenance and Reasoning Configuration".
**Check.** both — script checks whether `core/llm_client.py` reads `resp.usage` (or
equivalent) anywhere and whether shelf records carry populated `tokens`; judgment
spot-checks a few records against the provider's actual response shape.
**Failure signature.** `tokens` missing, zero across every record (a placeholder,
not a real call with zero usage), or `reported_as` inconsistent with which fields
are present.
**Not a violation.** A missing `extra` sub-block for cache/reasoning-token fields —
those are provider-dependent and not required; `total` absent when
`reported_as == "split"`.

## F. Run identity & cross-run provenance

ID hierarchy: `registry_id -> run_id -> agent_id -> artifact_id`. Every shelf
record carries its own `run_id`/`agent_id`/`artifact_id`; every `input_refs`
entry carries the upstream record's `artifact_id` + `payload_sha256` + `run_id`
(`_active_ref()`, `regulation_cli.py`) — the data needed for cross-run detection
is recorded on every write. Pre-topology agents (`R1, DA1, R2, D1, D2, CP1,
TS1, D3`) are
resumable across runs by design; a carried-over record is marked
`carried_over: true` + `carried_from_run_id` by `_scoped_snapshot`
(`regulation_cli.py`) and is a WARN, not a FAIL. Everything else
(P1/P2/P3/S1/C1/C2/F1, P4/P5/P6/F2, EX1/EX2) must match its own
`run_id` — a mismatch there is a FAIL.

**Closed as of 2026-09-11:** R25 is now enforced at consumption time, not just
recorded — `regulation_cli.py`'s `_checked_refs()` wraps `input_refs=` at every
gate-final/stage-2 call site (P1, P2, P3, S1, C1, C2, F1, P4, P5, P6, F2, EX1,
EX2) and raises `RuntimeError` on any non-exempt `run_id` mismatch, naming the
consuming agent, the ref key, and both run ids. Pre-topology agents
(`_PRE_TOPOLOGY_AGENTS`) and `state: "candidate"` refs (EX1/EX2's gate-payload
variant) remain exempt, per the rule's own carve-outs. R26 required no new code
— `_scoped_snapshot`'s existing drop-and-panel logic already satisfies it.

Two structural notes any check against these rules must account for:
- The run-directory snapshot (`active_profile_set.json` / `active_document_set.json`)
  is already filtered before it is written — `_scoped_snapshot` drops (does not
  mark) a stage-2 record with a foreign `run_id`. A dropped record is invisible in
  the snapshot; distinguishing "this agent never ran" from "this agent's stale
  record was dropped" requires cross-referencing the round-output file
  (`<stage>_output_round_NN.json`), which exists whenever the agent executed.
- The snapshot's per-agent keys are stage slugs, not agent ids (`ts` -> `TS1`,
  `rc1_2` -> `RC1.2`) — matching must go through `agent_id`, not the dict key.

### R25 — Same-run provenance
**Rule.** For every shelf record and every run snapshot entry, each
`input_refs[*].run_id` equals the consuming record's own `run_id`. A mismatch on
a pre-topology agent (see the list above) is legitimate only if the record is
marked `carried_over: true` with `carried_from_run_id` set; any other mismatch,
and any mismatch at all on a gate-final/stage-2 agent, is a violation.
**Source.** AGENTS.md "Run identity and cross-run provenance"; `_active_ref()`
and `_scoped_snapshot()` in `regulation_cli.py`.
**Check.** both — script compares `run_id` fields across every `active.json` and
every `run_NNN/active_*_set.json` on disk; judgment confirms a flagged carry-over
was actually intentional resumption, not a symptom of a broken `--refresh`.
**Failure signature.** A post-gate agent's `input_refs` entry whose `run_id`
differs from the agent's own `run_id` with no `carried_over` marker (there is no
legitimate carry-over path for these agents, so any mismatch is a FAIL); a
pre-topology agent's mismatch with no `carried_over` marker (WARN — recorded but
unflagged, not itself an active leak).
**Not a violation.** `run_id` absent (`None`) on records predating this rule; a
pre-topology mismatch that carries `carried_over: true` +
`carried_from_run_id`. Report old records as WARN, not as a live FAIL.

### R26 — Recent-run integrity
**Rule.** For each `registry_id`'s most recent runs (default last 3, `--last-runs
N` to override, `--registry <id>` to scope to one use case), each run that
reports `complete` genuinely is end-to-end (`_snapshot_is_end_to_end` — terminal
agent's own `run_id` equals the run directory name) and shows no cross-run edge
per R25; a run missing an expected agent's snapshot entry while that agent's
`<stage>_output_round_NN.json` exists in the same run directory is a silent-drop
finding, not silence.
**Source.** AGENTS.md "Run identity and cross-run provenance";
`_snapshot_is_end_to_end`, `list_runs_for_registry`
(`regulation_cli.py:613-660`); `selection/stage2_binding.py`'s
`TOPOLOGY_AGENTS`/`DOCUMENT_TOPOLOGY_AGENTS` for the expected stage-2 agent set.
**Check.** both — script derives the expected agent set from the run's own `ts`
snapshot entry's selected topology and compares it against present/dropped
records; judgment spot-checks a WARN/FAIL run's README and round files to confirm
the classification.
**Failure signature.** A run marked `complete` whose terminal record's `run_id`
does not match the run directory; a round-output file present with no
corresponding snapshot entry (a silently dropped stale record); a cross-run edge
inside the window per R25.
**Not a violation.** A smaller `agent_count` from legitimate `carried_over`
pre-topology reuse; EX1/EX2 absent for the same reasons as R15 (no web-search
backend, or a resume path that skips them).
