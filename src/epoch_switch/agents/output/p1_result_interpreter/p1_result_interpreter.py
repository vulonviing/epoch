"""P1 — result interpreter: first step of the Direct sequential chain.

P1 is a pure LLM agent (lead_interpreter slot).  It receives the approved
regulation boundary (R2 in-scope findings) and the deterministic computation
results (D3 payload), then produces a per-site regulatory interpretation with
rich, location-aware reasoning grounded via live web search.

Execution model — concurrent batching + synthesis:
  1. D3 rows are grouped by location_id (fallback: location_name) so all of a
     site's rule-rows stay together.  Locations are then batched into groups of
     P1_BATCH_SIZE locations (default 5; ~10 rows/batch for UC1 with 2 rules).
     Output order is site-major (each location's rule-rows adjacent).
  2. Batches run concurrently (ThreadPoolExecutor, max P1_MAX_CONCURRENCY workers).
     Each batch calls the web-search-enabled LLM path (complete_json_websearch)
     so the model can ground location/current-event claims.
  3. After all batches complete, a cheap synthesis LLM call (no web) reads the
     full result set and produces chain-level carried_caveats.
  4. P1Result is assembled from the concatenated rows + synthesis caveats.

Input contract (isolated memory + data):
  - r2_in_scope_findings: approved R2 regulation boundary.
  - d3_payload: D3 computation results (per-site status, gap, geo fields, etc.).

Deliberately excluded from P1's input:
  - CP1 profile (its job — topology selection — ended at TS1; it is not part of
    the isolated memory).
  - DA1 handoff (already consumed by D1/D3; D3 payload is the observable result).
  - Registry demand (R2 is the approved, refined form of the original registry
    scope; re-forwarding the raw registry could contradict the approved boundary).

P1 does NOT format the final deliverable.  That is F1's job (finalizer slot).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import LLMClient, llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector

from .p1_result import P1Result, SiteInterpretation

# ── Synthesis prompt (chain-level caveats, no web search) ─────────────────────
_SYNTHESIS_SYSTEM = """\
You are P1-synthesis, the final step of the P1 batch interpretation pass.

You receive a summary of per-site regulatory interpretations that were produced
by concurrent batches (each batch interpreted a subset of the sites).  Your job
is to identify chain-level patterns that no single batch could see on its own.

Produce ONLY a JSON object with one key:

{
  "carried_caveats": [
    "<chain-level observation — max 2 sentences each>"
  ]
}

Examples of chain-level caveats worth flagging:
- Most or all sites have years_used < 3 (regulation requires a 3-year average
  but the available data could not form it).
- A systematic gap pattern (e.g. all obligated sites are in the same region).
- A mismatch between the approved regulation threshold and the threshold D3
  applied across all sites.
- Any other portfolio-wide tension visible only in aggregate.

Do NOT repeat per-site caveats that are already in each row's carried_caveats.
If there are no genuine chain-level patterns, return an empty list.
Output ONLY the JSON object — no prose outside it.
"""


class ResultInterpreterAgent(BaseAgent):
    agent_id = "P1"
    can_fill = ["lead_interpreter"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict[str, Any] | None = None

    def execute_interpretation(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        r2_in_scope_findings: list[dict[str, Any]],
        d3_payload: dict[str, Any],
    ) -> Message:
        """CLI entrypoint.  Runs P1, writes evidence, returns a Message.

        No human review gate here — P1 is an intermediate Direct step.
        The human review gate fires only after the final agent (O2) produces
        the complete deliverable (AGENTS.md: Topology final-agent gate rule).
        """
        result = self.interpret(
            r2_in_scope_findings=r2_in_scope_findings,
            d3_payload=d3_payload,
        )
        payload = result.model_dump()
        evidence_store.write(f"result_interpretation_{envelope.case_id}", payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[f"result_interpretation_{envelope.case_id}"],
            confidence=0.9,
        )

    def interpret(
        self,
        *,
        r2_in_scope_findings: list[dict[str, Any]],
        d3_payload: dict[str, Any],
    ) -> O1Result:
        """Core interpretation logic.

        Splits D3 rows into batches, runs each batch concurrently with web
        search, then runs a final synthesis pass for chain-level caveats.
        """
        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.P1_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        all_rows: list[dict[str, Any]] = d3_payload.get("rows", [])
        batch_size = config.P1_BATCH_SIZE  # locations per batch

        # Group rows by location so all of a site's rule-rows stay together.
        # D3 is rule-major, so first-seen insertion order == site order from
        # the first rule block.  The resulting output is site-major (each
        # location's rule-rows adjacent), which is easier to audit.
        groups: dict[str, list[dict[str, Any]]] = {}
        n_missing_group = 0
        for row in all_rows:
            key = str(row.get("location_id") or row.get("location_name") or "").strip()
            if not key:
                key = "__no_location__"
                n_missing_group += 1
            groups.setdefault(key, []).append(row)

        group_rows = list(groups.values())
        # Batch by number of locations; flatten each location's rows into the batch.
        batches = [
            [row for grp in group_rows[i : i + batch_size] for row in grp]
            for i in range(0, len(group_rows), batch_size)
        ]

        # D3 metadata forwarded to each batch (without the full rows list).
        d3_meta = {k: v for k, v in d3_payload.items() if k != "rows"}

        def _interpret_batch(batch_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            """Interpret one batch of D3 rows with web-search grounding.

            Returns the raw list of row dicts (validated by the caller after
            all batches complete).
            """
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
                system,
                user,
                model=model,
                max_tokens=config.P1_MAX_TOKENS,
                max_uses=config.P1_WEB_MAX_USES,
                provenance=prov,
            )
            return raw.get("rows", [])

        # Run all batches concurrently; preserve original row order.
        max_workers = min(config.P1_MAX_CONCURRENCY, len(batches)) if batches else 1
        ordered_results: list[list[dict[str, Any]]] = [[] for _ in batches]

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {
                pool.submit(_interpret_batch, batch): idx
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                ordered_results[idx] = future.result()  # raises on batch failure

        site_rows_raw = [row for chunk in ordered_results for row in chunk]

        # Validate each row through the Pydantic schema.
        site_interpretations = [SiteInterpretation.model_validate(r) for r in site_rows_raw]

        # Synthesis pass: chain-level caveats across the full result set.
        chain_caveats = self._synthesise_caveats(llm, model, site_interpretations, d3_meta, prov)

        # Deterministic warning: rows that had neither location_id nor location_name
        # were grouped into one null bucket.  Surface so the reviewer notices.
        if n_missing_group > 0:
            chain_caveats = [
                f"{n_missing_group} row(s) had no location_id or location_name "
                f"and were grouped into a single batch without site-level isolation.",
                *chain_caveats,
            ]

        self.last_llm_provenance = prov.to_record()
        return P1Result(
            registry_id=d3_payload.get("registry_id", ""),
            rows=site_interpretations,
            carried_caveats=chain_caveats,
        )

    def _synthesise_caveats(
        self,
        llm: LLMClient,
        model: str,
        rows: list[SiteInterpretation],
        d3_meta: dict[str, Any],
        provenance: "ProvenanceCollector | None" = None,
    ) -> list[str]:
        """Run the cheap synthesis pass that produces chain-level carried_caveats.

        No web search — reads only the full per-site result set + D3 metadata.
        """
        summary = [
            {
                "location_name": r.location_name,
                "country_name": r.country_name,
                "cdp_region": r.cdp_region,
                "rule_id": r.rule_id,
                "status": r.status,
                "gap": r.gap,
                "carried_caveats": r.carried_caveats,
            }
            for r in rows
        ]
        user = json.dumps(
            {"site_summaries": summary, "d3_meta": d3_meta},
            ensure_ascii=False,
            indent=2,
        )
        raw, _ = llm.complete_json(
            _SYNTHESIS_SYSTEM,
            user,
            model=model,
            max_tokens=config.P1_MAX_TOKENS,
            stream=True,
            provenance=provenance,
        )
        return raw.get("carried_caveats", [])

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        """BaseAgent stub — not used by the CLI; real entrypoint is execute_interpretation."""
        raise NotImplementedError("Use execute_interpretation via the CLI orchestrator.")

    def _parse_output(
        self,
        result: dict[str, Any],
        envelope: CaseEnvelope,
        duration_ms: int,
    ) -> Message:
        parsed = O1Result.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
