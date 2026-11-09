# ex_commentator/ — EX1, EX2 (all use cases, every human gate)

**Status:** active. **Type:** LLM (pure reasoning + web search tool; see
AGENTS.md Agent Type Classification). **Stage:** EX1 runs once before every
Stage-1 human gate; EX2 runs once before every Stage-2 human gate.

One Python class (`ExternalCommentatorAgent`), two `agent_id` instances: `EX1`
(pre-Stage-1-gate) and `EX2` (pre-Stage-2-gate). Same deliberate deviation as
`persona_assessor/` (P4/P5/P6) — the two stages differ only in prompt framing
(`EX1.md` / `EX2.md`) and in which gate payload they receive, not in code or
output schema, so one class instantiated twice avoids duplicating identical
logic. `BaseAgent.role_prompt_path` resolves from `type(self)` (this module's
directory) plus `self.agent_id`, so each instance loads its own `.md` file
automatically.

## What it does

EX reads exactly the payload the human is about to review at that gate (no
more, no less — never raw registry scope, R1 findings, or another agent's
shelf beyond what the gate itself shows) and produces 5-15 sourced bullet
points from a perspective outside Siemens' own pipeline: does recent activity
at an official institution support, challenge, add context to, or flag a gap
in what the pipeline is about to have approved.

Web search is restricted to the domain allowlist in `ex_sources.py` (EUR-Lex,
EFRAG, the European Commission, ESMA, EEA, ECHA, BAFA, DEHSt, Umweltbundesamt,
BMUV, IFRS/ISSB, GRI, UNFCCC, UNEP, IPCC, OECD, IEA, US EPA) via the
`web_search_20250305` tool's `allowed_domains` parameter — the same list feeds
both the tool restriction and the institution table appended to the system
prompt, so there is exactly one place this list is maintained.

## Hard rule: advisory only, never binding

EX's output is never consumed by any downstream agent and is never treated as
part of the isolated memory. It does not modify, override, or supersede any
approved R1/DA1/R2 finding, any promoted shelf, or any gate decision. It is
displayed once, at the gate, alongside the payload it comments on, and then
retired to its own shelf for audit. A human who disagrees with EX's reading
simply ignores it — approving or rejecting the gate is entirely unaffected by
whether EX's commentary exists.

This is why `ExternalCommentary`'s schema (`ex_result.py`) carries no field
that could be mistaken for a decision: no approve/reject, no status override,
only `point` / `stance` / `relevance` / `grounding` / `sources`.

## Shelf

`ex_profile_store.py` deviates from the one-store-per-agent convention for
the same reason as `persona_profile_store.py`: its functions take `agent_id`
as an explicit parameter, and shelves are kept separate per stage under
`registry_profiles/<EX1|EX2>/<registry_id>/`.

## `input_refs` — candidate variant

Unlike most agents, EX reads a gate payload that has **not yet been
promoted** (the human hasn't approved it yet). `input_refs["gate_payload"]`
therefore carries `{"artifact_id": None, "payload_sha256": <hash of the gate
payload>, "state": "candidate"}` instead of the standard
`{"artifact_id", "payload_sha256"}` pointing at an already-active shelf. Any
upstream shelf that *is* already active at gate time (e.g. R1/DA1 for the
tabular EX1) is still referenced in the standard form.

## Mandatory for new use cases

Every new use case, regardless of `pipeline_family`, must run EX1 before its
Stage-1 human gate and EX2 before its Stage-2 human gate. A human gate added
without a matching EX run is an architecture violation — see AGENTS.md's
"External commentary agents (EX1/EX2)" section.
