# D2 — Missing Value and Data Quality Analyst

**Status:** Completed
**Type:** Hybrid (LLM + script)
**Cluster:** data

## Scope

D2 is the mandatory data-quality preflight gate. It runs deterministic profiling over the D1 DataProduct, then uses two constrained LLM calls to plan evidence requirements and adjudicate a structured quality verdict. D2 does not impute values, repair data, aggregate business metrics, interpret regulation, or perform statistical outlier/trend analysis. Its verdict determines which claims are assessable and which are blocked, and it directly controls CLI routing: continue normally, restrict claims, increase assurance, or stop before CP1.

## Pipeline

1. **[deterministic]** Check the evidence store for `data_quality_verdict_<case_id>`. If present, return the cached verdict immediately (`preflight_reused = true`).
2. **[deterministic]** Read `data_catalog_<case_id>` from the evidence store (or build it fresh if absent).
3. **[LLM — step 1: requirements planning]** In regulation mode, send the exact R2 in-scope findings, final DA1 handoff, and complete agent-local v0.2 runtime catalog. The catalog is the visibility universe; R2 + handoff are the decision universe. Returns one validated `RequirementPlan` claim per R2 finding.
4. **[deterministic]** `DataQualityEngine().execute()` profiles every supported quality dimension over the handoff-filtered row population. `selected_checks` records LLM relevance recommendations; it does not suppress deterministic visibility. The engine produces a complete structured profile and a balanced sample (24 / 60 / 120 rows).
5. **[LLM — step 2: adjudication]** Send R2 scope, handoff, full catalog, complete deterministic profile, requirement plan, and balanced sample without character slicing. The LLM decides which observed issues affect approved claims and completely ignores unrelated tables, KPIs and fields.
6. **[deterministic]** `render_quality_report()` produces a human-readable markdown report from the plan, profile, and verdict.
7. **[deterministic]** Write four evidence_store keys and return a broadcast `Message`.

If either LLM call fails validation, one automatic repair call is attempted before raising. Additional structural guards require an exact R2 claim set and a complete, non-overlapping assessable/blocked verdict partition. Semantic scope relevance remains an LLM judgment.

### Quality checks available

`schema`, `completeness`, `duplicates`, `referential_integrity`, `validity`, `consistency`, `timeliness`, `coverage`, `sentinel_fill`, `source_kpi_reconciliation`.

### Verdict → routing mapping (enforced by Pydantic validator)

| `verdict` | `routing_recommendation` |
|---|---|
| `pass` | `normal` |
| `warning` | `increase_assurance` |
| `partial` | `restrict_claims` |
| `insufficient` | `stop` |

## Inputs

- **CaseEnvelope fields:** `case_id`, `assurance_level`, `regulation_refs`, `time_window`, `site_filter`.
- **Upstream evidence_store keys:** `data_catalog_<case_id>` (optional fallback), `data_product_<case_id>` (read by `DataQualityEngine` internally).
- **Prompt files:**
  - `D2.md` — role/contract prompt.
  - `D2_requirements.md` — system prompt for the planning LLM call (loaded via `Path(__file__).parent`).
  - `D2_adjudicator.md` — system prompt for the adjudication LLM call (loaded via `Path(__file__).parent`).

## Outputs

**evidence_store keys written:**

| Key | Content |
|---|---|
| `data_quality_profile_<case_id>` | Deterministic per-check results from `DataQualityEngine` |
| `data_quality_requirement_plan_<case_id>` | `RequirementPlan` dict (claims, selected_checks, strata) |
| `data_quality_verdict_<case_id>` | `QualityVerdict` dict (verdict, routing, completeness score, blocked claims) |
| `data_quality_report_<case_id>` | Human-readable markdown string |

**Message returned:**
- `message_type`: `"finding"`
- `receiver`: `"broadcast"`
- `confidence`: `evidence_completeness` float from the verdict (0–1)
- `evidence_refs`: all four keys above, plus `preflight_reused` boolean in payload

## Files

- `d2_missing_value.py` — `MissingValueAnalyst` class + `render_quality_report()` helper; Pydantic models `RequirementPlan`, `QualityVerdict`, `ClaimRequirement`, etc.
- `D2.md` — role-capsule prompt.
- `D2_requirements.md` — LLM system prompt for the planning step.
- `D2_adjudicator.md` — LLM system prompt for the adjudication step.
- `__init__.py` — re-exports `MissingValueAnalyst` and `render_quality_report`.

## Related

- **D1** produces `data_product_<registry_id>` and the normalized request consumed by `DataQualityEngine`.
- **CP1** receives D2's verdict and blocked-claims summary in its approved profile context.
- **`d2_data_quality_engine.py`** — `DataQualityEngine`: deterministic check runner and sample producer. Defines `CORE_CHECKS` and `COLUMN_POLICIES` (co-located).
- **`epoch_switch/core/data_catalog.py`** — shared runtime `DataCatalogBuilder` infrastructure.
- **`tests/test_d2_agent.py`** — unit tests for the two-step LLM flow, caching, and validation repair.
- **`tests/test_data_quality_engine.py`** — unit tests for deterministic profiling logic.
