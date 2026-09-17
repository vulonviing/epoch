"""P1 output schema — per-site regulatory interpretation.

P1 is a pure LLM interpretation agent (lead_interpreter slot in the Direct chain).
It receives R2 in-scope findings and D3 computation results, then produces a
per-site interpretation grounded in the approved regulation boundary.

Pydantic validates structure and type only.  Content of LLM-generated text fields
(interpretation, carried_caveats) is never checked against expected values — that
is the LLM's responsibility (AGENTS.md: Agent Type Classification, Validation Restraint).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SiteInterpretation(BaseModel):
    """Regulatory interpretation for a single site row from D3."""

    location_id: str
    location_name: str
    rule_id: str          # e.g. "enefg_8" or "enefg_16" — passed through from D3
    rule_label: str
    status: str           # "obligated" | "near_breach" | "compliant" — passed through from D3
    near_breach: bool = False  # True when avg_3yr is within near_breach_ratio band — from D3
    gap: float            # distance to threshold in MWh, passed through from D3
    interpretation: str   # P1's plain-language regulatory reading of this site
    carried_caveats: list[str] = Field(default_factory=list)
    # Geo passthrough from D3 (sourced from D1 dimension joins).
    # Empty string when absent in the underlying data (e.g. division-grain UC3).
    country: str = ""
    country_code: str = ""
    country_name: str = ""
    cdp_region: str = ""
    # Full address from D1 locations join (dist_ie_locations.csv).
    city: str = ""
    address: str = ""
    zip_code: str = ""
    latitude: str = ""
    longitude: str = ""


class P1Result(BaseModel):
    """Top-level P1 output: all site interpretations + chain-level caveats."""

    registry_id: str
    rows: list[SiteInterpretation]
    carried_caveats: list[str] = Field(default_factory=list)
