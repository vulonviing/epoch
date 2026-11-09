"""DA1 — Data-aware planner agent wrapper.

The runtime integration calls the same planner from D1/global data preparation.
This class exists so DA1 is also available as an explicit agent in future
topologies without duplicating the planning logic.

Agent prompt: epoch_switch/agents/data/da1_request_planner/DA1.md.
Tools (self-contained, co-located in this folder):
- da1_data_request_planner.py  — LLM-backed DataRequest planner
"""
from __future__ import annotations
from pathlib import Path

from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.data_catalog import DataCatalogBuilder
from .da1_data_request_planner import DataRequestPlanner
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore


class DataRequestPlannerAgent(BaseAgent):
    agent_id = "DA1"
    can_fill = ["data_planner"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_llm_provenance: dict | None = None

    def execute(self, capsule: str, envelope: CaseEnvelope, evidence_store: EvidenceStore) -> Message:
        catalog = DataCatalogBuilder().build()
        planner = DataRequestPlanner(prompt_path=Path(__file__).parent / "DA1.md")
        mapping_report = planner.plan(
            envelope=envelope, catalog=catalog, capsule=capsule
        )
        self.last_llm_provenance = planner.last_llm_provenance
        return Message.new(
            sender=self.agent_id,
            receiver="D1",
            case_id=envelope.case_id,
            message_type="request",
            payload={
                "catalog": catalog,
                "mapping_report": mapping_report,
                "data_request": mapping_report.get("suggested_request", {}),
            },
            evidence_refs=[],
            confidence=(
                1.0
                if mapping_report.get("validation", {}).get("ok", True)
                else 0.7
            ),
        )

    def _parse_output(self, result: dict, envelope: CaseEnvelope, duration_ms: int) -> Message:
        return Message.new(
            sender=self.agent_id,
            receiver="D1",
            case_id=envelope.case_id,
            message_type="request",
            payload=result,
            confidence=1.0,
        )
