"""UC3 Coalition chain -- structural wiring tests (D3 -> C1 -> C2 -> F1).

These are wiring/isolation tests only -- no assertions on LLM-generated
content (AGENTS.md: Validation Restraint). They verify:

  - C1 opens one isolated call per division, each payload containing only
    that division's rows, with no ``n_sites`` key present anywhere in it
    (Coalition independence rule, enforced in code not prompt).
  - C2 receives all C1 rows together in a single call.
  - F1 dispatches to the ``F1Numeric`` schema and ``F1_numeric_measurement.md``
    prompt when ``demand.output_profile.form == "numeric_measurement"``.
  - D3's ``deterministic_summary`` carries ``portfolio_total_t`` for the
    consolidation kind.
"""
from __future__ import annotations

import json

from epoch_switch.agents.data.d3_time_series.d3_time_series import _deterministic_summary
from epoch_switch.agents.output.c1_divisional_attestor.c1_divisional_attestor import (
    DivisionalAttestorAgent,
)
from epoch_switch.agents.output.c1_divisional_attestor.c1_result import C1Result
from epoch_switch.agents.output.c2_coalition_synthesizer.c2_coalition_synthesizer import (
    CoalitionSynthesizerAgent,
)
from epoch_switch.agents.output.c2_coalition_synthesizer.c2_result import C2Result
from epoch_switch.agents.output.f1_finalizer.f1_finalizer import FinalizerAgent
from epoch_switch.agents.output.f1_finalizer.f1_result import F1Numeric

D3_PAYLOAD = {
    "registry_id": "uc3_csrd_scope2_measure",
    "kind": "consolidation",
    "portfolio_total_t": 46390.4,
    "division_count": 5,
    "rows": [
        {"division": "DI", "year": "2024", "subtotal_t": 11777.0, "n_sites": 9, "n_rows": 40},
        {"division": "DI", "year": "2023", "subtotal_t": 10000.0, "n_sites": 9, "n_rows": 38},
        {"division": "SMO", "year": "2024", "subtotal_t": 14613.4, "n_sites": 6, "n_rows": 22},
        {"division": "SI", "year": "2024", "subtotal_t": 3109.2, "n_sites": 4, "n_rows": 12},
        {"division": "SRE", "year": "2024", "subtotal_t": 14774.3, "n_sites": 5, "n_rows": 18},
        {"division": "Advanta", "year": "2024", "subtotal_t": 2116.5, "n_sites": 2, "n_rows": 5},
    ],
}

R2_FINDINGS = [
    {"finding_id": "F-1", "article": "CSRD Art. 8", "statement": "test", "obligation_type": "disclosure"}
]


class FakeC1LLM:
    """Records every call's payload and returns a schema-conforming report."""

    def __init__(self):
        self.calls: list[dict] = []

    def complete_json(self, system, user, **kwargs):
        payload = json.loads(user)
        self.calls.append(payload)
        return {
            "division": payload["division"],
            "subtotal_t": sum(r["subtotal_t"] for r in payload["division_rows"]),
            "years": [r["year"] for r in payload["division_rows"]],
            "n_rows": sum(r["n_rows"] for r in payload["division_rows"]),
            "data_volume_status": "adequate",
            "narrative": "test narrative",
            "provenance_note": "test provenance",
            "carried_caveats": [],
        }, 1


class FakeC2LLM:
    def __init__(self):
        self.calls: list[dict] = []

    def complete_json(self, system, user, **kwargs):
        payload = json.loads(user)
        self.calls.append(payload)
        return {
            "registry_id": "uc3_csrd_scope2_measure",
            "portfolio_total_t": sum(r["subtotal_t"] for r in payload["c1_rows"]),
            "deterministic_portfolio_total_t": payload["deterministic_portfolio_total_t"],
            "thin_data_divisions": [],
            "data_volume_coverage_note": "all divisions covered",
            "readiness_assessment": "on track",
            "carried_caveats": [],
        }, 1


class FakeF1LLM:
    def complete_json(self, system, user, **kwargs):
        payload = json.loads(user)
        c1_rows = payload["upstream_outputs"]["C1"]["rows"]
        return {
            "registry_id": "uc3_csrd_scope2_measure",
            "output_form": "numeric_measurement",
            "headline": "test headline",
            "portfolio_summary": {
                "portfolio_total_t": payload["deterministic_summary"]["portfolio_total_t"],
                "division_count": payload["deterministic_summary"]["division_count"],
                "n_adequate": sum(1 for r in c1_rows if r["data_volume_status"] == "adequate"),
                "n_thin": sum(1 for r in c1_rows if r["data_volume_status"] != "adequate"),
                "narrative": "test",
            },
            "division_results": [
                {
                    "division": r["division"],
                    "subtotal_t": r["subtotal_t"],
                    "years": r["years"],
                    "n_rows": r["n_rows"],
                    "data_volume_status": r["data_volume_status"],
                    "provenance_note": r["provenance_note"],
                    "entry_text": "test",
                    "citations": [],
                }
                for r in c1_rows
            ],
            "readiness_assessment": "test readiness",
            "limitations": [],
            "citations": [],
        }, 1


def test_d3_deterministic_summary_carries_portfolio_total() -> None:
    summary = _deterministic_summary(D3_PAYLOAD)
    assert summary["division_count"] == 5
    assert summary["portfolio_total_t"] == 46390.4


def test_c1_opens_one_isolated_call_per_division_with_no_n_sites() -> None:
    fake = FakeC1LLM()
    agent = DivisionalAttestorAgent(llm=fake)
    result = agent.attest(r2_in_scope_findings=R2_FINDINGS, d3_payload=D3_PAYLOAD)

    divisions_in_data = {row["division"] for row in D3_PAYLOAD["rows"]}
    assert len(fake.calls) == len(divisions_in_data)

    for call in fake.calls:
        # Each call sees exactly one division's rows -- never another's.
        for row in call["division_rows"]:
            assert "n_sites" not in row
            assert set(row.keys()) == {"year", "subtotal_t", "n_rows"}
        # No cross-division leakage: every division_rows entry belongs to
        # the same division that D3 originally tagged (checked via count).
        expected_n_rows = sum(
            r["n_rows"] for r in D3_PAYLOAD["rows"] if r["division"] == call["division"]
        )
        assert sum(r["n_rows"] for r in call["division_rows"]) == expected_n_rows

    assert isinstance(result, C1Result)
    assert len(result.rows) == len(divisions_in_data)


def test_c2_receives_all_c1_rows_in_one_call() -> None:
    c1_fake = FakeC1LLM()
    c1_agent = DivisionalAttestorAgent(llm=c1_fake)
    c1_result = c1_agent.attest(r2_in_scope_findings=R2_FINDINGS, d3_payload=D3_PAYLOAD)

    c2_fake = FakeC2LLM()
    c2_agent = CoalitionSynthesizerAgent(llm=c2_fake)
    c2_result = c2_agent.consolidate(
        c1_result=c1_result.model_dump(),
        r2_in_scope_findings=R2_FINDINGS,
        d3_payload=D3_PAYLOAD,
    )

    assert len(c2_fake.calls) == 1
    assert len(c2_fake.calls[0]["c1_rows"]) == len(c1_result.rows)
    assert isinstance(c2_result, C2Result)


def test_f1_numeric_measurement_form_selects_f1numeric_schema() -> None:
    c1_fake = FakeC1LLM()
    c1_agent = DivisionalAttestorAgent(llm=c1_fake)
    c1_result = c1_agent.attest(r2_in_scope_findings=R2_FINDINGS, d3_payload=D3_PAYLOAD)

    c2_fake = FakeC2LLM()
    c2_agent = CoalitionSynthesizerAgent(llm=c2_fake)
    c2_result = c2_agent.consolidate(
        c1_result=c1_result.model_dump(),
        r2_in_scope_findings=R2_FINDINGS,
        d3_payload=D3_PAYLOAD,
    )

    f1_agent = FinalizerAgent(llm=FakeF1LLM())
    demand = {
        "registry_id": "uc3_csrd_scope2_measure",
        "output_profile": {"form": "numeric_measurement"},
    }
    result = f1_agent.finalize(
        upstream_outputs={"C1": c1_result.model_dump(), "C2": c2_result.model_dump()},
        r2_in_scope_findings=R2_FINDINGS,
        demand=demand,
        d3_summary=_deterministic_summary(D3_PAYLOAD),
    )

    assert isinstance(result, F1Numeric)
    assert result.output_form == "numeric_measurement"
    assert result.portfolio_summary.portfolio_total_t == 46390.4
    assert result.portfolio_summary.division_count == 5
