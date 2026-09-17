# EPOCH agent and stage index

This is the public routing index for every executable agent and deterministic
document stage. Runtime prompts are co-located with their agent. Store modules
own that agent's local shelf; cross-agent orchestration occurs only in
`regulation_cli.py`.

| ID | Type | Pipeline / shape | Runtime implementation | Prompt or contract | Result / shelf |
|---|---|---|---|---|---|
| R1 | LLM | Tabular foundation | `regulation/r1_active_reader/r1_active_reader.py` | `R1.md` | `r1_profile_store.py` |
| DA1 | LLM | Tabular foundation | `data/da1_request_planner/da1_request_planner.py` | `DA1.md` | `da1_profile_store.py` |
| R2 | LLM | Tabular foundation | `regulation/r2_scope_reviewer/r2_scope_reviewer.py` | `R2.md` | `r2_scope_profile.py`, `r2_profile_store.py` |
| D1 | Deterministic | Tabular foundation | `data/d1_loader/d1_loader.py`, local executor/planner modules | `D1.md` (contract) | `d1_profile_store.py` |
| D2 | Hybrid | Tabular foundation | `data/d2_missing_value/d2_missing_value.py`, `d2_data_quality_engine.py` | `D2_requirements.md`, `D2_requirements_scope.md`, `D2_adjudicator.md`; `D2.md` is the contract | `d2_profile_store.py` |
| D3 | Deterministic | All tabular shapes | `data/d3_time_series/d3_time_series.py`, `d3_time_series_engine.py` | none | `d3_profile_store.py` |
| CP1 | LLM | Both pipeline families | `orchestration/cp1_case_profiler/cp1_case_profiler.py` | `CP1.md` | `case_profile.py`, `cp1_profile_store.py` |
| TS1 | LLM | Both pipeline families | `orchestration/topology_selector/topology_selector.py` | `TS1.md` | `topology_selection.py`, `topology_selector_store.py` |
| P1 | LLM | Direct | `output/p1_result_interpreter/p1_result_interpreter.py` | `P1.md` | `p1_result.py`, `p1_profile_store.py` |
| P2 | LLM | Debate reader | `output/p2_energy_method_interpreter/p2_energy_method_interpreter.py` | `P2.md` | `p2_result.py`, `p2_profile_store.py` |
| P3 | LLM | Debate reader | `output/p3_reported_method_interpreter/p3_reported_method_interpreter.py` | `P3.md` | `p3_result.py`, `p3_profile_store.py` |
| S1 | LLM | Debate reconciliation | `output/s1_synthesizer/s1_synthesizer.py` | `S1.md` | `s1_result.py`, `s1_profile_store.py` |
| C1 | LLM | Coalition domain fan-out | `output/c1_divisional_attestor/c1_divisional_attestor.py` | `C1.md` | `c1_result.py`, `c1_profile_store.py` |
| C2 | LLM | Coalition convergence | `output/c2_coalition_synthesizer/c2_coalition_synthesizer.py` | `C2.md` | `c2_result.py`, `c2_profile_store.py` |
| F1 | LLM | Tabular finalizer | `output/f1_finalizer/f1_finalizer.py` | `F1_yes_no_alert.md`, `F1_structured_memo.md`, `F1_numeric_measurement.md` | `f1_result.py`, `f1_profile_store.py` |
| RC1.1 | LLM | Document blind pass | `regulation/rc1_change_classifier/rc1_1_blind_matcher.py` | `RC1_1.md` | `rc1_1_result.py`, `rc1_1_profile_store.py` |
| RC1.2 | LLM | Document binding pass | `regulation/rc1_change_classifier/rc1_change_classifier.py` | `RC1_2.md` | `rc1_result.py`, `rc1_profile_store.py` |
| RM1.1 | LLM | Document blind pass | `regulation/rm1_exposure_mapper/rm1_1_blind_exposure.py` | `RM1_1.md` | `rm1_1_result.py`, `rm1_1_profile_store.py` |
| RM1.2 | LLM | Document binding pass | `regulation/rm1_exposure_mapper/rm1_exposure_mapper.py` | `RM1_2.md` | `rm1_result.py`, `rm1_profile_store.py` |
| RD3 | Deterministic | Document join | `regulation/rd3_join/rd3_join.py` | none | `rd3_result.py`, `rd3_profile_store.py` |
| P4 | LLM | Document persona | `output/persona_assessor/persona_assessor.py` | `P4.md` | `persona_result.py`, `persona_profile_store.py` |
| P5 | LLM | Document persona | `output/persona_assessor/persona_assessor.py` | `P5.md` | `persona_result.py`, `persona_profile_store.py` |
| P6 | LLM | Document persona | `output/persona_assessor/persona_assessor.py` | `P6.md` | `persona_result.py`, `persona_profile_store.py` |
| F2 | Hybrid | Document finalizer | `output/f2_impact_finalizer/f2_impact_finalizer.py` | `F2.md` | `f2_result.py`, `f2_profile_store.py` |
| EX1 | LLM | Stage-1 advisory | `external/ex_commentator/ex_commentator.py` | `EX1.md` | `ex_result.py`, `ex_profile_store.py` |
| EX2 | LLM | Stage-2 advisory | `external/ex_commentator/ex_commentator.py` | `EX2.md` | `ex_result.py`, `ex_profile_store.py` |

RD1 and RD2 are deterministic corpus parsing/mapping stages, not agents. Their
implementations and contracts live under `epoch_switch/corpus/`.

Static topology bindings are defined in `selection/stage2_binding.py`:

- Direct: `D3 -> P1 -> F1`
- Debate: `D3 -> (P2 || P3) -> S1 -> F1`
- Coalition: `D3 -> C1 -> C2 -> F1`
- Document: `(P4 || P5 || P6) -> F2`
