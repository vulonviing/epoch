"""Phase 5+: RC1.2 (LLM) -- deterministic candidate prep + FakeLLM classification."""
from __future__ import annotations

import json
from pathlib import Path

from epoch_switch.agents.regulation.rc1_change_classifier import ChangeClassifierAgent
from epoch_switch.agents.regulation.rc1_change_classifier.rc1_result import RC1Result

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeLLM:
    """Synthesizes a schema-valid response from the deterministic candidates it is handed.

    Not testing RC1.2's reasoning quality here (that needs a real model) --
    testing that the deterministic prep -> LLM call -> validation wiring
    round-trips correctly and every candidate gets at least one row.
    """

    def __init__(self):
        self.calls = []

    def complete_json(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        payload = json.loads(user)
        rows = []
        for c in payload["deterministic_candidates"]:
            old_ids = c["old_dr_ids"]
            new_ids = c["new_dr_ids"]
            if not new_ids:
                status = "Removed"
            elif not old_ids:
                status = "New"
            else:
                status = "Retained"
            rows.append(
                {
                    "standard": payload["standard"],
                    "old_dr_ids": old_ids,
                    "old_dr_titles": [],
                    "new_dr_ids": new_ids,
                    "new_dr_titles": [],
                    "change_status": status,
                    "change_description": "test",
                    "old_paragraph_refs": [],
                    "new_paragraph_refs": [],
                    "mapping_uncertain": len(new_ids) > 1,
                    "citations": [],
                    "confidence": 0.7,
                    "blind_agreement": "agree",
                    "deterministic_agreement": "agree",
                    "divergence_note": "",
                }
            )
        return {"rows": rows}, 1


def _document_sources(standards: list[str]) -> dict:
    return {
        "baseline_dir": "regulations/esrs/baseline_2025_amended",
        "target_dir": "regulations/esrs/2026",
        "helper_dir": "regulations/comparison_helpers",
        "company_report_dir": "data/siemens_sustainability_2025",
        "standards": standards,
    }


def test_rc1_produces_at_least_one_row_per_candidate() -> None:
    from epoch_switch.corpus.corpus_bundle import extract_corpus, map_candidates

    bundle = map_candidates(extract_corpus(_document_sources(["E1"]), REPO_ROOT))
    expected_n_candidates = len(bundle.standards["E1"].candidates)

    agent = ChangeClassifierAgent(llm=FakeLLM())
    result = agent.classify(bundle=bundle, blind_rows=[])
    assert isinstance(result, RC1Result)
    assert len(result.rows) >= expected_n_candidates
    assert any(r.change_status == "New" for r in result.rows)


def test_rc1_batches_are_scoped_to_their_own_standard() -> None:
    from epoch_switch.corpus.corpus_bundle import extract_corpus, map_candidates

    bundle = map_candidates(extract_corpus(_document_sources(["E2", "E5"]), REPO_ROOT))
    agent = ChangeClassifierAgent(llm=FakeLLM())
    result = agent.classify(bundle=bundle, blind_rows=[])
    standards_seen = {row.standard for row in result.rows}
    assert standards_seen == {"E2", "E5"}


def test_rc1_receives_blind_matches_scoped_to_standard() -> None:
    from epoch_switch.corpus.corpus_bundle import extract_corpus, map_candidates

    bundle = map_candidates(extract_corpus(_document_sources(["E2", "E5"]), REPO_ROOT))
    fake = FakeLLM()
    agent = ChangeClassifierAgent(llm=fake)
    blind_rows = [
        {"standard": "E2", "old_dr_id": "X", "new_dr_ids": ["Y"], "basis": "b", "match_uncertain": False, "confidence": 0.5},
        {"standard": "E5", "old_dr_id": "A", "new_dr_ids": ["B"], "basis": "b", "match_uncertain": False, "confidence": 0.5},
    ]
    agent.classify(bundle=bundle, blind_rows=blind_rows)
    for call in fake.calls:
        payload = json.loads(call["user"])
        standard = payload["standard"]
        assert all(row["standard"] == standard for row in payload["blind_matches"])
