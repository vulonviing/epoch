"""Shared batching + web-search execution helper for the Debate method agents.

``run_method_interpretation`` mirrors P1's ``interpret()`` body but is
method-agnostic: method identity lives entirely in the ``system_prompt``
supplied by the calling agent's ``.md`` file.  P2 and P3 each call this
function with their own prompt and config knobs.

Batching strategy — **batch by site** (all quarters of one site in one batch):
  D3 dual-method rows are keyed (location_id, year) with one row per site×quarter.
  Grouping by site means the interpreter sees the full quarterly picture for each
  site together, producing interpretively coherent multi-quarter observations.

No synthesis pass:
  Unlike P1 (which produces a chain-level synthesis after all batches), Debate
  method agents do NOT produce a synthesis pass.  Chain-level patterns across
  both methods are S1's job (the Reconciler).  The helper returns only the list
  of validated per-site-per-quarter rows.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Type

from epoch_switch.core.llm_client import LLMClient
from epoch_switch.core.llm_provenance import ProvenanceCollector

from .method_interpretation import MethodInterpretationRow


def run_method_interpretation(
    *,
    llm: LLMClient,
    system_prompt: str,
    r2_in_scope_findings: list[dict[str, Any]],
    d3_payload: dict[str, Any],
    row_model: Type[MethodInterpretationRow] = MethodInterpretationRow,
    model: str,
    max_tokens: int,
    batch_size: int,
    max_concurrency: int,
    web_max_uses: int,
    provenance: "ProvenanceCollector | None" = None,
) -> list[MethodInterpretationRow]:
    """Execute web-search-enabled LLM interpretation for one Debate method agent.

    Parameters
    ----------
    llm:
        LLM client instance (must support ``complete_json_websearch``).
    system_prompt:
        The agent's ``.md`` file content — loaded by the caller via
        ``self.role_prompt_path.read_text()``.  Method identity lives here.
    r2_in_scope_findings:
        Approved R2 regulation boundary (isolated memory).
    d3_payload:
        Full D3 ``ReconciliationResult`` payload (``rows`` + metadata).
    row_model:
        Pydantic row class to validate each output row.  Defaults to
        ``MethodInterpretationRow``; can be substituted in tests.
    model, max_tokens, batch_size, max_concurrency, web_max_uses:
        Agent-specific config knobs (from ``config.O3_*`` or ``config.O4_*``).

    Returns
    -------
    list[MethodInterpretationRow]
        Ordered list of validated per-site-per-quarter rows in site-major order
        (all quarters of a site together, matching the input grouping).
    """
    all_rows: list[dict[str, Any]] = d3_payload.get("rows", [])
    d3_meta: dict[str, Any] = {k: v for k, v in d3_payload.items() if k != "rows"}

    # Group rows by site (location_id → list of quarter rows).
    # Rows with no location_id fall into a single unnamed bucket.
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in all_rows:
        key = str(row.get("location_id") or row.get("location_name") or "").strip()
        if not key:
            key = "__no_location__"
        groups.setdefault(key, []).append(row)

    group_rows = list(groups.values())

    # Build batches of `batch_size` sites (flatten quarters into each batch).
    batches: list[list[dict[str, Any]]] = [
        [row for grp in group_rows[i : i + batch_size] for row in grp]
        for i in range(0, len(group_rows), batch_size)
    ]

    def _call_batch(batch_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        user = json.dumps(
            {
                "r2_in_scope_findings": r2_in_scope_findings,
                "d3_meta": d3_meta,
                "rows": batch_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        raw, _ = llm.complete_json_websearch(
            system_prompt,
            user,
            model=model,
            max_tokens=max_tokens,
            max_uses=web_max_uses,
            provenance=provenance,
        )
        return raw.get("rows", [])

    # Run batches concurrently; preserve site-major order in the result.
    max_workers = min(max_concurrency, len(batches)) if batches else 1
    ordered: list[list[dict[str, Any]]] = [[] for _ in batches]

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {
            pool.submit(_call_batch, batch): idx for idx, batch in enumerate(batches)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            ordered[idx] = future.result()  # raises on batch failure; caller handles

    flat_rows = [row for chunk in ordered for row in chunk]

    # Validate each row through the Pydantic schema (structure + type only).
    return [row_model.model_validate(r) for r in flat_rows]
