"""Deterministic derived_case_facts builder for CP1 enrichment.

Takes registry threshold_parameters and D1's observed data product,
compares observed values against regulation thresholds, and produces
a derived_case_facts dict suitable for CP1's execute_preselection().

Pure Python — no LLM, no I/O. Fully testable.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def build_derived_case_facts(
    threshold_parameters: dict[str, Any],
    data_product: dict[str, Any],
    quality_verdict: dict[str, Any] | None = None,
    window_divergence: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build derived_case_facts from registry thresholds and D1 observed data.

    Args:
        threshold_parameters: Registry threshold specs keyed by threshold_id.
            Each value must be a dict or ThresholdSpec-compatible object with:
            value (float), column (str), aggregation (str), near_breach_ratio (float).
        data_product: D1 output dict with shape:
            {"tables": {"energy": {"data": [list of row dicts]}, ...}}
        quality_verdict: Optional D2 QualityVerdict dict.  When provided,
            ``evidence_assessable`` is derived from the verdict (``False`` when
            verdict is "insufficient") instead of the hardcoded ``True``.
            ``evidence_completeness`` and a ``blocked_claims_summary`` are also
            included in the returned dict for CP1 context.
        window_divergence: Optional dict describing a D1 time-window clamp
            divergence from the DA1-approved handoff.  When present and
            non-empty, it is forwarded verbatim so CP1 can surface it as a
            limitation.  Expected keys: ``requested`` ([start, end]),
            ``effective`` ([start, end]), ``note`` (str).

    Returns:
        derived_case_facts dict or None if no thresholds or no data.
        Dict shape:
            observed_value: float — the observed value closest to threshold
            threshold: float — the reference threshold value
            calculation_method: str — "deterministic_threshold_comparison"
            evidence_assessable: bool — from D2 verdict (or True if not integrated)
            evidence_completeness: float | None — from D2 (or None if not integrated)
            blocked_claims_summary: list[dict] — from D2 (or [] if not integrated)
            window_divergence: dict | None — D1 clamp info for CP1 limitation
    """
    if not threshold_parameters:
        return None

    energy_table = (
        data_product.get("tables", {})
        .get("energy", {})
        .get("data", [])
    )
    if not energy_table:
        return None

    df = pd.DataFrame(energy_table)

    details: list[dict[str, Any]] = []

    for name, spec in threshold_parameters.items():
        raw_spec = _to_spec_dict(spec)

        column = raw_spec.get("column", "")
        if not column or column not in df.columns:
            continue

        agg = raw_spec.get("aggregation", "max")
        if agg == "sum":
            observed = float(df[column].sum())
        elif agg == "mean":
            observed = float(df[column].mean())
        else:
            observed = float(df[column].max())

        threshold_value = float(raw_spec.get("value", 0))
        ratio = observed / threshold_value if threshold_value > 0 else 0.0

        details.append({
            "threshold_id": name,
            "observed_value": observed,
            "threshold": threshold_value,
            "ratio": round(ratio, 6),
        })

    if not details:
        return None

    closest = max(details, key=lambda d: d["ratio"])

    # Derive evidence_assessable from D2 verdict when available.
    if quality_verdict is not None:
        evidence_assessable = quality_verdict.get("verdict") != "insufficient"
        evidence_completeness: float | None = quality_verdict.get("evidence_completeness")
        blocked_claims_summary = [
            {"claim_id": bc.get("claim_id"), "reason": bc.get("reason")}
            for bc in quality_verdict.get("blocked_claims", [])
            if isinstance(bc, dict)
        ]
    else:
        evidence_assessable = True  # D2 not yet integrated for this call
        evidence_completeness = None
        blocked_claims_summary = []

    return {
        "observed_value": closest["observed_value"],
        "threshold": closest["threshold"],
        "calculation_method": "deterministic_threshold_comparison",
        "evidence_assessable": evidence_assessable,
        "evidence_completeness": evidence_completeness,
        "blocked_claims_summary": blocked_claims_summary,
        "window_divergence": window_divergence or None,
    }


def _to_spec_dict(spec: Any) -> dict[str, Any]:
    if hasattr(spec, "model_dump"):
        return spec.model_dump()
    if isinstance(spec, dict):
        return spec
    return {}
