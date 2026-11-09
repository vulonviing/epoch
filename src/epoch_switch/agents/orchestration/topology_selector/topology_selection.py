"""Pydantic output schema for TS1 — the topology selector."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from epoch_switch.selection.topology_library import LAMBDA

# Cascade priority order for deterministic tie-breaking: Coalition > Debate > Direct.
# Only used when two topologies share the highest score.
_CASCADE_PRIORITY = ["Coalition", "Debate", "Direct"]


class TopologySelection(BaseModel):
    """TS1 output payload — a confidence distribution over Λ plus the selected topology.

    Invariants enforced by the validator:
    - topology_scores keys are exactly Λ = {Direct, Debate, Coalition}.
    - All three scores are non-negative integers summing to 100.
    - selected_topology_id equals the argmax of topology_scores, with ties
      broken by cascade priority (Coalition > Debate > Direct).
    """

    topology_scores: dict[str, int]
    """Confidence distribution over Λ.  Each value is an integer in [0, 100].
    The three values sum to exactly 100.
    Example: {"Direct": 70, "Debate": 20, "Coalition": 10}
    """

    selected_topology_id: Literal["Direct", "Debate", "Coalition"]
    """Argmax of topology_scores.  Must exactly match the argmax (tie-break:
    Coalition > Debate > Direct).  The CLI reads this field as the operative
    selection.
    """

    rationale: str
    """Plain-language explanation of why the selected topology was chosen,
    referencing the relevant CP1 signals.
    """

    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    """Why each non-selected topology was not chosen.
    Each entry: {"topology_id": str, "why_not": str}.
    """

    signal_sources: dict[str, list[str]] = Field(default_factory=dict)
    """Which CP1 signals were most decisive, keyed by the payload field they
    drove.  Example: {"selected_topology_id": ["uncertainty", "output_profile"]}.
    """

    bindings: list[Any] = Field(default_factory=list)
    """Stage-1 placeholder. The CLI resolves the selected topology through the
    static stage-2 binding table; bindings must be empty in TS1 output.
    """

    limitations: list[str] = Field(default_factory=list)
    """Any caveats about the selection — e.g. because deadline_proximity_days
    is null and the escalation check could not be performed.
    """

    @model_validator(mode="after")
    def _validate_selection(self) -> "TopologySelection":
        # 1. Keys must be exactly Λ
        scores = self.topology_scores
        expected_keys = set(LAMBDA.keys())
        actual_keys = set(scores.keys())
        if actual_keys != expected_keys:
            extra = actual_keys - expected_keys
            missing = expected_keys - actual_keys
            parts = []
            if extra:
                parts.append(f"unexpected keys: {sorted(extra)}")
            if missing:
                parts.append(f"missing keys: {sorted(missing)}")
            raise ValueError(
                f"topology_scores must have exactly the keys {sorted(expected_keys)}.  "
                + "; ".join(parts)
            )

        # 2. All values must be non-negative integers summing to 100
        total = sum(scores.values())
        if total != 100:
            raise ValueError(
                f"topology_scores values must sum to 100, got {total}: {scores}"
            )
        for key, value in scores.items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"topology_scores['{key}'] must be a non-negative integer, got {value!r}"
                )

        # 3. selected_topology_id must equal the argmax (deterministic tie-break)
        max_score = max(scores.values())
        candidates = [tid for tid in _CASCADE_PRIORITY if scores.get(tid) == max_score]
        expected_selected = candidates[0]  # highest cascade priority wins tie
        if self.selected_topology_id != expected_selected:
            raise ValueError(
                f"selected_topology_id '{self.selected_topology_id}' does not match "
                f"the argmax of topology_scores.  Given scores {scores}, the "
                f"correct selection (with cascade tie-break Coalition > Debate > Direct) "
                f"is '{expected_selected}'."
            )

        return self
