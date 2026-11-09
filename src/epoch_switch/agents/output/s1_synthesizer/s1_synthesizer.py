"""S1 — Synthesizer: convergence step of the Debate chain.

S1 is a pure LLM agent (reconciler slot in the Debate topology).  It is the
first agent to see BOTH P2's energy-method interpretations and P3's reported-method
interpretations — this is the core Debate convergence point.

S1 does NOT use web search.  It synthesizes the two independent reads against each
other and against the D3-authoritative comparison numbers, producing a single
synthesized conclusion per site×quarter.

Batching: S1 batches by site (all years of one site in one batch), matching P2/P3's
strategy.  This prevents token-limit failures on large portfolios (e.g. UC2.1 with
53 site×year rows) while keeping per-site context intact across years.

Key Validation Restraint rule: S1 carries D3's ``abs_delta``, ``pct_diff``, and
``discrepancy_flag`` unchanged.  It interprets them but does NOT re-derive them.
It also does NOT content-compare P2 and P3 text verbatim — it reads the
interpretation and defensibility fields holistically.

Input contract:
  - o3_result: P2's per-site-per-quarter energy-method interpretations.
  - o4_result: P3's per-site-per-quarter reported-method interpretations.
  - r2_in_scope_findings: approved R2 regulation boundary.
  - d3_payload: D3 ReconciliationResult (for D3 metadata + row-level delta figures).

Excluded from S1's input:
  - CP1, DA1 handoff, raw registry demand / scope.
  - Any direct D1 data (already expressed through D3 → P2/P3).
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

from .s1_result import ReconciledMemoResult, ReconciledSiteQuarter


class SynthesizerAgent(BaseAgent):
    agent_id = "S1"
    can_fill = ["reconciler"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict[str, Any] | None = None

    def execute_reconciliation(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        o3_result: dict[str, Any],
        o4_result: dict[str, Any],
        r2_in_scope_findings: list[dict[str, Any]],
        d3_payload: dict[str, Any],
    ) -> Message:
        """CLI entrypoint.  Runs S1, writes evidence, returns a Message.

        No human review gate here — S1 is an intermediate Debate step.
        The human review gate fires only after the final agent (F1) produces
        the complete deliverable (AGENTS.md: Topology final-agent gate rule).
        """
        result = self.reconcile(
            o3_result=o3_result,
            o4_result=o4_result,
            r2_in_scope_findings=r2_in_scope_findings,
            d3_payload=d3_payload,
        )
        payload = result.model_dump()
        evidence_store.write(f"reconciliation_{envelope.case_id}", payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[f"reconciliation_{envelope.case_id}"],
            confidence=0.9,
        )

    def reconcile(
        self,
        *,
        o3_result: dict[str, Any],
        o4_result: dict[str, Any],
        r2_in_scope_findings: list[dict[str, Any]],
        d3_payload: dict[str, Any],
    ) -> ReconciledMemoResult:
        """Core synthesis logic.  Batched by site; no web search."""
        system = self.role_prompt_path.read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.S1_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        d3_meta = {k: v for k, v in d3_payload.items() if k != "rows"}
        d3_rows: list[dict[str, Any]] = d3_payload.get("rows", [])
        o3_rows: list[dict[str, Any]] = o3_result.get("rows", [])
        o4_rows: list[dict[str, Any]] = o4_result.get("rows", [])

        # Index o3 and o4 rows by (location_id, year) for fast lookup.
        def _key(row: dict[str, Any]) -> str:
            return f"{row.get('location_id', '')}|{row.get('year', '')}"

        o3_index = {_key(r): r for r in o3_rows}
        o4_index = {_key(r): r for r in o4_rows}

        # Group d3 rows by site so each batch sees all years of a site together.
        site_groups: dict[str, list[dict[str, Any]]] = {}
        for row in d3_rows:
            sid = str(row.get("location_id") or row.get("location_name") or "").strip() or "__no_location__"
            site_groups.setdefault(sid, []).append(row)

        group_list = list(site_groups.values())
        batch_size = config.S1_BATCH_SIZE

        # Build batches of batch_size sites; each entry carries its o3/o4 triples.
        batches: list[dict[str, Any]] = []
        for i in range(0, len(group_list), batch_size):
            batch_d3 = [row for grp in group_list[i : i + batch_size] for row in grp]
            batch_o3 = [o3_index.get(_key(r), {}) for r in batch_d3]
            batch_o4 = [o4_index.get(_key(r), {}) for r in batch_d3]
            batches.append({"d3_rows": batch_d3, "o3_rows": batch_o3, "o4_rows": batch_o4})

        def _call_batch(b: dict[str, Any]) -> dict[str, Any]:
            user = json.dumps(
                {
                    "r2_in_scope_findings": r2_in_scope_findings,
                    "d3_meta": d3_meta,
                    "d3_rows": b["d3_rows"],
                    "o3_rows": b["o3_rows"],
                    "o4_rows": b["o4_rows"],
                },
                ensure_ascii=False,
                indent=2,
            )
            raw, _ = llm.complete_json(
                system,
                user,
                model=model,
                max_tokens=config.S1_MAX_TOKENS,
                stream=True,
                provenance=prov,
            )
            return raw

        max_workers = min(config.S1_MAX_CONCURRENCY, len(batches)) if batches else 1
        ordered: list[dict[str, Any]] = [{} for _ in batches]

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {pool.submit(_call_batch, b): idx for idx, b in enumerate(batches)}
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                ordered[idx] = future.result()

        all_rows: list[ReconciledSiteQuarter] = []
        all_caveats: list[str] = []
        for batch_raw in ordered:
            all_rows.extend(
                ReconciledSiteQuarter.model_validate(r)
                for r in batch_raw.get("rows", [])
            )
            all_caveats.extend(batch_raw.get("carried_caveats", []))

        # Deduplicate caveats while preserving order.
        seen: set[str] = set()
        deduped_caveats: list[str] = []
        for c in all_caveats:
            if c not in seen:
                seen.add(c)
                deduped_caveats.append(c)

        self.last_llm_provenance = prov.to_record()
        return ReconciledMemoResult(
            registry_id=d3_payload.get("registry_id", ""),
            rows=all_rows,
            carried_caveats=deduped_caveats,
        )

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        """BaseAgent stub — not used by the CLI."""
        raise NotImplementedError("Use execute_reconciliation via the CLI orchestrator.")

    def _parse_output(
        self,
        result: dict[str, Any],
        envelope: CaseEnvelope,
        duration_ms: int,
    ) -> Message:
        parsed = ReconciledMemoResult.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
