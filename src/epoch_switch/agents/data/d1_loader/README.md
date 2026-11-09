# D1 — Data Loader

**Status:** Completed (regulation CLI pipeline integrated)
**Type:** Deterministic data executor
**Cluster:** data

## Scope

D1 is the primary data ingestion agent. In the regulation CLI pipeline
(`build`/`refresh`/`edit` commands), it runs automatically after R2 approval
and before CP1, receiving an approved `handoff_data_request` from DA1's
approved scope and executing it deterministically via `HandoffDataExecutor`.
D1 does not interpret regulation, calculate compliance gaps, or write business
conclusions. It may filter, join, aggregate, and derive only the measures
explicitly defined in the runtime data catalog.

D1 output is consumed by `derived_case_facts_builder.py` to produce
deterministic `near_breach` signals for CP1 enrichment.

## Pipeline Position (regulation CLI)

```
R1 → DA1 → (human) → R2 → (human) → D1 → derived_case_facts → CP1 → (human)
```

D1 runs before CP1 in the build pipeline. The standalone `data` command
remains available for independent re-execution.

## Execution Modes

### Regulation CLI mode (primary)

Invoked as part of `epoch-regulation build` (inline) or `epoch-regulation data`
(standalone, for re-execution after a data refresh).

Pipeline:
1. **[deterministic]** Read `handoff_data_request` from DA1's approved scope
2. **[deterministic]** `DataCatalogBuilder().build()` builds the runtime catalog
3. **[deterministic]** `HandoffDataExecutor.execute_from_handoff()` normalizes, validates, and executes
4. **[deterministic]** `derived_case_facts_builder.build_derived_case_facts()` computes near_breach
5. **[deterministic]** Writes EvidenceStore keys (using `registry_id` pattern)
6. **[deterministic]** Promotes D1 shelf record (automatic, no human approval)

No LLM planning. Fails if `handoff_data_request` is missing or validation fails.

## Inputs

- **handoff_data_request:** From DA1's `approved_mapping_scope.handoff_data_request`
- **CaseEnvelope fields:** `usecase_ref` (registry_id), `site_filter`, `time_window`
- **Data files (via `config.DATA_DIR`):** All 8 CSV files

## Outputs

### EvidenceStore keys

| Key | Content |
|---|---|
| `data_catalog_<registry_id>` | Runtime DataCatalog dict |
| `data_request_<registry_id>` | Normalized and validated DataRequest dict |
| `data_product_<registry_id>` | DataProduct with per-table summaries + row previews |

### Agent-local shelf

D1 maintains a registry-keyed shelf at `registry_profiles/<registry_id>/active.json`
following the same pattern as R1, DA1, R2, and CP1. The shelf stores:

- **data_request:** The normalized DataRequest spec
- **summary:** Compact DataProduct summary (tables, row counts, measures, grain, time range, quality)

Raw tabular data remains in the EvidenceStore. The shelf is promoted automatically
after successful execution (no human approval required).

## Files

- `d1_loader.py` — `DataLoaderAgent` class with `execute_from_handoff()` for regulation CLI mode.
- `d1_handoff_executor.py` — `HandoffDataExecutor` for regulation CLI mode.
- `derived_case_facts_builder.py` — Deterministic `build_derived_case_facts()` for near_breach computation from D1 data + registry thresholds.
- `d1_profile_store.py` — Registry-keyed shelf persistence.
- `D1.md` — role-capsule prompt: documents the tool-backed IO contract and hard rules.
- `__init__.py` — re-exports `DataLoaderAgent`, `HandoffDataExecutor`, `d1_profile_store`.

## Related

- **DA1** provides the `handoff_data_request` consumed in regulation CLI mode.
- **CP1** consumes `derived_case_facts` (built from D1 data + registry thresholds) to set `near_breach`.
- **D2** reads `data_product_<registry_id>` for its quality profiling.
- **`d1_data_request_planner.py`** — normalization and validation helpers used by `HandoffDataExecutor`.
- **`d1_data_executor.py`** — `DataRequestExecutor` that runs the pandas joins/filters.
- **`epoch_switch/core/data_catalog.py`** — shared runtime `DataCatalogBuilder` infrastructure.
