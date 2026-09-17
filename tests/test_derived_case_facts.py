"""Tests for derived_case_facts_builder — especially quality_verdict integration."""
from __future__ import annotations

from epoch_switch.agents.data.d1_loader.derived_case_facts_builder import (
    build_derived_case_facts,
)


# ── fixtures ────────────────────────────────────────────────────────────────


def threshold_params() -> dict:
    return {
        "enefg_8": {"value": 7500.0, "column": "energy_mwh", "aggregation": "max"},
    }


def data_product_with_energy(energy_mwh: float) -> dict:
    return {
        "tables": {
            "energy": {
                "data": [{"site_id": "site_001", "energy_mwh": energy_mwh}]
            }
        }
    }


def pass_verdict() -> dict:
    return {
        "verdict": "pass",
        "evidence_completeness": 0.95,
        "blocked_claims": [],
        "routing_recommendation": "normal",
    }


def insufficient_verdict() -> dict:
    return {
        "verdict": "insufficient",
        "evidence_completeness": 0.05,
        "blocked_claims": [
            {
                "claim_id": "enefg_8_applicability",
                "reason": "No energy data.",
                "missing_evidence": ["annual energy coverage"],
            }
        ],
        "routing_recommendation": "stop",
    }


def partial_verdict() -> dict:
    return {
        "verdict": "partial",
        "evidence_completeness": 0.62,
        "blocked_claims": [
            {
                "claim_id": "enefg_16_applicability",
                "reason": "Incomplete historical coverage.",
                "missing_evidence": ["three-year energy history"],
            }
        ],
        "routing_recommendation": "restrict_claims",
    }


# ── backward compatibility ──────────────────────────────────────────────────


def test_no_quality_verdict_defaults_evidence_assessable_true():
    result = build_derived_case_facts(
        threshold_params(), data_product_with_energy(8000.0)
    )
    assert result is not None
    assert result["evidence_assessable"] is True
    assert result["evidence_completeness"] is None
    assert result["blocked_claims_summary"] == []


def test_no_thresholds_returns_none():
    assert build_derived_case_facts({}, data_product_with_energy(8000.0)) is None


def test_empty_energy_table_returns_none():
    result = build_derived_case_facts(
        threshold_params(), {"tables": {"energy": {"data": []}}}
    )
    assert result is None


# ── observed_value and threshold passthrough ────────────────────────────────


def test_observed_value_returned_when_above_threshold(pass_verdict=pass_verdict()):
    result = build_derived_case_facts(
        threshold_params(),
        data_product_with_energy(7300.0),
        quality_verdict=pass_verdict,
    )
    assert result["observed_value"] == 7300.0
    assert result["threshold"] == 7500.0
    assert "near_breach" not in result


def test_observed_value_returned_when_below_threshold():
    result = build_derived_case_facts(
        threshold_params(),
        data_product_with_energy(5000.0),
        quality_verdict=pass_verdict(),
    )
    assert result["observed_value"] == 5000.0
    assert "near_breach" not in result


# ── evidence_assessable from verdict ───────────────────────────────────────


def test_pass_verdict_sets_evidence_assessable_true():
    result = build_derived_case_facts(
        threshold_params(),
        data_product_with_energy(8000.0),
        quality_verdict=pass_verdict(),
    )
    assert result["evidence_assessable"] is True


def test_insufficient_verdict_sets_evidence_assessable_false():
    result = build_derived_case_facts(
        threshold_params(),
        data_product_with_energy(8000.0),
        quality_verdict=insufficient_verdict(),
    )
    assert result is not None
    assert result["evidence_assessable"] is False


def test_partial_verdict_sets_evidence_assessable_true():
    """partial is not insufficient — evidence_assessable remains True."""
    result = build_derived_case_facts(
        threshold_params(),
        data_product_with_energy(8000.0),
        quality_verdict=partial_verdict(),
    )
    assert result["evidence_assessable"] is True


# ── evidence_completeness passthrough ──────────────────────────────────────


def test_evidence_completeness_from_pass_verdict():
    result = build_derived_case_facts(
        threshold_params(),
        data_product_with_energy(8000.0),
        quality_verdict=pass_verdict(),
    )
    assert result["evidence_completeness"] == 0.95


def test_evidence_completeness_from_insufficient_verdict():
    result = build_derived_case_facts(
        threshold_params(),
        data_product_with_energy(8000.0),
        quality_verdict=insufficient_verdict(),
    )
    assert result["evidence_completeness"] == 0.05


# ── blocked_claims_summary ─────────────────────────────────────────────────


def test_blocked_claims_summary_empty_for_pass_verdict():
    result = build_derived_case_facts(
        threshold_params(),
        data_product_with_energy(8000.0),
        quality_verdict=pass_verdict(),
    )
    assert result["blocked_claims_summary"] == []


def test_blocked_claims_summary_populated_for_partial_verdict():
    result = build_derived_case_facts(
        threshold_params(),
        data_product_with_energy(8000.0),
        quality_verdict=partial_verdict(),
    )
    summary = result["blocked_claims_summary"]
    assert len(summary) == 1
    assert summary[0]["claim_id"] == "enefg_16_applicability"
    assert "Incomplete" in summary[0]["reason"]
