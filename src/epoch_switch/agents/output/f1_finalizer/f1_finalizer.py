"""F1 — Finalizer: topology-agnostic final agent.

F1 is a pure LLM agent that reads whatever the upstream topology chain produced and
composes the reader-facing deliverable.  It is topology-agnostic: it does not know
whether the Debate or Direct chain fed it — it reads ``upstream_outputs`` (a dict
keyed by upstream agent ID) and ``demand.output_profile.form`` to decide both the
output shape and which prompt file to use.

Supported output forms:
  yes_no_alert        → F1Deliverable  (Direct chain; threshold-check use cases)
  structured_memo     → F1Memo         (Debate chain; dual-method memo use cases)
  numeric_measurement → F1Numeric      (Coalition chain; divisional attestation use cases)
  <any other>         → F1Memo         (fallback — memo shape is the safer generic)

Input contract:
  - upstream_outputs: dict keyed by upstream agent ID, e.g.:
      Direct  → {"P1": <p1_payload>}
      Debate  → {"P2": <p2_payload>, "P3": <p3_payload>, "S1": <s1_payload>}
  - r2_in_scope_findings: approved regulation boundary (citations only).
  - demand: registry demand_snapshot (natural_request, expected_output,
    output_profile, assurance_profile).

Human review gate:
  F1 is the final agent in every topology chain.  The gate is in the CLI, not here
  (AGENTS.md: Topology final-agent gate rule).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import LLMClient, llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector

from .f1_result import F1Deliverable, F1Memo, F1Numeric

_PROMPT_DIR = Path(__file__).parent

_FORM_MAP: dict[str, tuple[type, str]] = {
    "yes_no_alert":        (F1Deliverable, "F1_yes_no_alert.md"),
    "structured_memo":     (F1Memo,        "F1_structured_memo.md"),
    "numeric_measurement": (F1Numeric,     "F1_numeric_measurement.md"),
}
_DEFAULT_SCHEMA = (F1Memo, "F1_structured_memo.md")


class FinalizerAgent(BaseAgent):
    agent_id = "F1"
    can_fill = ["finalizer"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict[str, Any] | None = None

    def execute_finalization(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        upstream_outputs: dict[str, Any],
        r2_in_scope_findings: list[dict[str, Any]],
        demand: dict[str, Any],
        d3_summary: dict[str, Any] | None = None,
    ) -> Message:
        """CLI entrypoint.  Runs F1, writes evidence, returns a Message.

        No human review gate here — the gate is in the CLI orchestrator
        (AGENTS.md: Topology final-agent gate rule).
        """
        result = self.finalize(
            upstream_outputs=upstream_outputs,
            r2_in_scope_findings=r2_in_scope_findings,
            demand=demand,
            d3_summary=d3_summary,
        )
        payload = result.model_dump()
        evidence_store.write(f"deliverable_{envelope.case_id}", payload)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=[f"deliverable_{envelope.case_id}"],
            confidence=0.9,
        )

    def finalize(
        self,
        *,
        upstream_outputs: dict[str, Any],
        r2_in_scope_findings: list[dict[str, Any]],
        demand: dict[str, Any],
        d3_summary: dict[str, Any] | None = None,
    ) -> F1Deliverable | F1Memo | F1Numeric:
        """Core finalization logic.  Single LLM call; no web search, no batching."""
        form = demand.get("output_profile", {}).get("form", "")
        schema_cls, prompt_file = _FORM_MAP.get(form, _DEFAULT_SCHEMA)

        system = (_PROMPT_DIR / prompt_file).read_text(encoding="utf-8")
        if self._llm is not None:
            llm, model = self._llm, config.F1_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)

        user_body: dict[str, Any] = {
            "upstream_outputs": upstream_outputs,
            "r2_in_scope_findings": r2_in_scope_findings,
            "demand": demand,
        }
        if d3_summary:
            user_body["deterministic_summary"] = d3_summary

        user = json.dumps(user_body, ensure_ascii=False, indent=2)

        raw, _ = llm.complete_json(
            system,
            user,
            model=model,
            max_tokens=config.F1_MAX_TOKENS,
            stream=True,
            provenance=prov,
        )

        # Ensure registry_id is always present
        registry_id = demand.get("registry_id", "")
        if not registry_id:
            for v in upstream_outputs.values():
                if isinstance(v, dict) and v.get("registry_id"):
                    registry_id = v["registry_id"]
                    break
        raw.setdefault("registry_id", registry_id)

        self.last_llm_provenance = prov.to_record()
        return schema_cls.model_validate(raw)

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        raise NotImplementedError("Use execute_finalization via the CLI orchestrator.")

    def _parse_output(
        self,
        result: dict[str, Any],
        envelope: CaseEnvelope,
        duration_ms: int,
    ) -> Message:
        form = result.get("output_form", "")
        schema_cls, _ = _FORM_MAP.get(form, _DEFAULT_SCHEMA)
        parsed = schema_cls.model_validate(result)
        return Message.new(
            sender=self.agent_id,
            receiver="orchestrator",
            case_id=envelope.case_id,
            message_type="finding",
            payload=parsed.model_dump(),
        )
