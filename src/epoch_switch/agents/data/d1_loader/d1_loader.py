"""D1 — Data Loader for approved regulation-CLI handoffs.

Agent prompt: epoch_switch/agents/data/d1_loader/D1.md.
Tools (self-contained, all co-located in this folder):
- d1_data_request_planner.py — normalization/validation helpers
- d1_data_executor.py      — deterministic pandas executor
- d1_handoff_executor.py   — deterministic executor for regulation CLI handoff flow
"""
from __future__ import annotations

from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.data_catalog import DataCatalogBuilder
from .d1_handoff_executor import HandoffDataExecutor


class DataLoaderAgent(BaseAgent):
    agent_id = "D1"
    can_fill = ["ingestor", "worker", "pos_1"]

    def execute(self, capsule: str, envelope: CaseEnvelope, evidence_store: EvidenceStore) -> Message:
        """Reject the removed topology-dispatch execution path."""
        raise RuntimeError(
            "D1 only supports regulation CLI handoff execution. "
            "Use DataLoaderAgent.execute_from_handoff()."
        )

    def _parse_output(self, result: dict, envelope: CaseEnvelope, duration_ms: int) -> Message:
        return Message.new(
            sender=self.agent_id,
            receiver="broadcast",
            case_id=envelope.case_id,
            message_type="evidence",
            payload=result,
            confidence=1.0,
        )

    def execute_from_handoff(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        handoff_request: dict,
    ) -> Message:
        """Execute D1 from an approved handoff_data_request.

        Regulation CLI mode only. No LLM planning.
        handoff_request comes from DA1's approved_mapping_scope.handoff_data_request.
        Fails if handoff_request is missing or validation fails.

        EvidenceStore keys use registry_id (envelope.usecase_ref) instead of
        case_id (UUID) for consistency with the shelf pattern.
        """
        registry_id = envelope.usecase_ref

        executor = HandoffDataExecutor()
        data_request, data_product, summary = executor.execute_from_handoff(
            registry_id=registry_id,
            handoff_request=handoff_request,
            envelope=envelope,
        )

        catalog = DataCatalogBuilder().build()

        catalog_key = f"data_catalog_{registry_id}"
        request_key = f"data_request_{registry_id}"
        product_key = f"data_product_{registry_id}"

        evidence_store.write(catalog_key, catalog)
        evidence_store.write(request_key, data_request)
        evidence_store.write(product_key, data_product)

        payload = {
            "store_key": product_key,
            "request_key": request_key,
            "catalog_key": catalog_key,
            "data_request": data_request,
            "summary": summary,
            "data_product_preview": {
                name: table.get("preview", [])
                for name, table in data_product.get("tables", {}).items()
            },
        }
        return Message.new(
            sender=self.agent_id,
            receiver="broadcast",
            case_id=envelope.case_id,
            message_type="evidence",
            payload=payload,
            evidence_refs=[catalog_key, request_key, product_key],
            confidence=1.0 if data_request.get("validation", {}).get("ok", True) else 0.7,
        )
