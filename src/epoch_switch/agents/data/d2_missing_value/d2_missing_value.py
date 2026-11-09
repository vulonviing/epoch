"""D2 — mandatory hybrid data-quality preflight agent."""
from __future__ import annotations

import json
from typing import Any, Callable, Literal

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
)

from pathlib import Path

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.data_catalog import DataCatalogBuilder
from .d2_data_quality_engine import CORE_CHECKS, DataQualityEngine
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector


def _compact_json(value: Any) -> str:
    """Serialize complete prompt inputs without lossy character slicing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _decision_evidence_summary(
    plan: "RequirementPlan",
    handoff_data_request: dict[str, Any] | None,
) -> dict[str, Any]:
    """Make the LLM's already-approved decision evidence easy to distinguish."""
    handoff = handoff_data_request or {}
    return {
        "handoff_source_domain": handoff.get("source_domain"),
        "handoff_measures": handoff.get("measures", []),
        "handoff_filters": handoff.get("filters", {}),
        "required_tables": sorted(
            {
                table
                for claim in plan.claims
                for table in claim.required_tables
            }
        ),
        "required_fields": sorted(
            {
                field
                for claim in plan.claims
                for field in claim.required_fields
            }
        ),
        "required_value_fields": sorted(
            {
                field
                for claim in plan.claims
                for field, values in claim.required_values.items()
                if values not in (None, [], "")
            }
        ),
    }


class ClaimRequirement(BaseModel):
    claim_id: str
    claim: str
    core: bool = True
    required_tables: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    required_values: dict[str, list[Any]] = Field(default_factory=dict)
    required_grain: str | None = None
    minimum_history_years: int = 0
    proxy_allowed: bool = False
    scope_rationale: str = ""


class RequirementPlan(BaseModel):
    claims: list[ClaimRequirement] = Field(min_length=1)
    selected_checks: list[str]
    sample_strata: list[str] = Field(default_factory=list)
    planning_notes: list[str] = Field(default_factory=list)

    @field_validator("selected_checks")
    @classmethod
    def selected_checks_must_be_supported(cls, value: list[str]) -> list[str]:
        invalid = sorted(set(value) - CORE_CHECKS)
        if invalid:
            raise ValueError(f"unsupported checks: {invalid}")
        return value


class BlockedClaim(BaseModel):
    claim_id: str
    reason: str
    missing_evidence: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    issue_id: str
    probable_cause: str
    confidence: float = Field(ge=0, le=1)
    verification_step: str


class RemediationAction(BaseModel):
    issue_id: str
    immediate_containment: str
    root_cause_check: str
    permanent_fix: str
    owner_role: str
    verification_step: str


class QualityVerdict(BaseModel):
    verdict: Literal["pass", "warning", "partial", "insufficient"]
    evidence_completeness: float = Field(ge=0, le=1)
    max_severity: Literal["info", "low", "medium", "high", "critical"]
    summary: str
    assessable_claims: list[str] = Field(default_factory=list)
    blocked_claims: list[BlockedClaim] = Field(default_factory=list)
    observed_facts: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    remediation_actions: list[RemediationAction] = Field(default_factory=list)
    routing_recommendation: Literal[
        "normal", "increase_assurance", "restrict_claims", "stop"
    ]


class MissingValueAnalyst(BaseAgent):
    agent_id = "D2"
    can_fill = [
        "quality_gate",
        "analyst",
        "pos_2",
        "spoke_a",
        "spoke_b",
        "member",
        "secondary",
        "tier1_spec",
    ]

    def __init__(self, *, llm: Any | None = None, engine: DataQualityEngine | None = None):
        self._llm = llm
        self._engine = engine
        self.last_llm_provenance: dict[str, Any] | None = None

    def execute_from_scope(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        *,
        registry_id: str,
        in_scope_findings: list[dict[str, Any]],
        handoff_data_request: dict[str, Any],
        normalized_data_request: dict[str, Any],
        data_product: dict[str, Any],
    ) -> Message:
        """Registry-mode entry path: profiles the approved-handoff data population.

        This replaces the old ``execute()`` topology path for regulation CLI runs.
        All EvidenceStore keys are scoped by ``registry_id`` (not ``case_id``).
        Claim validation uses R2 ``in_scope_findings`` instead of
        ``assessment_contract["claims"]``.
        """
        cache_key = f"data_quality_verdict_{registry_id}"
        existing = evidence_store.read(cache_key)
        if existing is not None:
            return self._message_registry(registry_id, existing, reused=True)

        catalog = evidence_store.read(f"data_catalog_{registry_id}")
        if catalog is None:
            catalog = DataCatalogBuilder().build()

        model = config.D2_MODEL if self._llm is not None else config.model_for_agent(self.agent_id)
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)
        plan = self._plan_requirements_from_scope(
            envelope, catalog, in_scope_findings, handoff_data_request, prov
        )
        sample_limit = self._sample_limit(envelope.assurance_level)
        bundle = (self._engine or DataQualityEngine()).execute(
            envelope,
            plan.model_dump(),
            sample_limit=sample_limit,
            handoff=normalized_data_request,
            data_product=data_product,
        )
        verdict = self._adjudicate(
            envelope,
            plan,
            bundle.profile,
            bundle.samples,
            catalog=catalog,
            in_scope_findings=in_scope_findings,
            handoff_data_request=handoff_data_request,
            provenance=prov,
        )
        report = render_quality_report(
            envelope, plan.model_dump(), bundle.profile, verdict.model_dump()
        )

        keys = {
            f"data_quality_profile_{registry_id}": bundle.profile,
            f"data_quality_requirement_plan_{registry_id}": plan.model_dump(),
            f"data_quality_verdict_{registry_id}": verdict.model_dump(),
            f"data_quality_report_{registry_id}": report,
        }
        for key, value in keys.items():
            evidence_store.write(key, value)
        self.last_llm_provenance = prov.to_record()
        return self._message_registry(registry_id, verdict.model_dump(), reused=False)

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        existing = evidence_store.read(f"data_quality_verdict_{envelope.case_id}")
        if existing is not None:
            return self._message(envelope, existing, reused=True)

        catalog = evidence_store.read(f"data_catalog_{envelope.case_id}")
        if catalog is None:
            catalog = DataCatalogBuilder().build()

        plan = self._plan_requirements(envelope, catalog, capsule)
        sample_limit = self._sample_limit(envelope.assurance_level)
        bundle = (self._engine or DataQualityEngine()).execute(
            envelope, plan.model_dump(), sample_limit=sample_limit
        )
        verdict = self._adjudicate(
            envelope, plan, bundle.profile, bundle.samples, catalog=catalog
        )
        report = render_quality_report(envelope, plan.model_dump(), bundle.profile, verdict.model_dump())

        keys = {
            f"data_quality_profile_{envelope.case_id}": bundle.profile,
            f"data_quality_requirement_plan_{envelope.case_id}": plan.model_dump(),
            f"data_quality_verdict_{envelope.case_id}": verdict.model_dump(),
            f"data_quality_report_{envelope.case_id}": report,
        }
        for key, value in keys.items():
            evidence_store.write(key, value)
        return self._message(envelope, verdict.model_dump(), reused=False)

    def _plan_requirements(
        self, envelope: CaseEnvelope, catalog: dict[str, Any], capsule: str
    ) -> RequirementPlan:
        prompt = (Path(__file__).parent / "D2_requirements.md").read_text(
            encoding="utf-8"
        )
        user = (
            f"CASE ENVELOPE:\n{json.dumps(envelope.__dict__, indent=2, default=str)}\n\n"
            f"POSITION CONTEXT:\n{capsule[:2500]}\n\n"
            f"RUNTIME CATALOG (full visibility universe):\n{_compact_json(catalog)}\n\n"
            f"ALLOWED CHECKS:\n{json.dumps(sorted(CORE_CHECKS))}\n"
        )
        return self._validated_llm_call(
            prompt, user, RequirementPlan, max_tokens=config.D2_MAX_TOKENS
        )

    def _plan_requirements_from_scope(
        self,
        envelope: CaseEnvelope,
        catalog: dict[str, Any],
        in_scope_findings: list[dict[str, Any]],
        handoff_data_request: dict[str, Any],
        provenance: "ProvenanceCollector | None" = None,
    ) -> RequirementPlan:
        """Plan requirements using R2 in_scope_findings instead of assessment_contract."""
        prompt = (Path(__file__).parent / "D2_requirements_scope.md").read_text(
            encoding="utf-8"
        )
        user = (
            f"CASE ENVELOPE:\n{json.dumps(envelope.__dict__, indent=2, default=str)}\n\n"
            f"R2 IN-SCOPE FINDINGS (approved regulatory scope):\n"
            f"{_compact_json(in_scope_findings)}\n\n"
            f"APPROVED HANDOFF DATA REQUEST (human-approved and authoritative):\n"
            f"{_compact_json(handoff_data_request)}\n\n"
            f"RUNTIME CATALOG (full visibility universe):\n{_compact_json(catalog)}\n\n"
            f"ALLOWED CHECKS:\n{json.dumps(sorted(CORE_CHECKS))}\n"
        )
        return self._validated_llm_call(
            prompt,
            user,
            RequirementPlan,
            max_tokens=config.D2_MAX_TOKENS,
            provenance=provenance,
        )

    def _message_registry(
        self, registry_id: str, verdict: dict[str, Any], *, reused: bool
    ) -> Message:
        """Build a broadcast Message keyed by registry_id (regulation CLI mode)."""
        refs = [
            f"data_quality_profile_{registry_id}",
            f"data_quality_requirement_plan_{registry_id}",
            f"data_quality_verdict_{registry_id}",
            f"data_quality_report_{registry_id}",
        ]
        payload = dict(verdict)
        payload["preflight_reused"] = reused
        # Include a compact CP1 signal for downstream consumption.
        payload["cp1_signal"] = {
            "evidence_assessable": verdict.get("verdict") != "insufficient",
            "evidence_completeness": verdict.get("evidence_completeness"),
            "blocked_claims": verdict.get("blocked_claims", []),
            "routing_recommendation": verdict.get("routing_recommendation"),
        }
        # Use a synthetic case_id derived from registry_id so Message.new is satisfied.
        return Message.new(
            sender=self.agent_id,
            receiver="broadcast",
            case_id=registry_id,
            message_type="finding",
            payload=payload,
            evidence_refs=refs,
            confidence=verdict.get("evidence_completeness", 0.0),
        )

    def _adjudicate(
        self,
        envelope: CaseEnvelope,
        plan: RequirementPlan,
        profile: dict[str, Any],
        samples: list[dict[str, Any]],
        *,
        catalog: dict[str, Any] | None = None,
        in_scope_findings: list[dict[str, Any]] | None = None,
        handoff_data_request: dict[str, Any] | None = None,
        provenance: "ProvenanceCollector | None" = None,
    ) -> QualityVerdict:
        prompt = (Path(__file__).parent / "D2_adjudicator.md").read_text(
            encoding="utf-8"
        )
        user = (
            f"CASE ENVELOPE:\n{json.dumps(envelope.__dict__, indent=2, default=str)}\n\n"
            f"R2 IN-SCOPE FINDINGS (decision universe):\n"
            f"{_compact_json(in_scope_findings or [])}\n\n"
            f"APPROVED HANDOFF DATA REQUEST (decision universe):\n"
            f"{_compact_json(handoff_data_request or {})}\n\n"
            f"RUNTIME CATALOG (full visibility universe):\n"
            f"{_compact_json(catalog or {})}\n\n"
            f"DECISION-EVIDENCE REFERENCES (issues outside these references must be ignored):\n"
            f"{_compact_json(_decision_evidence_summary(plan, handoff_data_request))}\n\n"
            f"REQUIREMENT PLAN:\n{plan.model_dump_json(indent=2)}\n\n"
            f"DETERMINISTIC QUALITY PROFILE:\n"
            f"{_compact_json(profile)}\n\n"
            f"BALANCED ROW SAMPLE (context only; never derive rates from it):\n"
            f"{_compact_json(samples)}\n"
        )
        return self._validated_llm_call(
            prompt,
            user,
            QualityVerdict,
            max_tokens=config.D2_MAX_TOKENS,
            provenance=provenance,
        )

    def _validated_llm_call(
        self,
        system: str,
        user: str,
        model_type: type[BaseModel],
        *,
        max_tokens: int,
        post_validate: Callable[[Any], None] | None = None,
        provenance: "ProvenanceCollector | None" = None,
    ) -> Any:
        if self._llm is not None:
            llm, model = self._llm, config.D2_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        result, _ = llm.complete_json(
            system,
            user,
            temperature=0.0,
            max_tokens=max_tokens,
            model=model,
            stream=True,  # D2's larger token budget trips the Anthropic non-stream 10-min guard
            provenance=provenance,
        )
        try:
            validated = model_type.model_validate(result)
            if post_validate is not None:
                post_validate(validated)
            return validated
        except (ValidationError, ValueError) as exc:
            repair_user = (
                f"{user}\n\nYOUR PREVIOUS JSON FAILED VALIDATION:\n"
                f"{exc}\n\nEXPECTED JSON SCHEMA:\n"
                f"{_compact_json(model_type.model_json_schema())}\n\n"
                "Return a corrected JSON object only. Preserve the required "
                "field types exactly."
            )
            repaired, _ = llm.complete_json(
                system,
                repair_user,
                temperature=0.0,
                max_tokens=max_tokens,
                model=model,
                stream=True,  # match the primary call — same guard applies on repair
                provenance=provenance,
            )
            validated = model_type.model_validate(repaired)
            if post_validate is not None:
                post_validate(validated)
            return validated

    def _sample_limit(self, assurance_level: str) -> int:
        if assurance_level in {"audit_ready", "regulatory"}:
            return 120
        if assurance_level == "elevated":
            return 60
        return 24

    def _message(
        self, envelope: CaseEnvelope, verdict: dict[str, Any], *, reused: bool
    ) -> Message:
        refs = [
            f"data_quality_profile_{envelope.case_id}",
            f"data_quality_requirement_plan_{envelope.case_id}",
            f"data_quality_verdict_{envelope.case_id}",
            f"data_quality_report_{envelope.case_id}",
        ]
        payload = dict(verdict)
        payload["preflight_reused"] = reused
        return Message.new(
            sender=self.agent_id,
            receiver="broadcast",
            case_id=envelope.case_id,
            message_type="finding",
            payload=payload,
            evidence_refs=refs,
            confidence=verdict.get("evidence_completeness", 0.0),
        )

    def _parse_output(
        self, result: dict, envelope: CaseEnvelope, duration_ms: int
    ) -> Message:
        return self._message(envelope, result, reused=False)


def render_quality_report(
    envelope: CaseEnvelope,
    plan: dict[str, Any],
    profile: dict[str, Any],
    verdict: dict[str, Any],
) -> str:
    lines = [
        f"# Data Quality Preflight — {envelope.case_id}",
        "",
        f"- **Verdict:** `{verdict['verdict']}`",
        f"- **Evidence completeness:** {verdict['evidence_completeness']:.2f}",
        f"- **Maximum severity:** `{verdict['max_severity']}`",
        f"- **Routing:** `{verdict['routing_recommendation']}`",
        "",
        "## Summary",
        "",
        verdict["summary"],
        "",
        "## Claims",
        "",
    ]
    assessable = set(verdict.get("assessable_claims", []))
    blocked = {
        item["claim_id"]: item for item in verdict.get("blocked_claims", [])
    }
    for claim in plan.get("claims", []):
        claim_id = claim["claim_id"]
        if claim_id in assessable:
            status = "assessable"
        elif claim_id in blocked:
            status = f"blocked — {blocked[claim_id]['reason']}"
        else:
            status = "not adjudicated"
        lines.append(f"- **{claim_id}:** {claim['claim']} — `{status}`")

    lines.extend(["", "## Deterministic Findings", ""])
    for fact in verdict.get("observed_facts", []):
        lines.append(f"- {fact}")
    lines.extend(["", "## Hypotheses", ""])
    for item in verdict.get("hypotheses", []):
        lines.append(
            f"- `{item['issue_id']}` {item['probable_cause']} "
            f"(confidence {item['confidence']:.2f}); verify: {item['verification_step']}"
        )
    lines.extend(["", "## Remediation Plan", ""])
    for item in verdict.get("remediation_actions", []):
        lines.extend(
            [
                f"### {item['issue_id']}",
                f"- Immediate containment: {item['immediate_containment']}",
                f"- Root-cause check: {item['root_cause_check']}",
                f"- Permanent fix: {item['permanent_fix']}",
                f"- Owner role: {item['owner_role']}",
                f"- Verification: {item['verification_step']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Profile Reference",
            "",
            f"- Issues: {profile.get('issue_counts', {})}",
            f"- Sample rows sent to adjudicator: "
            f"{profile.get('sample_manifest', {}).get('selected_count', 0)}",
            "- Raw sample rows were not persisted.",
            "",
        ]
    )
    return "\n".join(lines)
