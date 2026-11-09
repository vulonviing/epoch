"""S1 result models — Synthesizer (Debate topology, reconciler slot).

NAMING NOTE: D3's engine already defines ``ReconciliationResult`` (in
``d3_time_series_engine.py``).  S1's LLM-produced synthesized output uses the
distinct names ``ReconciledMemoResult`` and ``ReconciledSiteQuarter`` to avoid
any import collision or naming confusion.

Design:
- ``abs_delta``, ``pct_diff``, and ``discrepancy_flag`` are carried through from D3
  (code-authoritative) unchanged.  S1 interprets them but does NOT recompute.
- ``materiality_verdict`` is a plain ``str`` — the 5% threshold is a guideline, not
  a legal definition (R2F-003 flags it as undefined in the directive).  An enum would
  reject harmless LLM phrasing variation (Validation Restraint rule).
- ``method_a_grounding`` / ``method_b_grounding`` are P2/P3's regulatory citations,
  carried through for F1 to cite in the final memo.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ReconciledSiteQuarter(BaseModel):
    """Synthesized S1 output for one site × one quarter."""

    # ── Identity ─────────────────────────────────────────────────────────────
    location_id: str
    location_name: str
    year: str                   # e.g. "2026 Q1"

    # ── Both figures (carried from D3 via P2/P3, unchanged) ──────────────────
    method_a: float
    method_b: float
    method_a_label: str
    method_b_label: str

    # ── Regulatory grounding from each analyst (carried from P2/P3) ──────────
    method_a_grounding: str     # P2's article_grounding (Annex IV citation)
    method_b_grounding: str     # P3's article_grounding (Art. 14(3) citation)

    # ── D3-authoritative comparison (carry unchanged — do NOT recompute) ──────
    abs_delta: float
    pct_diff: float
    discrepancy_flag: bool

    # ── S1's synthesis output ────────────────────────────────────────────────
    materiality_verdict: str        # plain str — the 5% threshold is a guideline
    delta_interpretation: str       # why the delta exists and what it means
    reconciled_conclusion: str      # the single agreed-upon conclusion for this site×quarter

    citations: list[str] = Field(default_factory=list)
    carried_caveats: list[str] = Field(default_factory=list)

    # ── Geo for display in F1 (carry from P2/P3 rows, default "" if absent) ──
    country_name: str = ""
    cdp_region: str = ""
    city: str = ""


class ReconciledMemoResult(BaseModel):
    """Full output produced by S1 for one registry case."""

    registry_id: str
    rows: list[ReconciledSiteQuarter]
    carried_caveats: list[str] = Field(default_factory=list)
