"""D3 — TimeSeriesComputeAgent: deterministic computation agent.

Receives the locked D1 data product + approved DA1 mapping + registry recipe
parameters and produces a per-site compliance or reconciliation table via the
engine selected by ``computation_spec.kind``.

Agent type: Deterministic (AGENTS.md).  No LLM call, ever.
The recipe/spec is always derived from the registry — never generated or
modified by an LLM at runtime.

Engine dispatch:
    kind="threshold"     → SiteComplianceEngine          (UC1 §8/§16 binary obligation)
    kind="dual_method"   → DualMethodReconciliationEngine (UC2 ETS1 CO₂e A vs B)
    kind="consolidation" → DivisionConsolidationEngine    (UC3 CSRD ESRS E1 roll-up)
    None / missing       → falls back to "threshold" (UC1 backward-compatible default)

Input  : D1 data product (EvidenceStore) + handoff mapping + computation_spec /
         threshold_parameters
Output : D3Result, ReconciliationResult, or ConsolidationResult serialised as
         the Message payload
"""
from __future__ import annotations

from typing import Any

from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore

from .d3_time_series_engine import (
    ConsolidationResult,
    D3Result,
    DivisionConsolidationEngine,
    DualMethodReconciliationEngine,
    RecipeEntry,
    ReconciliationResult,
    SiteComplianceEngine,
)

# Human-readable labels for well-known threshold IDs.
# Extend as new regulations add thresholds; unknown IDs fall back to the ID.
_RULE_LABELS: dict[str, str] = {
    "enefg_8":  "§8 EnMS obligation (ISO 50001 / EMAS)",
    "enefg_16": "§16 waste-heat obligation",
}


class TimeSeriesComputeAgent(BaseAgent):
    """Deterministic per-site computation agent (Stage-2 agent).

    Bound to topology slots where ``can_fill`` matches ``"computation"``.
    For UC1 (EnEfG Direct topology) this is the sole Stage-2 actor.
    For UC2 (ETS1 Debate) this produces the dual-method reconciliation table
    that the downstream LLM Debate layer interprets.
    """

    agent_id = "D3"
    can_fill = ["computation"]

    def __init__(
        self,
        *,
        engine: SiteComplianceEngine | None = None,
        dual_engine: DualMethodReconciliationEngine | None = None,
        cons_engine: DivisionConsolidationEngine | None = None,
    ) -> None:
        self._engine = engine
        self._dual_engine = dual_engine
        self._cons_engine = cons_engine

    # ── Public entry point ─────────────────────────────────────────────────────

    def execute_computation(
        self,
        envelope: CaseEnvelope,
        store: EvidenceStore,
        *,
        registry_id: str,
        data_product: dict[str, Any],
        handoff: dict[str, Any],
        threshold_parameters: dict[str, Any],
        computation_spec: Any | None = None,
    ) -> Message:
        """Compute per-site results and return a Message.

        Args:
            envelope: CaseEnvelope for case_id and audit context.
            store: EvidenceStore (not read directly — passed for interface
                   consistency; D3 receives data_product explicitly).
            registry_id: the registry seed id (e.g. ``"uc1_enefg_threshold_check"``).
            data_product: D1's full data product dict
                ``{"tables": {<source_domain>: {"data": [...]}}}``.
            handoff: the human-approved DA1 handoff dict (used for audit
                     context; D3 does not re-issue data requests from it).
            threshold_parameters: registry ThresholdSpec dict, keyed by
                threshold_id.  Used for kind="threshold" path only.
            computation_spec: optional ``ComputationSpec`` from the registry.
                ``kind`` selects the engine.  When ``None``, falls back to
                kind="threshold" using threshold_parameters (UC1 default).

        Returns:
            Message with ``payload`` containing the model_dump of the result.
        """
        kind = getattr(computation_spec, "kind", None) or "threshold"

        if kind == "dual_method":
            dual_engine = self._dual_engine or DualMethodReconciliationEngine()
            result: ReconciliationResult = dual_engine.compute(
                data_product,
                computation_spec,
                registry_id=registry_id,
            )
            payload = result.model_dump()
        elif kind == "consolidation":
            cons_engine = self._cons_engine or DivisionConsolidationEngine()
            cons_result: ConsolidationResult = cons_engine.compute(
                data_product,
                computation_spec,
                registry_id=registry_id,
            )
            payload = cons_result.model_dump()
        else:
            source_domain = getattr(computation_spec, "source_domain", "energy") or "energy"
            recipe = _build_recipe(threshold_parameters)
            engine = self._engine or SiteComplianceEngine()
            threshold_result: D3Result = engine.compute(
                data_product,
                recipe,
                registry_id=registry_id,
                source_domain=source_domain,
            )
            payload = threshold_result.model_dump()

        payload["handoff_column"] = handoff.get("measures", [])
        payload["deterministic_summary"] = _deterministic_summary(payload)
        return self._message(envelope, payload)

    # ── BaseAgent contract ─────────────────────────────────────────────────────

    def _parse_output(
        self, result: dict, envelope: CaseEnvelope, duration_ms: int
    ) -> Message:  # pragma: no cover
        # BaseAgent.execute() uses an LLM path; D3 never reaches here.
        return self._message(envelope, result)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _message(self, envelope: CaseEnvelope, payload: dict[str, Any]) -> Message:
        return Message.new(
            sender=self.agent_id,
            receiver="broadcast",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            confidence=1.0,  # deterministic — fully reproducible
        )


# ── Recipe builder (threshold path) ───────────────────────────────────────────

def _build_recipe(threshold_parameters: dict[str, Any]) -> list[RecipeEntry]:
    """Build a deterministic list of RecipeEntry from registry ThresholdSpec.

    Args:
        threshold_parameters: ``seed.threshold_parameters`` — a dict mapping
            threshold_id to ThresholdSpec (or ThresholdSpec-compatible dict).
            The case controls this; no LLM input.

    Returns:
        Ordered list of RecipeEntry, one per threshold in the registry.
        Unknown threshold IDs get a formatted fallback label.
    """
    entries: list[RecipeEntry] = []
    for tid, spec in threshold_parameters.items():
        raw = _to_spec_dict(spec)
        entries.append(
            RecipeEntry(
                threshold_id=tid,
                rule_label=_RULE_LABELS.get(tid, tid.replace("_", " ").title()),
                column=raw.get("column", "energy_mwh"),
                threshold_value=float(raw.get("value", 0.0)),
                near_breach_ratio=float(raw.get("near_breach_ratio", 0.95)),
            )
        )
    return entries


def _deterministic_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a compact authoritative-numbers block from the already-computed payload.

    Always included in the D3 payload so every downstream agent (P1/P2/P3/S1/F1)
    receives the exact counts without having to re-count rows themselves.
    LLM agents must copy these verbatim — never re-derive from rows.
    """
    kind = payload.get("kind", "threshold")
    summary: dict[str, Any] = {
        "n_entries": len(payload.get("rows", [])),
        "n_sites":   payload.get("site_count", 0),
    }
    if kind == "dual_method":
        summary["n_flagged"] = payload.get("n_flagged", 0)
    elif kind == "threshold":
        summary["n_obligated"]   = payload.get("n_obligated", 0)
        summary["n_near_breach"] = payload.get("n_near_breach", 0)
        summary["n_compliant"]   = payload.get("n_compliant", 0)
    elif kind == "consolidation":
        summary["division_count"] = payload.get("division_count", 0)
        summary["portfolio_total_t"] = payload.get("portfolio_total_t", 0.0)
    return summary


def _to_spec_dict(spec: Any) -> dict[str, Any]:
    if hasattr(spec, "model_dump"):
        return spec.model_dump()
    if isinstance(spec, dict):
        return spec
    return {}
