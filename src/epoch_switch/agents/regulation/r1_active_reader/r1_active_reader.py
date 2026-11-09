"""R1 regulation reader and possible-field discovery agent."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

from epoch_switch import config
from epoch_switch.agents.base import BaseAgent
from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import LLMClient, llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector
from .r1_document_loader import DOC_TOKEN_BUDGET, R1DocumentLoader
from . import r1_profile_store


EventCallback = Callable[[dict[str, Any]], None]
AGENT_SCRIPT = "src/epoch_switch/agents/regulation/r1_active_reader/r1_active_reader.py"
LOADER_SCRIPT = "src/epoch_switch/agents/regulation/r1_active_reader/r1_document_loader.py"
TOPOLOGY_TOKEN = re.compile(r"\bT(?:0[1-9]|1[0-2])\b", re.IGNORECASE)
R1_PROFILE_SCHEMA_VERSION = "5"
R1_LOADER_VERSION = "full_document_v2"
R1_MAX_REPAIR_ATTEMPTS = 3


class R1Finding(BaseModel):
    finding_id: str = Field(pattern=r"^RF-\d{3}$")
    requirement_type: Literal[
        "obligation",
        "threshold",
        "deadline",
        "method",
        "verification",
        "responsible_party",
        "scope",
    ]
    statement: str
    source_ref: str
    source_file: str
    page: int = Field(ge=1)
    evidence_excerpt: str


class R1PossibleField(BaseModel):
    field_id: str = Field(pattern=r"^PF-\d{3}$")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    role: Literal["entity", "time", "measure", "qualifier"]
    priority: Literal["core", "related"]
    description: str
    reason: str
    expected_unit: str | None = None
    expected_grain: str | None = None
    finding_refs: list[str] = Field(min_length=1)


class R1Profile(BaseModel):
    verdict: Literal["grounded", "insufficient"]
    summary: str
    selected_pages: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[R1Finding] = Field(default_factory=list)
    possible_fields: list[R1PossibleField] = Field(default_factory=list)
    missing_regulatory_context: list[str] = Field(default_factory=list)
    stop_reason: str | None = None
    source_hashes: dict[str, str] = Field(default_factory=dict)
    cache_signature: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_profile(self) -> "R1Profile":
        if self.verdict == "grounded" and (not self.findings or not self.possible_fields):
            raise ValueError("A grounded R1 profile needs findings and possible_fields")
        if self.verdict == "insufficient" and not self.stop_reason:
            raise ValueError("An insufficient R1 profile needs stop_reason")
        finding_ids = [item.finding_id for item in self.findings]
        field_ids = [item.field_id for item in self.possible_fields]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("R1 finding_id values must be unique")
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("R1 field_id values must be unique")
        known_findings = set(finding_ids)
        for field in self.possible_fields:
            unknown = sorted(set(field.finding_refs) - known_findings)
            if unknown:
                raise ValueError(
                    f"Possible field '{field.field_id}' cites unknown findings: {unknown}"
                )
        return self


class ActiveRegulationReader(BaseAgent):
    agent_id = "R1"
    can_fill = ["rules_provider", "spoke_a", "spoke_b", "pos_1", "base_left", "base_right"]

    def __init__(
        self,
        llm: LLMClient | None = None,
        loader: R1DocumentLoader | None = None,
    ):
        self._llm = llm
        self._loader = loader or R1DocumentLoader()
        self.last_llm_provenance: dict[str, Any] | None = None

    def execute_preselection(
        self,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
        event_callback: EventCallback | None = None,
        *,
        cache_mode: Literal["use", "refresh", "off"] = "use",
        force_refresh: bool = False,
    ) -> Message:
        if force_refresh:
            cache_mode = "refresh"
        if cache_mode not in {"use", "refresh", "off"}:
            raise ValueError(f"Unsupported R1 cache_mode: {cache_mode}")

        registry_id = envelope.usecase_ref
        system_prompt = self.role_prompt_path.read_text(encoding="utf-8")
        model = config.R1_MODEL if self._llm is not None else config.model_for_agent(self.agent_id)
        source_hashes = self._loader.hashes(envelope)
        signature = self._cache_signature(
            envelope=envelope,
            source_hashes=source_hashes,
            system_prompt=system_prompt,
            model=model,
        )
        if cache_mode == "use":
            persisted = r1_profile_store.load(registry_id)
            if persisted and persisted.get("cache_signature") == signature:
                pages, _ = self._loader.load(envelope)
                evidence_store.write(f"r1_document_pages_{envelope.case_id}", pages)
                evidence_store.write(f"regulation_profile_{envelope.case_id}", persisted)
                envelope.regulation_profile = persisted
                self._emit(
                    event_callback,
                    stage="r1_cache",
                    executor="python",
                    status="completed",
                    script=AGENT_SCRIPT,
                    detail=f"Reused approved discovery input for registry '{registry_id}'",
                    duration_ms=0,
                )
                return self._message(persisted, envelope)

        started = time.monotonic()
        pages, source_hashes = self._loader.load(envelope)
        evidence_store.write(f"r1_document_pages_{envelope.case_id}", pages)
        self._emit(
            event_callback,
            stage="document_loading",
            executor="python",
            status="completed",
            script=LOADER_SCRIPT,
            detail=self._page_detail(pages),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

        llm = self._llm if self._llm is not None else llm_for_agent(self.agent_id)[0]
        backend = config.backend_for_agent(self.agent_id)
        prov = ProvenanceCollector(provider=backend.kind, backend_preset=backend.name, model=model)
        if self._loader.estimate_tokens(pages) <= DOC_TOKEN_BUDGET:
            raw = self._call_llm(llm, system_prompt, envelope, pages, model, prov)
        else:
            raw = self._call_llm_chunked(llm, system_prompt, envelope, pages, model, prov)

        raw["source_hashes"] = source_hashes
        raw["cache_signature"] = signature

        # ── Validate with up to R1_MAX_REPAIR_ATTEMPTS self-repair attempts ──
        original_user_payload = {
            **self._registry_payload(envelope),
            "document_pages": self._pages_for_prompt(pages),
        }
        last_exc: Exception | None = None
        repaired_raw: dict[str, Any] | None = None
        for attempt in range(R1_MAX_REPAIR_ATTEMPTS):
            try:
                profile = R1Profile.model_validate(raw)
                self._validate_citations(profile, pages)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < R1_MAX_REPAIR_ATTEMPTS - 1:
                    raw = self._call_llm_repair(
                        llm,
                        system_prompt,
                        raw,
                        str(exc),
                        original_user_payload,
                        model,
                        prov,
                    )
                    raw["source_hashes"] = source_hashes
                    raw["cache_signature"] = signature
                    repaired_raw = raw
        else:
            # All attempts exhausted
            audit_path = r1_profile_store.save_validation_attempt(
                registry_id,
                raw_output=repaired_raw or raw,
                validation_error=str(last_exc),
                review_status="validation_failed",
            )
            raise ValueError(
                f"R1 returned invalid output after {R1_MAX_REPAIR_ATTEMPTS} attempts; "
                f"audit saved to {audit_path}"
            ) from last_exc

        if repaired_raw is not None:
            r1_profile_store.save_validation_attempt(
                registry_id,
                raw_output=repaired_raw,
                validation_error="(repaired — original error logged separately)",
                review_status="validation_repaired",
            )
        # ─────────────────────────────────────────────────────────────────────

        result = profile.model_dump()
        evidence_store.write(f"regulation_profile_{envelope.case_id}", result)
        envelope.regulation_profile = result
        self._emit(
            event_callback,
            stage="r1_discovery",
            executor="llm+python",
            status="completed",
            script=AGENT_SCRIPT,
            model=model,
            detail=f"{len(profile.possible_fields)} possible fields discovered",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        self.last_llm_provenance = prov.to_record()
        return self._message(result, envelope)

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        return self.execute_preselection(envelope, evidence_store)

    def _parse_output(
        self,
        result: dict[str, Any],
        envelope: CaseEnvelope,
        duration_ms: int,
    ) -> Message:
        return self._message(result, envelope)

    @staticmethod
    def _registry_payload(envelope: CaseEnvelope) -> dict[str, Any]:
        return {
            "registry_id": envelope.usecase_ref,
            "registry_question": envelope.natural_request,
            "expected_output": envelope.expected_output_family,
            "site_filter": envelope.site_filter,
            "time_window": envelope.time_window,
            "regulation_refs": envelope.regulation_refs,
        }

    def _call_llm(
        self,
        llm: LLMClient,
        system: str,
        envelope: CaseEnvelope,
        pages: list[dict[str, Any]],
        model: str,
        provenance: "ProvenanceCollector | None" = None,
    ) -> dict[str, Any]:
        user = {
            **self._registry_payload(envelope),
            "document_pages": self._pages_for_prompt(pages),
        }
        raw, _ = llm.complete_json(
            system,
            json.dumps(user, ensure_ascii=False, indent=2),
            model=model,
            max_tokens=config.R1_MAX_TOKENS,
            stream=True,  # R1's large token budget trips the Anthropic non-stream 10-min guard
            images=self._collect_images(pages),
            provenance=provenance,
        )
        return raw

    @staticmethod
    def _pages_for_prompt(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop embedded image bytes from the JSON text payload.

        Images are sent to the model as separate vision content blocks (see
        _collect_images); keeping data_b64 in the JSON text as well would send
        every figure twice. Alt text and path stay so the model can still cite
        which figure a page refers to.
        """
        result = []
        for page in pages:
            page = dict(page)
            if page.get("images"):
                page["images"] = [
                    {"path": img["path"], "alt": img["alt"]} for img in page["images"]
                ]
            result.append(page)
        return result

    @staticmethod
    def _collect_images(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []
        for page in pages:
            for img in page.get("images", []):
                images.append(
                    {
                        "media_type": img["media_type"],
                        "data_b64": img["data_b64"],
                        "alt": img["alt"],
                    }
                )
        return images

    def _call_llm_chunked(
        self,
        llm: LLMClient,
        system: str,
        envelope: CaseEnvelope,
        pages: list[dict[str, Any]],
        model: str,
        provenance: "ProvenanceCollector | None" = None,
    ) -> dict[str, Any]:
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        chars = 0
        max_chars = DOC_TOKEN_BUDGET * 4
        for page in pages:
            if current and chars + len(page.get("text", "")) > max_chars:
                chunks.append(current)
                current = []
                chars = 0
            current.append(page)
            chars += len(page.get("text", ""))
        if current:
            chunks.append(current)

        partials = [
            self._call_llm(llm, system, envelope, chunk, model, provenance)
            for chunk in chunks
        ]
        consolidation = {
            **self._registry_payload(envelope),
            "partial_profiles": partials,
            "instruction": (
                "Merge the partial profiles into the exact R1 output contract. "
                "Re-evaluate all possible fields against the registry question, "
                "remove duplicates and unrelated document fields, preserve core "
                "versus related priority, and keep only supplied citations."
            ),
        }
        raw, _ = llm.complete_json(
            system,
            json.dumps(consolidation, ensure_ascii=False, indent=2),
            model=model,
            max_tokens=config.R1_MAX_TOKENS,
            stream=True,  # R1's large token budget trips the Anthropic non-stream 10-min guard
            provenance=provenance,
        )
        return raw

    def _call_llm_repair(
        self,
        llm: LLMClient,
        system: str,
        invalid_output: dict[str, Any],
        validation_error: str,
        original_user_payload: dict[str, Any],
        model: str,
        provenance: "ProvenanceCollector | None" = None,
    ) -> dict[str, Any]:
        repair_user = {
            "invalid_output": invalid_output,
            "validation_error": validation_error,
            "original_input": original_user_payload,
            "instruction": (
                "Repair the invalid R1 output and return the exact R1 JSON contract. "
                "Use only the allowed requirement_type values: "
                "obligation, threshold, deadline, method, verification, "
                "responsible_party, scope. "
                "Use only the allowed possible_fields.role values: "
                "entity, time, measure, qualifier. "
                "Do not invent any new enum values."
            ),
        }
        raw, _ = llm.complete_json(
            system,
            json.dumps(repair_user, ensure_ascii=False, indent=2),
            model=model,
            max_tokens=config.R1_MAX_TOKENS,
            stream=True,  # match primary call — same guard applies on repair
            provenance=provenance,
        )
        return raw

    @staticmethod
    def _validate_citations(
        profile: R1Profile, pages: list[dict[str, Any]]
    ) -> None:
        if TOPOLOGY_TOKEN.search(json.dumps(profile.model_dump(), ensure_ascii=False)):
            raise ValueError("R1 profile contains a topology identifier")
        allowed = {
            (item["source_ref"], item["source_file"], item["page"])
            for item in pages
        }
        for page in profile.selected_pages:
            key = (page.get("source_ref"), page.get("source_file"), page.get("page"))
            if key not in allowed:
                raise ValueError(f"R1 selected an unsupplied page: {key}")
        for finding in profile.findings:
            key = (finding.source_ref, finding.source_file, finding.page)
            if key not in allowed:
                raise ValueError(
                    f"Finding {finding.finding_id} cites an unsupplied page"
                )

    @staticmethod
    def _cache_signature(
        *,
        envelope: CaseEnvelope,
        source_hashes: dict[str, str],
        system_prompt: str,
        model: str,
    ) -> dict[str, Any]:
        registry = {
            "registry_id": envelope.usecase_ref,
            "natural_request": envelope.natural_request,
            "expected_output": envelope.expected_output_family,
            "regulation_refs": envelope.regulation_refs,
            "regulation_sources": envelope.regulation_sources,
            "site_filter": envelope.site_filter,
            "time_window": envelope.time_window,
        }
        return {
            "schema_version": R1_PROFILE_SCHEMA_VERSION,
            "loader_version": R1_LOADER_VERSION,
            "model": model,
            "prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
            "r1_registry_input_sha256": hashlib.sha256(
                json.dumps(registry, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            "source_hashes": source_hashes,
        }

    @staticmethod
    def _message(profile: dict[str, Any], envelope: CaseEnvelope) -> Message:
        grounded = profile.get("verdict") == "grounded"
        return Message.new(
            sender="R1",
            receiver="DA1" if grounded else "broadcast",
            case_id=envelope.case_id,
            message_type="evidence" if grounded else "verdict",
            payload=profile,
            confidence=0.9 if grounded else 0.0,
        )

    @staticmethod
    def _page_detail(pages: list[dict[str, Any]]) -> str:
        files: dict[str, int] = {}
        for page in pages:
            files[page["source_file"]] = files.get(page["source_file"], 0) + 1
        return ", ".join(f"{name} · {count} pages" for name, count in files.items())

    @staticmethod
    def _emit(
        callback: EventCallback | None,
        *,
        stage: str,
        executor: str,
        status: str,
        script: str,
        detail: str,
        model: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if callback:
            callback(
                {
                    "stage": stage,
                    "executor": executor,
                    "status": status,
                    "script": script,
                    "detail": detail,
                    "model": model,
                    "duration_ms": duration_ms,
                }
            )
