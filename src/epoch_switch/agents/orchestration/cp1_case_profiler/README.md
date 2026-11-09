# CP1 - Case Profiler

**Status:** CLI-integrated
**Type:** LLM interpretation with Pydantic and provenance guards
**Stage:** After the family-specific human-approved evidence boundary; before topology selection

CP1 owns the semantic routing profile, not measured data facts. Its upstream evidence
depends on `pipeline_family`:

1. **Registry demand** (`demand` key) — what the user asked and how the result must be
   delivered: `natural_request`, `expected_output`, `output_profile`, `assurance_profile`.
   This is the only registry channel that reaches CP1. Raw registry scope (`site_filter`,
   `time_window`, `regulation_refs`) is NOT forwarded; CP1 reads finalized scope from
   `approved_scope.handoff_data_request` and `r2_in_scope_findings`.
2. **Finalized upstream artifacts** — R2/DA1 for tabular cases; approved RC1/RM1
   evidence summaries for document cases.
   R1's raw findings and possible_fields remain in R1's shelf for audit; they are not
   forwarded to CP1. Human per-field decisions (approve/unresolved/exclude) remain in DA1's
   shelf; CP1 sees only the net result in `approved_scope`.

CP1 is called by `regulation_cli.py` after R2 human approval, D1 data loading, and
`derived_case_facts` computation, as the final stage of the `build` / `refresh` / `edit`
pipeline. Inputs are passed in-process by the CLI orchestrator. CP1 does not read other
agents' shelves directly. Document cases pass `document_evidence` and leave
`approved_scope`, `r2_in_scope_findings`, `derived_case_facts`, and `data_quality`
null; their signal sources are `rc1`, `rm1`, and `human_review`, never absent stages.

**`output_profile` is a CP1 output decision** (activated). CP1 emits `output_profile` as the
`o` in `π(case, o, a)` and persists it in its own shelf (`active.json`) like every other
signal. The baseline is always `demand.output_profile` (registry declared). CP1 may refine
`output_type` or `form` when upstream evidence (D2 `blocked_claims`, R2 findings,
`derived_case_facts`) shows the declared form cannot be supported — but any change must be
justified in `limitations`. The human approves the full CP1 payload (including
`output_profile`) through the existing CP1 review gate; the selector reads `o` from that
approved shelf.

CP1 output is shown to the human reviewer. Only an approved CP1 profile is written to
`active.json` via `cp1_profile_store.promote_active`. A rejected profile is archived with
`status="human_rejected"`. The active set (`load_active_set`) is not considered ready
until R1, DA1, R2, D1, and CP1 all have consistent, chain-linked active records.

**Current scope:** `deadline_proximity_days` remains `null` (requires structured R1
deadline date extraction). `near_breach` is tracked only in D3 (per-site status bands);
it is not a CP1 signal.

CP1 uses the same agent-local persistence layout: `active.json` for its current
approved output and a tagged `archive/` for all prior outcomes.

## Signal Dependency Matrix

Signals are grouped into three blocks matching `case_profile.py`:

**Block A — Assurance artifact (primary output):** `assurance_artifact` is CP1's
central output; it names the required evidence artifact. TS1 uses it as a primary
routing signal while scoring the topology library.
The authority for assurance is the **isolated memory** (approved R2 findings + DA1
handoff), not the registry `demand.assurance_profile`. Registry demand is context only.

**Block B — Escalation (orthogonal to artifact selection)**

**Block C — Context (non-routing):** `uncertainty`, `task_type`, `output_profile` do
not select a topology; they describe the case and shape output form within a topology.

| Signal | Block | Registry | R2 | DA1/approved | D1 | D2 | Owner |
|---|---|---|---:|---:|---:|---:|---|
| `assurance_artifact` | A | `demand.assurance_profile` (context only) | primary evidence | approved mapping | no | calibration | CP1 — names the required artifact from the isolated memory |
| `assurance_level` | A | `demand.assurance_profile` (context only) | required | no | no | later escalation only | CP1 derives from R2 evidence + isolated memory; demand is request, not floor |
| `dual_method_required` | A | required | required | no | no | calibration only | CP1 |
| `cluster_count` | A | required | required | useful | no | no | CP1 |
| `cross_functional_need` | A | required | required | no | no | no | CP1 |
| `risk_level` | B | required | required | no | no | no | CP1 |
| `deadline_proximity_days` | B | reference date | no | no | no | no | deterministic date calculation supplied to CP1 |
| `task_type` | C | required | required | no | no | no | CP1 — descriptive, not a topology driver |
| `uncertainty` | C | no | required | no | no | calibration only | CP1, interpretive only — does NOT trigger Debate |
| `output_profile` | C | `demand.output_profile` (declared baseline) | refinement evidence | no | no | refinement evidence | CP1 — confirms or refines; shapes form within topology; any change requires a limitation |

## D1 and derived_case_facts

D1 runs before CP1 in the pipeline. After D1 loads the observed data,
`derived_case_facts_builder.py` compares observed values against registry
`threshold_parameters` and produces a `derived_case_facts` dict with
`observed_value`, `threshold`, `calculation_method`, `evidence_assessable`,
`evidence_completeness`, `blocked_claims_summary`, and `window_divergence`.
This is passed to CP1 as a parameter for context (evidence completeness, window clamping).

D2 is integrated: `evidence_assessable` is derived from the D2 verdict, and
`data_quality` (including `routing_recommendation`) is passed to CP1 for the
combined-signal calibration rule. `d2` is a valid `signal_sources` value.

`deadline_proximity_days` needs neither D1 nor D2. R1 grounds the applicable
deadline and a deterministic Python date calculation supplies the day-distance.

`near_breach` (per-site threshold proximity band) belongs to D3 only and is not a
CP1 signal.
