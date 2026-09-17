# Agent Prompts

Agent role prompts live co-located with each active CLI agent under
`agents/<group>/<agent>/<AGENT>.md`. This file documents the runtime
classification labels used in those prompt files.

- LLM-backed agents use their prompt as the active system instruction.
- Tool-backed agents keep their prompt as the human-readable contract; the
  deterministic behavior lives **inside the agent's own folder** as an
  agent-prefixed Python module.

---

## ⚠️ HARD RULE — Every Agent Is Fully Self-Contained

**All scripts and prompts that belong to an agent MUST live inside that agent's
own folder. Cross-cutting infrastructure lives under `epoch_switch.core`; there
is no shared `agents/tools/` folder.**

- Every agent-owned deterministic engine, executor, or planner lives in the
  agent's own folder with an agent-prefixed filename. The runtime schema catalog
  is shared infrastructure under `epoch_switch.core.data_catalog`.
- Every LLM prompt used by an agent lives in the agent's own folder
  (e.g. `R1.md`, `DA1.md`, `D2_requirements.md`).
- Agent-specific business logic is duplicated when necessary rather than
  imported from a sibling. Genuine runtime infrastructure shared by the CLI
  and multiple agents belongs under `epoch_switch.core`.
- **Agents MUST NOT import from another agent's folder** (`from
  epoch_switch.agents.X.Y import Z` is forbidden across agent boundaries).
  All cross-agent imports go through `epoch_switch.core.*` only.
- Cross-folder prompt reads (e.g. `Path(__file__).parent.parent / "other_agent" /
  "X.md"`) are **forbidden**. If an agent needs a prompt from another agent,
  it copies the file into its own folder first.
- This rule makes scaling up/down trivial: adding an agent = add one folder;
  removing an agent = delete one folder. No other code changes required.

---

## ⚠️ HARD RULE — Cross-Agent Data Filtering (Post-R2 Boundary)

**R2 is the regulation boundary for downstream agents.** After human R2 approval,
CP1 and all subsequent agents receive only the **approved, filtered scope**:

- **From R2:** `in_scope_findings` only (blocked/excluded findings stay in R2's shelf)
- **From DA1:** mapped/approved fields only (unmapped fields stay in DA1's shelf)
- **From registry:** the `demand` keys only (`natural_request`, `expected_output`,
  `output_profile`, `assurance_profile`) — as original request context, **not as
  assurance authority**. The registry describes what was asked; it does not override
  the approved isolated memory.
- **NOT from R1:** R1's raw findings and `possible_fields` remain in R1's shelf
  for audit; they are not forwarded past R2
- **NOT `human_decisions`:** the net result is in `approved_scope`; per-field
  decisions are not forwarded

R1, DA1, and R2 each retain their full warnings in their own shelves for
post-hoc audit. Downstream agents operate on the **approved, filtered scope**
only. This ensures that:

1. Regulatory warnings (R2 blocked/excluded) are acknowledged by humans but do
   not propagate as uncertainty signals to downstream agents
2. Mapping gaps (DA1 unmapped) are recorded for audit but do not inflate
   downstream uncertainty
3. CP1 receives clean, approved signals — not a mix of approved scope and
   unresolved warnings

The CLI enforces this boundary when constructing the `approved_profile_context`
for CP1. Individual agents do not filter upstream data themselves; the CLI
relays only the approved subset.

> **Isolated memory is the assurance authority.** The approved R2 in-scope
> findings + DA1 data mapping + the approval act form the sole epistemic reality
> for D1/D2/CP1 and beyond. See the "Isolated memory is the single source of
> truth for assurance" rule in `AGENTS.md` (Critical Architecture Rules).

---

## D1 — Data Execution (Regulation CLI Mode)

D1 receives only `handoff_data_request` from DA1's approved scope. No LLM
planning occurs. The handoff request is normalized, validated against the
runtime catalog, and executed deterministically via `HandoffDataExecutor`.

D1 has its own agent-local shelf (`registry_profiles/<registry_id>/active.json`)
with query spec + result summary. Raw tabular data remains in the EvidenceStore.
D1 shelf is promoted automatically (no human approval required).

EvidenceStore keys use `registry_id` pattern (e.g. `data_product_uc1_ets1_monitoring`)
for consistency with the shelf pattern.

---

## On-demand hub for agent-structure detail

This README is the routing point AGENTS.md points to whenever a task touches
`agents/` structure — a new use case's agent chain, the Person/Coalition
prompt-style contract, or the EX1/EX2 pattern. Read the relevant sub-README
before adding or changing an agent:

- **Output-stage agents (P1/P2/P3/P4-P6, S1/F1/F2, C1/C2, EX1/EX2):**
  `agents/output/README.md` — the Person (P) vs Coalition prompt-style
  contract, and each role agent's convergence/composition function.
- **UC4 document-chain agents (RC1.x/RM1.x/RD3, plus CP1/TS1 reuse):**
  `agents/regulation/README.md`'s "UC4 document chain" section.
- **RD1/RD2 (deterministic UC4 parsing, not agents — see below):**
  `src/epoch_switch/corpus/README.md`.

## Runtime Classification Labels

Each prompt file starts with a comment block that declares what the prompt
actually does at runtime.

- `active_llm_prompt`: loaded directly as the LLM instruction. Editing this can
  materially change agent decisions. DA1 is the main example.
- `tool_backed_contract_prompt`: mostly a manifest/contract. The real work is
  performed by a deterministic Python tool, and the prompt documents the role,
  IO contract, and related script paths.
- `hybrid_llm_tool_contract`: deterministic profiling produces all measured
  facts, while constrained LLM stages plan evidence requirements and adjudicate
  a structured decision. D2 is the main example.
