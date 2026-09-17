---
name: system-check
description: "Audit the epoch-regulation pipeline against its architecture rules — agent-local shelves, input_refs provenance, isolated-memory boundary, blind-pass pairs, EX1/EX2 advisory-only, topology/gate shape, validation restraint — after a pipeline run or an agent change. Read-only: reports findings, never edits code, never re-runs the pipeline. Invoke when the user says /system-check, asks for an architecture audit, or asks to verify the pipeline still follows its rules after adding or updating a use case or agent."
---

# /system-check

This is the canonical, tool-neutral home for the audit protocol. Both
`.claude/skills/system-check/SKILL.md` and `.codex/skills/system-check/SKILL.md` are
thin stubs that route here — read this file fresh every invocation rather than trusting
a cached summary, since `RULES.md` and `check.py` may have changed since the last run.

## Purpose

Adding or updating a use case keeps causing the same class of damage: a shelf written
to the wrong folder, one agent's output leaking into another agent's input,
`input_refs` provenance going missing, the isolated-memory boundary quietly eroding.
The rules that prevent this are all written down (mostly in `AGENTS.md`), but nothing
checks them after the fact. `/system-check` is that check — an on-demand, rule-by-rule
audit run **after** a pipeline run, or after an agent/use-case change, to confirm the
architecture still holds.

This skill is a verification instrument, not a fixer. It reports; it does not repair.
Any fix it recommends is applied only in a separate, user-approved turn.

## When to use

- The user types `/system-check`.
- The user asks for an architecture audit, a pipeline sanity check, or "does this still
  follow our rules" after adding a use case, editing an agent, or changing a shelf/store.
- Right after a `cli-fix-loop` round reaches its gold anchors, as a structural check
  distinct from the topology-match check `cli-fix-loop` already does.

## The rule catalog

Read `RULES.md` (same folder) before auditing. It holds 26 numbered rules (R01–R26)
across six blocks — pipeline & topology, shelf & provenance, epistemic boundary, gates
& conventions, LLM provenance, run identity & cross-run provenance — each with a
one-sentence rule statement, its source in `AGENTS.md` or the code, whether it is
decided by script/read/both, its failure signature, and the common false positive to
not flag. Do not restate the rules here; `RULES.md` is the single source of truth for
their content — this file only orders the audit process.

## How to run the audit

### Phase 1 — Scope

Determine what is being audited:
- If the user names a local run (or one was just produced), resolve its directory
  under `experiments/public/` or `experiments/<EPOCH_DATA_MODE>/`.
- Otherwise let `check.py` auto-detect the most recent run across both roots.
- If no run exists yet (fresh checkout, or the user asks for a source-only audit), pass
  `--static` and skip run-artifact rules (R01, R10, R26) — they become `N/A`, not `FAIL`;
  R25 still runs its live-shelf half under `--static`.
- Use `--last-runs N` (default 3) to change how many recent runs per registry R26
  audits, and `--registry <id>` to scope R26 to a single use case.
- State plainly at the top of your reply which run (or "static, no run") and which
  registry/pipeline family is being audited.

### Phase 2 — Deterministic pass

Run the checker and ingest its findings verbatim — do not re-derive what it already
decided. But a script finding closes only the mechanical slice of a rule it actually
looked at, never the rule's full written statement: every rule in `RULES.md` is now
either `read` (script does not attempt it) or `both` (script covers part, judgment
covers the rest). There is no rule left where a script PASS alone can close the row.

```
python skills/system-check/check.py --json
```

Use `--run-dir PATH` to pin a specific run, `--rules R05,R07` to scope to a subset
during iteration, `--static` for a source-only pass. Exit code `1` means at least one
`FAIL` — treat that as a signal to look closer, not as reason to stop the audit early.

`check.py` decides: R01 (run completeness), R02 (deterministic agents have no LLM call
surface), R05 (shelf path layout), R06/R08 (cross-agent import scan), R07 (`input_refs`
+ `payload_sha256` shape), R10 (EvidenceStore key pattern), R15's mechanical half (no
`ex1`/`ex2` leakage into a non-EX shelf payload), R19 (single `visibility=` routing
point), R22/R23/R24's mechanical half (aggregated `llm_provenance` / reasoning-effort /
token-accounting counts across all shelf records — one finding per rule, not one per
record), R25 (`input_refs[*].run_id` vs. each record's own `run_id`, across live
shelves and the current run's snapshot), R26 (per-registry last-N-runs completeness,
cross-run edges, and silent-drop detection via round-file-vs-snapshot comparison).
Everything else is a judgment call. Do not re-derive a `both` rule's mechanical half
(take the script's finding verbatim) — but never skip its judgment half on the
strength of a script PASS; the script's coverage is narrower than the rule for every
`both` rule in the catalog, `check_r06_r08`'s combined finding included.

Note on `check_r06_r08`: it emits one shared `R06/R08` finding, but R06 and R08 now
have distinct judgment halves (store-access boundary vs. agent self-sufficiency) — in
the Phase 4 report, split it into two table rows, both citing the same script evidence
line plus their own judgment-pass finding.

### Phase 3 — Judgment pass

Practically the whole catalog needs a judgment look now: every `read` rule (R03, R04,
R09, R11, R12, R13, R14, R16, R17, R18, R20, R21) plus the judgment half of every
`both` rule (R01, R02, R04, R05, R06, R07, R08, R10, R15, R19, R22, R23, R24, R25,
R26). Read `RULES.md`'s named source for that rule and the relevant code/run
artifacts, then rule on it. Each `both` rule's `Check.` field in `RULES.md` names
concretely what the script's mechanical half misses — read that field before judging,
it is not boilerplate. For R22–R24, the judgment half means spot-checking that a
populated `llm_provenance` block (once one exists) holds real request parameters and
provider usage figures rather than placeholders — the script only counts
presence/absence. For R25/R26, the judgment half means reading a flagged WARN's
`carried_over`/`carried_from_run_id` fields to confirm the reuse was intentional
resumption, not a symptom of a broken `--refresh`.

Hard rules for this pass:
- **Cite file:line for every FAIL.** A FAIL with no concrete location is not a finding.
- **Never mark PASS from absence of evidence.** If you did not find the relevant code
  path or artifact, mark `UNVERIFIED` and say what you looked for and where you looked.
- **Do not weaken a rule to make it pass.** If the codebase has genuinely outgrown a
  rule (for example, AGENTS.md still calling something "not part of the active
  package" when it demonstrably is), report it as a FAIL against the documentation and
  say so explicitly — updating `AGENTS.md` is the user's call, not something to paper
  over during an audit.
- Prefer `graphify query "<question>"` / `graphify path` / `graphify explain` over raw
  grep to locate logic, per `AGENTS.md`'s graphify rules, when `graphify-out/graph.json`
  exists.
- Respect `AGENTS.md`'s Validation Restraint section while auditing R18 itself: the
  audit is read-only, so this mainly means not proposing new guard clauses as "fixes"
  without flagging the risk and asking first, exactly as R18 requires of any other task.

### Phase 4 — Report

Emit the report in `REPORT_TEMPLATE.md`'s exact shape: a one-line verdict, the full
R01–R26 table, then a FAIL detail section, then an UNVERIFIED detail section if either
is non-empty. Do not silently drop a rule from the table — `N/A` is a valid, visible
row.

## Guardrails

- **Read-only, always.** Never edit source, never promote or archive a shelf, never
  invoke the CLI pipeline. If a fix is warranted, describe it in the FAIL detail section
  and stop — applying it is a separate, explicitly requested task.
- **One audit, one report.** Do not fix findings mid-audit and re-run to "clean up" the
  report; that hides what was actually found on the run being audited.
- After the report is delivered, append one entry to `ai_usage/steps_logs.md` per the
  workflow-logging rule in `AGENTS.md`: English, monotonically increasing step number,
  at most 4 bullets of at most 15 words each, noting which rules failed/warned if any.
- Exclude `.claude/worktrees/` (a stale full copy of `src/`), `.venv/`, and
  `graphify-out/` from any manual grep — `check.py` already excludes them.
