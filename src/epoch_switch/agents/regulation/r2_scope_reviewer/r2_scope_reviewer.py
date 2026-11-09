"""R2 - human-approved regulation scope reviewer."""
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

from .r2_scope_profile import ClassifiedFinding, R2ScopeProfile
from . import r2_profile_store


R2_SCHEMA_VERSION = "1"
R2_MAX_REPAIR_ATTEMPTS = 3


class RegulationScopeReviewer(BaseAgent):
    agent_id = "R2"
    can_fill = ["regulation_scope_reviewer"]

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm
        self.last_llm_provenance: dict[str, Any] | None = None

    def review(
        self,
        *,
        registry_snapshot: dict[str, Any],
        r1_profile: dict[str, Any],
        approved_mapping_scope: dict[str, Any],
    ) -> dict[str, Any]:
        system = self.role_prompt_path.read_text(encoding="utf-8")
        user = {
            "registry": registry_snapshot,
            "r1_profile": r1_profile,
            "approved_mapping_scope": approved_mapping_scope,
            "scope_evidence_matrix": self._scope_evidence_matrix(
                r1_profile, approved_mapping_scope
            ),
        }
        if self._llm is not None:
            llm, model = self._llm, config.R2_MODEL
        else:
            llm, model = llm_for_agent(self.agent_id)
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)
        raw, _ = llm.complete_json(
            system,
            json.dumps(user, ensure_ascii=False, indent=2, default=str),
            model=model,
            max_tokens=config.R2_MAX_TOKENS,
            stream=True,  # max_tokens=32000 trips the Anthropic non-stream 10-min guard
            provenance=prov,
        )
        # ── Validate with up to R2_MAX_REPAIR_ATTEMPTS self-repair attempts ──
        last_exc: Exception | None = None
        repaired: dict | None = None
        for attempt in range(R2_MAX_REPAIR_ATTEMPTS):
            try:
                result = self._validate(
                    raw,
                    registry_snapshot=registry_snapshot,
                    r1_profile=r1_profile,
                    approved_mapping_scope=approved_mapping_scope,
                )
                if repaired is not None:
                    r2_profile_store.save_validation_attempt(
                        str(registry_snapshot.get("id")),
                        raw_output=repaired,
                        validation_error="(repaired — original error logged separately)",
                        repaired_output=None,
                        repair_error=None,
                        review_status="validation_repaired",
                    )
                self.last_llm_provenance = prov.to_record()
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < R2_MAX_REPAIR_ATTEMPTS - 1:
                    repair_user = {
                        "invalid_output": raw,
                        "validation_error": str(exc),
                        "original_input": user,
                        "instruction": "Repair the output and return the exact R2 JSON contract.",
                    }
                    raw, _ = llm.complete_json(
                        system,
                        json.dumps(repair_user, ensure_ascii=False, indent=2, default=str),
                        model=model,
                        max_tokens=config.R2_MAX_TOKENS,
                        stream=True,  # max_tokens=32000 trips the Anthropic non-stream 10-min guard
                        provenance=prov,
                    )
                    repaired = raw

        path = r2_profile_store.save_validation_attempt(
            str(registry_snapshot.get("id")),
            raw_output=repaired or raw,
            validation_error=str(last_exc),
            repaired_output=None,
            repair_error=None,
            review_status="validation_failed",
        )
        raise ValueError(
            f"R2 returned invalid scope classifications after {R2_MAX_REPAIR_ATTEMPTS} attempts; "
            f"audit saved to {path}"
        ) from last_exc
        # ─────────────────────────────────────────────────────────────────────

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        approved_scope = evidence_store.read(
            f"approved_mapping_scope_{envelope.case_id}"
        )
        if not approved_scope:
            raise ValueError("R2 requires an approved DA1 mapping scope")
        result = self.review(
            registry_snapshot={
                "id": envelope.usecase_ref,
                "natural_request": envelope.natural_request,
                "regulation_refs": envelope.regulation_refs,
                "regulation_sources": envelope.regulation_sources,
                "site_filter": envelope.site_filter,
                "time_window": envelope.time_window,
                "expected_output": envelope.expected_output_family,
            },
            r1_profile=envelope.regulation_profile,
            approved_mapping_scope=approved_scope,
        )
        evidence_store.write(f"r2_scope_profile_{envelope.case_id}", result)
        return Message.new(
            sender=self.agent_id,
            receiver="human_reviewer",
            case_id=envelope.case_id,
            message_type="verdict",
            payload=result,
            evidence_refs=[f"r2_scope_profile_{envelope.case_id}"],
            confidence=0.9,
        )

    def _parse_output(
        self,
        result: dict[str, Any],
        envelope: CaseEnvelope,
        duration_ms: int,
    ) -> Message:
        return Message.new(
            sender=self.agent_id,
            receiver="human_reviewer",
            case_id=envelope.case_id,
            message_type="verdict",
            payload=result,
        )

    @staticmethod
    def _validate(
        raw: dict[str, Any],
        *,
        registry_snapshot: dict[str, Any],
        r1_profile: dict[str, Any],
        approved_mapping_scope: dict[str, Any],
    ) -> dict[str, Any]:
        raw = RegulationScopeReviewer._normalize_citations(raw, r1_profile)
        profile = R2ScopeProfile.model_validate(raw)
        registry_id = str(registry_snapshot.get("id"))
        profile.registry_id = registry_id

        r1_findings = {
            item["finding_id"]: item for item in r1_profile.get("findings", [])
        }
        r1_fields = {
            item["field_id"]: item for item in r1_profile.get("possible_fields", [])
        }
        approved_ids = set(
            approved_mapping_scope.get("approved_field_ids", [])
        )
        known_ids = set(r1_findings)
        in_scope = {
            ref
            for item in profile.in_scope_findings
            for ref in item.r1_finding_refs
        }
        blocked = {item.r1_finding_id for item in profile.blocked_findings}
        excluded = {item.r1_finding_id for item in profile.excluded_findings}
        classified = in_scope | blocked | excluded
        unknown = sorted(classified - known_ids)
        if unknown:
            raise ValueError(
                f"R2 returned unknown R1 finding IDs: {unknown}"
            )
        missing = sorted(known_ids - classified)
        for finding_id in missing:
            profile.blocked_findings.append(
                ClassifiedFinding(
                    r1_finding_id=finding_id,
                    reason=(
                        "R2 did not establish a supported or excluded scope for "
                        "this finding; human review is required."
                    ),
                    related_field_ids=[],
                )
            )
            blocked.add(finding_id)
        scoped_refs: set[str] = set()
        for item in profile.in_scope_findings:
            refs = set(item.r1_finding_refs)
            if not refs <= in_scope:
                raise ValueError(
                    f"{item.r2_finding_id} references non-in-scope findings"
                )
            scoped_refs.update(refs)
            if not set(item.approved_field_ids) <= approved_ids:
                raise ValueError(
                    f"{item.r2_finding_id} references unapproved fields"
                )

        known_field_ids = set(r1_fields)
        for item in [*profile.blocked_findings, *profile.excluded_findings]:
            if not set(item.related_field_ids) <= known_field_ids:
                raise ValueError(
                    f"{item.r1_finding_id} references unknown related fields"
                )
            item.related_field_ids = sorted(
                field_id
                for field_id, field in r1_fields.items()
                if item.r1_finding_id in field.get("finding_refs", [])
            )
        return profile.model_dump()

    @staticmethod
    def _normalize_citations(
        raw: dict[str, Any],
        r1_profile: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = json.loads(json.dumps(raw))
        findings = {
            item["finding_id"]: item for item in r1_profile.get("findings", [])
        }
        for item in normalized.get("in_scope_findings", []):
            refs = item.get("r1_finding_refs", [])
            if any(ref not in findings for ref in refs):
                continue
            item["citations"] = [
                {
                    "source_ref": findings[ref]["source_ref"],
                    "source_file": findings[ref]["source_file"],
                    "page": findings[ref]["page"],
                    "evidence_excerpt": findings[ref]["evidence_excerpt"],
                }
                for ref in refs
            ]
        return normalized

    @staticmethod
    def _scope_evidence_matrix(
        r1_profile: dict[str, Any],
        approved_mapping_scope: dict[str, Any],
    ) -> list[dict[str, Any]]:
        approved_ids = set(
            approved_mapping_scope.get("approved_field_ids", [])
        )
        fields = r1_profile.get("possible_fields", [])
        return [
            {
                "r1_finding_id": finding["finding_id"],
                "approved_linked_field_ids": sorted(
                    field["field_id"]
                    for field in fields
                    if finding["finding_id"] in field.get("finding_refs", [])
                    and field["field_id"] in approved_ids
                ),
                "unapproved_linked_field_ids": sorted(
                    field["field_id"]
                    for field in fields
                    if finding["finding_id"] in field.get("finding_refs", [])
                    and field["field_id"] not in approved_ids
                ),
            }
            for finding in r1_profile.get("findings", [])
        ]
