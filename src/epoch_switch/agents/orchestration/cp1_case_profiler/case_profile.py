"""Typed CP1 case-profile output."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from epoch_switch.usecases.registry import OutputProfile


SignalSource = Literal[
    "registry",
    "r1",
    "da1",
    "r2",
    "rd3",
    "human_review",
    "d1",
    "d2",
]


class CaseProfile(BaseModel):
    # ── Block A: Assurance artifact (primary output) ───────────────────────────
    # The evidence artifact CP1 infers from the approved isolated memory.
    # This is CP1's central routing signal; TS1 weighs it with the other profile
    # signals when producing topology_scores.
    # Definitions (from topology philosophy):
    #   single_attestation    — one owner, one evidence chain, one signed result
    #   reconciliation_record — 2..N independent reads of the same claim +
    #                           documented resolution (Debate artifact)
    #   distributed_signoff   — multiple domain owners, each sub-result separately
    #                           attributed, assembled with multi-owner sign-off
    assurance_artifact: Literal[
        "single_attestation",
        "reconciliation_record",
        "distributed_signoff",
    ]
    assurance_level: Literal[
        "routine", "elevated", "audit_ready", "regulatory"
    ]
    dual_method_required: bool
    cluster_count: int = Field(ge=1, le=8)
    cross_functional_need: bool
    # ── Block B: Escalation (orthogonal to artifact selection) ─────────────────
    risk_level: Literal["low", "medium", "high", "critical"]
    deadline_proximity_days: int | None = Field(default=None, ge=0)
    # ── Block C: Context (non-routing — do NOT use to drive topology) ──────────
    # uncertainty: describes residual ambiguity in approved scope. Does NOT trigger
    #   Debate by itself; TS1 weighs it with Block A signals.
    # task_type: descriptive label for the kind of analytical task. Not a topology
    #   assignment by itself.
    # output_profile: shapes the form/depth of output WITHIN the chosen topology.
    #   Does NOT select the topology.
    task_type: Literal[
        "monitoring",
        "compliance_check",
        "risk_fusion",
        "verification",
        "multi_owner_consolidation",
        "escalation",
    ]
    uncertainty: Literal["low", "medium", "high"]
    # CP1's emitted output_profile decision — the 'o' in π(case, o, a).
    # Baseline is demand.output_profile; CP1 may refine form/description when
    # upstream evidence (D2 blocked_claims, R2 findings) shows the declared
    # form is not supportable. Any change MUST appear in limitations.
    output_profile: OutputProfile
    signal_sources: dict[str, list[SignalSource]] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_signal_sources(self) -> "CaseProfile":
        required = {
            # Block A — assurance artifact (primary)
            "assurance_artifact",
            "assurance_level",
            "dual_method_required",
            "cluster_count",
            "cross_functional_need",
            # Block B — escalation
            "risk_level",
            # Block C — context
            "task_type",
            "uncertainty",
            # output_profile is always required — CP1 always emits it
            "output_profile",
        }
        if self.deadline_proximity_days is not None:
            required.add("deadline_proximity_days")
        missing = sorted(
            key for key in required if not self.signal_sources.get(key)
        )
        if missing:
            raise ValueError(f"Missing signal_sources entries: {missing}")
        return self
