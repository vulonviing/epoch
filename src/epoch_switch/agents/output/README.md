# Output-Stage Agents

Every stage-2 (post-CP1/TS1) topology agent lives here. AGENTS.md's "Person (P)
vs Team (T) prompt-style contract" section points to this file for the full
contract; this file does not restate AGENTS.md's hard rules (isolated-memory
boundary, same-run consumption, topology final-agent gate) — read both
together.

## Two prompt styles, one real structure

Output-stage agents fall into one of two prompt-writing styles depending on
their topology role. Both styles share the same `BaseAgent` base class and the
same registry/shelf infrastructure — only the prompt framing and the ownership
shape differ.

### Person (P) style — single-owner, isolated scope

Used by: **P1** (`p1_result_interpreter/`, Direct chain), **P2** / **P3**
(`p2_energy_method_interpreter/`, `p3_reported_method_interpreter/`, Debate
chain's two independent reads), and **P4/P5/P6** (`persona_assessor/`, UC4's
three risk-posture reads — one class, three `agent_id` instances per the
one-class-N-instances pattern).

Section layout in the prompt file:
1. `# Pn — <Title>` — identity header.
2. "You are Pn, the …" — singular you, one named role, one domain.
3. Authoritative counts from D3 (sites, rows, etc.).
4. Your job — what you decide, what you carry forward.
5. Inputs table — column-level contract for the data you receive.
6. Isolated-memory rule — you work from approved R2 + D3 only; you do not
   consult raw registry scope.
7. How-to instructions — per-row or per-batch processing steps.
8. Output contract — the Pydantic schema fields you must populate.
9. Validator rules — structural constraints Pydantic will enforce.
10. Closing imperative — one sentence ("Produce only the JSON. …").

Framing markers:
- Singular address throughout ("You are", "Your job is", "You receive").
- No cross-agent coordination language — each P agent works alone within its scope.
- Cross-agent handoff is described only as "Pn does NOT produce X — that is
  S1's / F1's job."

### Coalition structure — real, not the speculative T-style this replaced

Used by: **C1** (`c1_divisional_attestor/`) and **C2**
(`c2_coalition_synthesizer/`), UC3's Coalition topology
(`TOPOLOGY_AGENTS["Coalition"] = ["D3", "C1", "C2", "F1"]`).

This is **not** separate per-domain agent classes (a T1/T2/T3 pattern) — that
was the original speculative design and was never built.
`c1_portfolio_assembler/` and `division_team/` are empty legacy folders (no
files, no git history) left over from that plan; treat them as unused, not as
partially-implemented.

What is actually built:
- **C1** is one class doing **N independent isolated LLM calls, one per
  division** — `ThreadPoolExecutor`, one worker per division, and each call's
  payload contains only that division's rows, never another's. The division
  count is whatever D3 found, not fixed in code: a third division appearing in
  the data produces a third independent call with no code change. This is
  structurally the same isolation pattern P1 uses for per-location batching,
  applied to Coalition's per-division ownership instead. C1 attests each
  in-scope division's Scope 2 sub-total independently — no division can
  produce or sign off another division's figure, and this is enforced in code
  (separate payloads per call), not just by prompt instruction.
- **C2** is a **role agent**, structurally analogous to S1's convergence role
  in the Debate chain — it is the first agent to see every division's C1
  result together. C2 does not re-assess any division's figure or overrule
  C1's `data_volume_status`; it consolidates, flags portfolio-wide coverage
  gaps, and cross-checks against D3's deterministic portfolio total.

### Role agents (neither P nor Coalition)

**S1** (`s1_synthesizer/`), **F1** (`f1_finalizer/`), **F2**
(`f2_impact_finalizer/`), and EX1/EX2 (`agents/external/ex_commentator/`) are
role agents. Their prompts describe a convergence, composition, or (EX1/EX2)
outside-verification function rather than a single-owner analysis:

- **S1** is the Debate chain's convergence point — the first agent to see both
  P2's and P3's independent reads together, reconciling them against D3's
  authoritative comparison numbers.
- **F1** is topology-agnostic: it reads whatever upstream chain fed it
  (`upstream_outputs`, keyed by agent id) plus `demand.output_profile.form`,
  and composes the reader-facing deliverable without knowing which topology
  produced its input.
- **F2** is UC4's terminal agent — one LLM call (no batching, since the
  whole-portfolio comparison table is assembled from already-verified,
  already-independent inputs) followed by a deterministic per-standard
  rollup with no LLM involvement. Holds UC4's second and final human gate.

## EX1/EX2 — evaluation gold checkpoint

EX1/EX2's advisory-only hard rules live in AGENTS.md (unchanged by this
file). One implementation detail lives here instead: EX1/EX2 are full,
scored checkpoints in the evaluation gold packages
(`evaluation/model_comparison/<uc>/gold/`), with their own `contract.json`
field-class table and `review_template.json` entry. Being a scored gold
checkpoint does not make EX binding inside the pipeline — scoring EX only
measures whether a model surfaces comparable external concerns, never
whether downstream agents used them.
