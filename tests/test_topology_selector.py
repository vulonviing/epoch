"""Tests for TS1 TopologySelection schema and TopologySelectorAgent.

Follows the FakeLLM pattern from test_cp1_case_profiler.py.
"""
from __future__ import annotations

import json

import pytest

from epoch_switch.agents.orchestration.topology_selector import (
    TopologySelectorAgent,
    TopologySelection,
)
from epoch_switch.core.envelope import CaseEnvelope
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.selection import topology_library
from epoch_switch.selection.stage2_binding import output_binding_for
from epoch_switch.usecases.registry import UC1


# ── FakeLLM ──────────────────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, response: dict):
        self.response = response
        self.last_user: dict | None = None

    def complete_json(self, system: str, user: str, **kwargs) -> tuple[dict, int]:
        self.last_user = json.loads(user)
        return self.response, 1


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _envelope() -> CaseEnvelope:
    return CaseEnvelope.new(
        usecase_ref=UC1.id,
        natural_request=UC1.natural_request,
        regulation_refs=UC1.regulation_refs,
        regulation_sources=UC1.regulation_sources,
        site_filter=UC1.site_filter,
        time_window=UC1.time_window,
        expected_output_family=UC1.expected_output,
    )


def _direct_payload(**overrides) -> dict:
    """Canonical Direct-topology TS1 payload (scores sum to 100)."""
    base = {
        "topology_scores": {"Direct": 80, "Debate": 15, "Coalition": 5},
        "selected_topology_id": "Direct",
        "rationale": (
            "Binary threshold check: one rule, one measure, one team.  "
            "Uncertainty is low, dual_method_required is false, "
            "cross_functional_need is false."
        ),
        "alternatives": [
            {"topology_id": "Debate", "why_not": "No interpretive ambiguity in R2 findings."},
            {"topology_id": "Coalition", "why_not": "Single domain, single method."},
        ],
        "signal_sources": {
            "selected_topology_id": ["task_type", "uncertainty", "cross_functional_need"],
        },
        "bindings": [],
        "limitations": [],
    }
    base.update(overrides)
    return base


# ── topology_library tests ────────────────────────────────────────────────────

def test_topology_library_get_direct():
    spec = topology_library.get("Direct")
    assert spec.id == "Direct"
    assert "single" in spec.interaction_pattern.lower() or "direct" in spec.display_name.lower()


def test_topology_library_get_debate():
    spec = topology_library.get("Debate")
    assert spec.id == "Debate"
    assert len(spec.slots) > 0
    assert "2..N" in spec.interaction_pattern
    assert any(slot.position_id == "additional_analyst" for slot in spec.slots)


def test_topology_library_get_coalition():
    spec = topology_library.get("Coalition")
    assert spec.id == "Coalition"
    assert len(spec.slots) > 0


def test_topology_library_get_bogus_raises():
    with pytest.raises(KeyError, match="Bogus"):
        topology_library.get("Bogus")


def test_topology_library_lambda_closed():
    """LAMBDA must have exactly three keys."""
    assert set(topology_library.LAMBDA.keys()) == {"Direct", "Debate", "Coalition"}


def test_topology_library_list_topologies_cascade_order():
    """list_topologies should return Coalition first, then Debate, then Direct."""
    ids = [tid for tid, _ in topology_library.list_topologies()]
    assert ids == ["Coalition", "Debate", "Direct"]


def test_stage2_direct_binding_excludes_upstream_d3():
    binding = output_binding_for("Direct")
    assert [rank["agents"][0]["agent_id"] for rank in binding["ranks"]] == ["P1", "F1"]
    assert binding["human_gate_after"] == "F1"


def test_stage2_debate_binding_preserves_parallel_rank():
    binding = output_binding_for("Debate")
    assert binding["ranks"][0]["mode"] == "parallel"
    assert [agent["agent_id"] for agent in binding["ranks"][0]["agents"]] == ["P2", "P3"]


def test_stage2_coalition_binding_implemented():
    """Coalition routes through C1 (divisional attestor, fans out per
    division internally) -> C2 (synthesis coordinator) -> F1, same as every
    other topology's human-gate-bearing final agent."""
    binding = output_binding_for("Coalition")
    assert binding["implemented"] is True
    assert [rank["agents"][0]["agent_id"] for rank in binding["ranks"]] == ["C1", "C2", "F1"]
    assert binding["human_gate_after"] == "F1"


# ── TopologySelection schema tests ────────────────────────────────────────────

def test_topology_selection_happy_path_direct():
    sel = TopologySelection.model_validate(_direct_payload())
    assert sel.selected_topology_id == "Direct"
    assert sum(sel.topology_scores.values()) == 100
    assert sel.bindings == []


def test_topology_selection_happy_path_debate():
    payload = _direct_payload(
        topology_scores={"Direct": 20, "Debate": 70, "Coalition": 10},
        selected_topology_id="Debate",
        rationale="Interpretive ambiguity in R2 scope.",
        alternatives=[
            {"topology_id": "Direct", "why_not": "Ambiguous threshold interpretation."},
            {"topology_id": "Coalition", "why_not": "Single domain."},
        ],
    )
    sel = TopologySelection.model_validate(payload)
    assert sel.selected_topology_id == "Debate"
    assert sum(sel.topology_scores.values()) == 100


def test_topology_selection_happy_path_coalition():
    payload = _direct_payload(
        topology_scores={"Direct": 5, "Debate": 20, "Coalition": 75},
        selected_topology_id="Coalition",
        rationale="Dual-method, multi-domain, regulatory assurance.",
        alternatives=[
            {"topology_id": "Direct", "why_not": "Cross-functional need present."},
            {"topology_id": "Debate", "why_not": "Different domains, not same claim."},
        ],
    )
    sel = TopologySelection.model_validate(payload)
    assert sel.selected_topology_id == "Coalition"
    assert sum(sel.topology_scores.values()) == 100


def test_topology_selection_scores_not_summing_to_100_raises():
    with pytest.raises(Exception, match="100"):
        TopologySelection.model_validate(
            _direct_payload(
                topology_scores={"Direct": 60, "Debate": 30, "Coalition": 5},  # sum=95
                selected_topology_id="Direct",
            )
        )


def test_topology_selection_selected_not_argmax_raises():
    """selected_topology_id must be the argmax; mismatches are rejected."""
    with pytest.raises(Exception, match="argmax"):
        TopologySelection.model_validate(
            _direct_payload(
                topology_scores={"Direct": 80, "Debate": 15, "Coalition": 5},
                selected_topology_id="Debate",  # wrong — Direct has 80
            )
        )


def test_topology_selection_bad_key_raises():
    """Keys not in Λ must be rejected."""
    with pytest.raises(Exception, match="Direct"):
        TopologySelection.model_validate(
            _direct_payload(
                topology_scores={"Direct": 80, "Debate": 15, "Bogus": 5},
                selected_topology_id="Direct",
            )
        )


def test_topology_selection_tie_break_coalition_wins():
    """When Coalition and Debate tie, Coalition wins (cascade priority)."""
    payload = _direct_payload(
        topology_scores={"Direct": 0, "Debate": 50, "Coalition": 50},
        selected_topology_id="Coalition",
        alternatives=[{"topology_id": "Direct", "why_not": "Score 0."}],
    )
    sel = TopologySelection.model_validate(payload)
    assert sel.selected_topology_id == "Coalition"


def test_topology_selection_tie_break_debate_over_direct():
    """When Debate and Direct tie, Debate wins."""
    payload = _direct_payload(
        topology_scores={"Direct": 50, "Debate": 50, "Coalition": 0},
        selected_topology_id="Debate",
        alternatives=[{"topology_id": "Coalition", "why_not": "Score 0."}],
    )
    sel = TopologySelection.model_validate(payload)
    assert sel.selected_topology_id == "Debate"


def test_topology_selection_tie_break_wrong_choice_raises():
    """Selecting Direct when Coalition has same score must be rejected."""
    with pytest.raises(Exception):
        TopologySelection.model_validate(
            _direct_payload(
                topology_scores={"Direct": 50, "Debate": 0, "Coalition": 50},
                selected_topology_id="Direct",  # should be Coalition
            )
        )


# ── TopologySelectorAgent tests ───────────────────────────────────────────────

def test_topology_selector_agent_happy_path(tmp_path):
    fake_llm = FakeLLM(_direct_payload())
    agent = TopologySelectorAgent(fake_llm)
    env = _envelope()

    message = agent.execute_selection(
        env,
        EvidenceStore(tmp_path),
        case_profile={"task_type": "threshold_monitoring", "uncertainty": "low"},
    )

    assert message.sender_agent == "TS1"
    assert message.payload["selected_topology_id"] == "Direct"
    assert sum(message.payload["topology_scores"].values()) == 100
    assert (tmp_path / "evidence_store.json").exists()


def test_topology_selector_agent_passes_case_profile_to_llm(tmp_path):
    fake_llm = FakeLLM(_direct_payload())
    agent = TopologySelectorAgent(fake_llm)
    env = _envelope()
    case_profile = {"task_type": "threshold_monitoring", "uncertainty": "low"}

    agent.execute_selection(env, EvidenceStore(tmp_path), case_profile=case_profile)

    assert fake_llm.last_user is not None
    assert fake_llm.last_user["case_profile"] == case_profile


def test_topology_selector_agent_validates_output(tmp_path):
    """An LLM response that fails schema validation raises before returning."""
    bad_payload = dict(_direct_payload())
    bad_payload["topology_scores"] = {"Direct": 60, "Debate": 30, "Coalition": 5}  # sum=95

    fake_llm = FakeLLM(bad_payload)
    agent = TopologySelectorAgent(fake_llm)

    with pytest.raises(Exception, match="100"):
        agent.execute_selection(
            _envelope(),
            EvidenceStore(tmp_path),
            case_profile={},
        )


def test_topology_selector_argmax_cli_contract():
    """Given a TS1 payload, the selected topology is the highest-scoring one."""
    payload = _direct_payload(
        topology_scores={"Direct": 5, "Debate": 25, "Coalition": 70},
        selected_topology_id="Coalition",
        alternatives=[
            {"topology_id": "Direct", "why_not": "No multi-domain ownership."},
            {"topology_id": "Debate", "why_not": "Different domains, not same claim."},
        ],
    )
    sel = TopologySelection.model_validate(payload)
    max_score = max(sel.topology_scores.values())
    # The selected topology must have the maximum score
    assert sel.topology_scores[sel.selected_topology_id] == max_score
