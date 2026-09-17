"""Core types: CaseEnvelope, Message, Binding, RunResult."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
import uuid


@dataclass
class CaseEnvelope:
    case_id: str
    usecase_ref: str
    natural_request: str
    regulation_refs: list[str]
    site_filter: dict
    time_window: tuple[str, str]
    expected_output_family: str
    regulation_sources: dict[str, str] = field(default_factory=dict)
    time_grain: str = "year"
    regulation_profile: dict[str, Any] = field(default_factory=dict)
    assessment_contract: dict[str, Any] = field(default_factory=dict)

    # Filled by Case Profiler (orchestrator step 0)
    task_type: str | None = None
    risk_level: str | None = None                  # low | medium | high | critical
    uncertainty: str | None = None                 # low | medium | high
    evidence_completeness: float | None = None
    audit_urgency: str | None = None               # none | q | m | w | d
    deadline_proximity_days: int | None = None
    dual_method_required: bool = False
    cluster_count: int = 1
    assurance_level: str = "routine"               # routine | elevated | audit_ready | regulatory
    cross_functional_need: bool = False
    data_quality_verdict: str | None = None
    data_quality_max_severity: str | None = None
    assessable_claims: list[str] = field(default_factory=list)
    blocked_claims: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def new(cls, **kwargs) -> "CaseEnvelope":
        kwargs.setdefault("case_id", str(uuid.uuid4())[:8])
        return cls(**kwargs)


@dataclass
class Message:
    msg_id: str
    case_id: str
    sender_agent: str
    receiver: str                        # agent_id or "broadcast"
    message_type: Literal[
        "request", "response", "claim", "evidence",
        "finding", "score", "projection", "verdict",
        "dissent", "provenance", "handoff", "error"
    ]
    payload: dict
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float | None = None
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @classmethod
    def new(cls, sender: str, receiver: str, case_id: str,
            message_type: str, payload: dict, **kwargs) -> "Message":
        return cls(
            msg_id=str(uuid.uuid4())[:8],
            case_id=case_id,
            sender_agent=sender,
            receiver=receiver,
            message_type=message_type,
            payload=payload,
            **kwargs,
        )


@dataclass
class Binding:
    """Orchestrator Step 2 output: one slot → one agent."""
    position_id: str
    agent_id: str
    case_mission: str
    rationale: str


@dataclass
class TopologyDecision:
    selected_topology_id: str
    rationale: str
    evidence_refs: list[str]
    alternatives: list[dict[str, str]]
    bindings: list[Binding]


@dataclass
class AgentRun:
    agent_id: str
    position_id: str
    topology_id: str
    capsule_hash: str           # sha256[:8] of the capsule prompt
    output: Message | None
    error: str | None
    duration_ms: int
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class RunResult:
    case_id: str
    usecase_ref: str
    topology_decision: TopologyDecision | None
    agent_runs: list[AgentRun]
    artifact: str               # final markdown artifact
    artifact_path: str
    preflight_status: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat())
