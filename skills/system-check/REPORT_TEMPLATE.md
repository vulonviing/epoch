# system-check report template

Fixed output shape for the `/system-check` audit. Fill every row; never omit a rule.

```markdown
# system-check — <run label or "static, no run">

**Verdict:** <one line: e.g. "4 FAIL, 2 WARN, 20 PASS — shelf and provenance rules hold,
isolated-memory boundary has one confirmed leak, LLM provenance not yet implemented,
run-identity checks (R25/R26) clean on the last 3 runs.">

## Findings

| Rule | Status | Evidence | Suggested fix |
|------|--------|----------|----------------|
| R01  | PASS/WARN/FAIL/N-A/UNVERIFIED | <one line, file:line or path> | <only if not PASS> |
| R02  | ... | ... | ... |
| ...  | ... | ... | ... |
| R26  | ... | ... | ... |

## FAIL detail

For every row marked FAIL (skip this section if none):

### R<NN> — <name>
- **What broke:** <concrete description>
- **Where:** `<file>:<line>` or `<shelf path>`
- **Why it matters:** <one sentence, tie back to RULES.md's rule statement>
- **Suggested fix:** <one sentence — proposal only, not applied>

## UNVERIFIED detail

For every row marked UNVERIFIED (skip if none): one line each — what evidence would be
needed to decide, and where to look for it next time.
```

Status meanings:
- `PASS` — checked, holds.
- `WARN` — a known-acceptable deviation (e.g. EX1/EX2 legitimately absent) or a minor
  finding worth surfacing without blocking.
- `FAIL` — a rule is violated. Always cite file:line or an exact path.
- `N/A` — the rule does not apply to what was audited (e.g. `--static` run skipping
  run-artifact rules, or a thesis-only rule when no thesis files changed).
- `UNVERIFIED` — the judgment pass could not find enough evidence to decide either way.
  Never mark PASS from absence of evidence.

For a `Check: both` rule, the row's Status is the **worse of the two halves** — the
script's mechanical result and the judgment pass's result for what the script does not
cover. A script PASS with a judgment-pass FAIL is a FAIL row, never a PASS. Evidence
must cite both: the script's evidence line plus the judgment pass's own file:line
finding. `check_r06_r08` emits one combined script finding for two rules (R06, R08) —
report it as two separate table rows, each citing that same script evidence alongside
its own judgment-pass finding.
