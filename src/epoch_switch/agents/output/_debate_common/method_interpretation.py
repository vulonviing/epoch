"""Shared Pydantic models for the Debate topology method-interpreter agents.

``MethodInterpretationRow`` is the per-site-per-quarter output row produced by
both P2 (Energy-Method Interpreter) and P3 (Reported-Method Interpreter).
The schema is identical for both agents; method identity is expressed through
the ``method_column``, ``method_label``, and ``article_grounding`` fields that
each agent fills from its own regulatory perspective.

Design note:
- ``method_a``, ``method_b``, ``abs_delta``, ``pct_diff``, and
  ``discrepancy_flag`` are carried through unchanged from the D3 dual-method
  computation.  Neither P2 nor P3 derives or validates them — D3 owns those
  numbers.  Pydantic validates type only (Validation Restraint rule).
- Verdict fields (``defensibility``) are plain ``str`` — an enum would reject
  harmless LLM phrasing variation.
- Geo fields default to ``""``; they are populated from D3 rows when the D1
  location join is current (UC2 geo re-run required for stale 2026-07-12 artifact).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class MethodInterpretationRow(BaseModel):
    """Per-site-per-quarter interpretation produced by one Debate method agent."""

    # ── Site identity (carry unchanged from D3 row) ─────────────────────────
    location_id: str
    location_name: str
    year: str                   # holds the time-period grain, e.g. "2026 Q1"

    # ── Method identity (agent fills from its own lens) ──────────────────────
    method_figure: float        # the figure this agent is responsible for
                                # P2 → method_a (energy-derived); P3 → method_b (reported)
    method_column: str          # "scope2_market_proxy_t" | "scope2_location_t"
    method_label: str           # human-readable label from D3 row

    # ── Both figures + D3-authoritative comparison (carry unchanged from D3) ─
    method_a: float
    method_b: float
    abs_delta: float
    pct_diff: float
    discrepancy_flag: bool

    # ── Agent's regulatory interpretation ───────────────────────────────────
    article_grounding: str      # e.g. "Annex IV" (P2) | "Art. 14(3)" (P3)
    interpretation: str         # 2-4 sentences: what the figure means under the
                                # approved R2 findings and the agent's regulatory lens
    defensibility: str          # agent's plain-language assessment of the figure's
                                # regulatory defensibility (not an enum)
    carried_caveats: list[str] = Field(default_factory=list)

    # ── Geographic attributes (carry unchanged from D3; default "" when absent) ─
    country: str = ""
    country_code: str = ""
    country_name: str = ""
    cdp_region: str = ""
    city: str = ""
    address: str = ""
    zip_code: str = ""
    latitude: str = ""
    longitude: str = ""


class MethodInterpretationResult(BaseModel):
    """Full result produced by one Debate method agent (P2 or P3)."""

    registry_id: str
    rows: list[MethodInterpretationRow]
