from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

import epoch_switch.agents.regulation.r1_active_reader.r1_profile_store as store_mod
from epoch_switch.agents.regulation.r1_active_reader.r1_active_reader import (
    ActiveRegulationReader,
    R1_MAX_REPAIR_ATTEMPTS,
)
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.regulation_cli import build_envelope
from epoch_switch.usecases.registry import UC2


class FakeLLM:
    model_name = "test-model"

    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete_json(self, system, user, **kwargs):
        self.calls += 1
        return deepcopy(self.response), 1


class SequenceFakeLLM:
    """Returns successive responses from a list; repeats the last one if exhausted."""

    model_name = "test-model"

    def __init__(self, responses: list):
        self.responses = responses
        self.calls = 0

    def complete_json(self, system, user, **kwargs):
        idx = min(self.calls, len(self.responses) - 1)
        result = deepcopy(self.responses[idx])
        self.calls += 1
        return result, 1


class FakeLoader:
    def __init__(self, page_hash="hash-abc"):
        self.page_hash = page_hash
        self.load_calls = 0

    def hashes(self, envelope):
        return {"EnEfG.pdf": self.page_hash}

    def load(self, envelope):
        self.load_calls += 1
        return (
            [
                {
                    "source_ref": "REG-2",
                    "source_file": "EnEfG.pdf",
                    "page": 8,
                    "text": "Regulation text.",
                }
            ],
            {"EnEfG.pdf": self.page_hash},
        )

    @staticmethod
    def estimate_tokens(pages):
        return 10


def grounded_response(page=8):
    return {
        "verdict": "grounded",
        "summary": "The request is grounded.",
        "selected_pages": [
            {
                "source_ref": "REG-2",
                "source_file": "EnEfG.pdf",
                "page": page,
                "reason": "Threshold rule",
            }
        ],
        "findings": [
            {
                "finding_id": "RF-001",
                "requirement_type": "threshold",
                "statement": "A consumption threshold applies.",
                "source_ref": "REG-2",
                "source_file": "EnEfG.pdf",
                "page": page,
                "evidence_excerpt": "threshold wording",
            }
        ],
        "possible_fields": [
            {
                "field_id": "PF-001",
                "name": "facility_energy_value",
                "role": "measure",
                "priority": "core",
                "description": "Operational energy value.",
                "reason": "Needed for the threshold comparison.",
                "expected_unit": "MWh",
                "expected_grain": "facility-year",
                "finding_refs": ["RF-001"],
            },
            {
                "field_id": "PF-002",
                "name": "thermal_release_temperature",
                "role": "measure",
                "priority": "related",
                "description": "Related thermal context.",
                "reason": "Relevant to associated waste-heat provisions.",
                "expected_unit": "C",
                "expected_grain": "facility-year",
                "finding_refs": ["RF-001"],
            },
        ],
        "missing_regulatory_context": [],
        "stop_reason": None,
    }


def test_r1_accepts_fields_not_predeclared_in_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROFILE_DIR", tmp_path / "profiles")
    response = grounded_response()
    agent = ActiveRegulationReader(FakeLLM(response), FakeLoader())
    message = agent.execute_preselection(
        build_envelope(UC2), EvidenceStore(tmp_path), cache_mode="off"
    )
    names = [field["name"] for field in message.payload["possible_fields"]]
    assert names == ["facility_energy_value", "thermal_release_temperature"]


def test_r1_cache_is_keyed_by_registry_and_source_signature(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROFILE_DIR", tmp_path / "profiles")
    llm = FakeLLM(grounded_response())
    loader = FakeLoader()
    agent = ActiveRegulationReader(llm, loader)
    run1 = tmp_path / "run1"
    run2 = tmp_path / "run2"
    run1.mkdir()
    run2.mkdir()
    first = agent.execute_preselection(build_envelope(UC2), EvidenceStore(run1))
    store_mod.promote_active(
        UC2.id,
        first.payload,
        run_id="test",
        review_round=1,
        input_refs={},
    )
    agent.execute_preselection(build_envelope(UC2), EvidenceStore(run2))
    assert llm.calls == 1
    assert loader.load_calls == 2


def test_r1_rejects_unknown_finding_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROFILE_DIR", tmp_path / "profiles")
    response = grounded_response()
    response["possible_fields"][0]["finding_refs"] = ["RF-999"]
    with pytest.raises(ValueError, match="invalid output after"):
        ActiveRegulationReader(
            FakeLLM(response), FakeLoader()
        ).execute_preselection(
            build_envelope(UC2), EvidenceStore(tmp_path), cache_mode="off"
        )


def test_r1_rejects_unsupplied_citation(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROFILE_DIR", tmp_path / "profiles")
    with pytest.raises(ValueError, match="invalid output after"):
        ActiveRegulationReader(
            FakeLLM(grounded_response(page=99)), FakeLoader()
        ).execute_preselection(
            build_envelope(UC2), EvidenceStore(tmp_path), cache_mode="off"
        )


def test_r1_prompt_has_no_predeclared_uc2_field_names():
    prompt = (
        Path(__file__).parents[1]
        / "src"
        / "epoch_switch"
        / "agents"
        / "regulation"
        / "r1_active_reader"
        / "R1.md"
    ).read_text(encoding="utf-8")
    assert "The registry does not provide a field answer key" in prompt
    assert "annual_total_final_energy_consumption" not in prompt
    assert "waste_heat_quantity" not in prompt


def invalid_enum_response():
    """A response with an illegal requirement_type that triggers repair."""
    response = grounded_response()
    response["findings"][0]["requirement_type"] = "penalty"
    return response


def test_r1_repair_succeeds_on_second_attempt(tmp_path, monkeypatch):
    """First call returns illegal enum; second (repair) call returns valid output."""
    monkeypatch.setattr(store_mod, "PROFILE_DIR", tmp_path / "profiles")
    llm = SequenceFakeLLM([invalid_enum_response(), grounded_response()])
    agent = ActiveRegulationReader(llm, FakeLoader())

    message = agent.execute_preselection(
        build_envelope(UC2), EvidenceStore(tmp_path), cache_mode="off"
    )

    assert message.payload["findings"][0]["requirement_type"] == "threshold"
    assert llm.calls == 2  # initial + 1 repair

    # A validation_repaired audit record should have been written to the archive.
    archive = store_mod.list_archive(UC2.id)
    assert any(r.get("status") == "validation_repaired" for r in archive)


def test_r1_repair_exhausted_raises_and_saves_audit(tmp_path, monkeypatch):
    """All R1_MAX_REPAIR_ATTEMPTS attempts return illegal enum → ValueError + audit."""
    monkeypatch.setattr(store_mod, "PROFILE_DIR", tmp_path / "profiles")
    llm = SequenceFakeLLM([invalid_enum_response()] * R1_MAX_REPAIR_ATTEMPTS)
    agent = ActiveRegulationReader(llm, FakeLoader())

    with pytest.raises(ValueError, match="invalid output after"):
        agent.execute_preselection(
            build_envelope(UC2), EvidenceStore(tmp_path), cache_mode="off"
        )

    assert llm.calls == R1_MAX_REPAIR_ATTEMPTS

    archive = store_mod.list_archive(UC2.id)
    assert any(r.get("status") == "validation_failed" for r in archive)


def test_r1_prompt_has_enum_hard_rule():
    """R1.md must declare the allowed requirement_type values as a hard rule."""
    prompt = (
        Path(__file__).parents[1]
        / "src"
        / "epoch_switch"
        / "agents"
        / "regulation"
        / "r1_active_reader"
        / "R1.md"
    ).read_text(encoding="utf-8")
    assert "HARD RULE" in prompt
    assert "obligation" in prompt
    assert "responsible_party" in prompt
    # The prompt must forbid inventing new types
    assert "Never invent a new type" in prompt
    # The word "penalty" may appear in the mapping guide as an example of what NOT to
    # use, but it must not appear as an allowed enum value (i.e. not in the table rows
    # as a `value` cell).
    assert "| `penalty`" not in prompt
