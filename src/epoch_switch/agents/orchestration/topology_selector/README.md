# TS1 — Topology Selector

**Status:** CLI-integrated; stage-2 agent binding and execution are live
**Type:** LLM interpretation with Pydantic validation + human review gate
**Stage:** After CP1 human approval; before downstream output construction

TS1 implements the operative `π(case, o, a) -> topology` function from the EPOCH thesis.
It receives the human-approved CP1 case profile and emits a **confidence distribution
over Λ = {Direct, Debate, Coalition}**.  The CLI takes the argmax as the selected topology.
The approved CP1 payload is the sole case reality TS1 may use; it already encodes the
DA1/R2 human-approved isolated memory and the CP1 review decision.

## Input

- **Approved CP1 payload** — passed by the CLI orchestrator (the `payload` field of the
  active CP1 shelf record).  TS1 does NOT read any shelf directly; the CLI hands it the
  data.  TS1 does not re-read raw registry scope, R1 raw findings, blocked/excluded R2
  findings, or `expected_topology`.

## Output

- **TopologySelection** — a Pydantic-validated object containing:
  - `topology_scores` — distribution summing to 100 over Λ.
  - `selected_topology_id` — argmax (tie-break: Coalition > Debate > Direct).
  - `rationale`, `alternatives`, `signal_sources`.
  - `escalation_flagged` / `escalation_reason` — overlay, not a 4th topology.
  - `bindings: []` — stage-1 empty placeholder.
  - `limitations`.

## Invariants

- **Λ is closed.** The selector may choose but may not invent.
- **Scores sum to 100.** Enforced by `TopologySelection` model validator.
- **selected = argmax.** The validator rejects any mismatch.
- **Escalation is an overlay.** It sets a flag; the base topology still comes from Λ.
- **`expected_topology` never enters the prompt.** It is an eval gold anchor only.

## Shelf pattern

Same as every other agent:
```
registry_profiles/<registry_id>/active.json   ← current approved selection
registry_profiles/<registry_id>/archive/      ← all prior outcomes
```

Access only via `topology_selector_store`.  CLI is the only layer that imports
multiple store modules.  `input_refs` always declares `{"cp1": {artifact_id, payload_sha256}}`.

## Human review gate

Identical to CP1:
1. CLI renders the TS1 output (`render_ts1`).
2. Human types `[a]pprove` or `[r]eject`.
3. Approve → `promote_active`.  Reject → `archive_candidate(status="human_rejected")`.

## Topology philosophy

| Topology | Execution shape | Distinguishing feature |
|---|---|---|
| **Direct** | Sequential, dependent chain (1..N steps) | Each step receives the *prior step's output* + isolated memory; single ownership throughout. |
| **Debate** | 2..N independent parallel reads → reconciliation | Readers do not see each other's work before reconciliation; requires interpretive ambiguity. |
| **Coalition** | Parallel domain-specialist agents → synthesis | Each agent owns a distinct sub-result; multi-domain ownership required. |

**Critical boundary:** Direct steps are *dependent* — each builds on the last.
Debate agents are *independent* — neither sees the other before reconciliation.
An agent that simply deepens a prior agent's result is a Direct step, not Debate.

## Stage-2 binding

`bindings` remains `[]` in TS1's stage-1 payload. The CLI reads
`selected_topology_id` and resolves the concrete, static binding through
`selection/stage2_binding.py`: Direct `[D3, P1, F1]`, Debate
`[D3, [P2, P3], S1, F1]`, and Coalition `[D3, C1, C2, F1]`. TS1 chooses the
shape; it does not currently choose the number of Direct steps.
