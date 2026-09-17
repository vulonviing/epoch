"""ExternalCommentatorAgent -- one class, two agent_id instances (EX1, EX2)."""
from __future__ import annotations

import pytest

from epoch_switch.agents.external.ex_commentator import ExternalCommentatorAgent
from epoch_switch.agents.external.ex_commentator.ex_result import ExternalCommentary

GATE_PAYLOAD = {"in_scope_findings": ["EnEfG Sec. 8"], "summary": "test scope"}


def _bullet(i: int) -> dict:
    return {
        "point": f"test point {i}",
        "stance": "adds_context",
        "relevance": "medium",
        "grounding": "web_verified",
        "sources": [
            {"institution": "BAFA", "url": "https://bafa.de/test", "title": "Test page", "published": "2026-01-01"}
        ],
    }


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete_json_websearch(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        payload = {
            "bullets": [_bullet(i) for i in range(5)],
            "institutions_consulted": ["BAFA"],
            "search_notes": [],
        }
        return payload, 1


def test_rejects_unknown_stage_id() -> None:
    with pytest.raises(ValueError):
        ExternalCommentatorAgent("EX9", llm=FakeLLM())


@pytest.mark.parametrize("stage_id", ["EX1", "EX2"])
def test_each_stage_loads_its_own_prompt_and_stamps_its_id(stage_id: str) -> None:
    agent = ExternalCommentatorAgent(stage_id, llm=FakeLLM())
    assert agent.role_prompt_path.name == f"{stage_id}.md"
    result = agent.comment(registry_id="uc1_energy_report", gate_payload=GATE_PAYLOAD)
    assert isinstance(result, ExternalCommentary)
    assert result.stage_id == stage_id
    assert result.registry_id == "uc1_energy_report"
    assert len(result.bullets) == 5


def test_allowed_domains_passed_to_websearch_call() -> None:
    fake = FakeLLM()
    agent = ExternalCommentatorAgent("EX1", llm=fake)
    agent.comment(registry_id="uc1_energy_report", gate_payload=GATE_PAYLOAD)
    assert fake.calls[0]["kwargs"]["allowed_domains"]
