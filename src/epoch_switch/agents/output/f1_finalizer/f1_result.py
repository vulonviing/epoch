"""F1 output schemas — topology-agnostic finalizer.

F1 dispatches on ``demand.output_profile.form`` and produces one of three shapes:

  yes_no_alert       → ``F1Deliverable``  (Direct chain / threshold check use cases)
  structured_memo    → ``F1Memo``         (Debate chain / dual-method memo use cases)
  numeric_measurement → ``F1Numeric``     (Coalition chain / divisional attestation use cases)

Schemas are adapted from the former O2 (o2_result.py) and O6 (o6_result.py) models.
Pydantic validates structure and type only — no content checks on LLM-generated text
fields (AGENTS.md: Validation Restraint).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# yes_no_alert shape (mirrors former O2Deliverable)
# ---------------------------------------------------------------------------

class SiteAlert(BaseModel):
    location_id: str
    location_name: str
    location_display: str = ""
    rule_id: str
    rule_label: str
    status: str               # "obligated" | "near_breach" | "compliant"
    near_breach: bool = False
    alert: bool = False
    gap: float = 0.0
    headline: str = ""


class PortfolioSummary(BaseModel):
    n_sites: int = 0
    n_obligated: int = 0
    n_near_breach: int = 0
    n_compliant: int = 0
    narrative: str = ""


class F1Deliverable(BaseModel):
    registry_id: str
    output_form: str = ""
    headline: str = ""
    portfolio_summary: PortfolioSummary = Field(default_factory=PortfolioSummary)
    site_alerts: list[SiteAlert] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# structured_memo shape (mirrors former O6 StructuredMemo)
# ---------------------------------------------------------------------------

class MemoPortfolioSummary(BaseModel):
    n_entries: int = 0      # total site×quarter entries
    n_sites: int = 0        # distinct location_id values
    n_flagged: int = 0      # entries with discrepancy_flag=True
    n_material: int = 0     # entries with material=True
    narrative: str = ""


class MemoSiteQuarterEntry(BaseModel):
    location_id: str
    location_name: str
    location_display: str = ""
    year: str

    method_a: float
    method_b: float
    abs_delta: float
    pct_diff: float
    discrepancy_flag: bool
    material: bool = False

    scope_determination: str
    entry_text: str
    citations: list[str] = Field(default_factory=list)


class MemoSection(BaseModel):
    heading: str
    body: str


class F1Memo(BaseModel):
    registry_id: str
    output_form: str = "structured_memo"
    headline: str = ""
    portfolio_summary: MemoPortfolioSummary = Field(default_factory=MemoPortfolioSummary)
    sections: list[MemoSection] = Field(default_factory=list)
    entries: list[MemoSiteQuarterEntry] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# numeric_measurement shape (Coalition chain — divisional attestation)
# ---------------------------------------------------------------------------

class NumericDivisionResult(BaseModel):
    division: str
    subtotal_t: float
    years: list[str] = Field(default_factory=list)
    n_rows: int = 0
    data_volume_status: str      # carried from C1, unchanged
    provenance_note: str = ""    # carried from C1, unchanged
    entry_text: str = ""         # F1's own reading of this division's row
    citations: list[str] = Field(default_factory=list)


class NumericPortfolioSummary(BaseModel):
    portfolio_total_t: float = 0.0   # copied verbatim from D3's deterministic_summary
    division_count: int = 0
    n_adequate: int = 0
    n_thin: int = 0
    narrative: str = ""


class F1Numeric(BaseModel):
    registry_id: str
    output_form: str = "numeric_measurement"
    headline: str = ""
    portfolio_summary: NumericPortfolioSummary = Field(default_factory=NumericPortfolioSummary)
    division_results: list[NumericDivisionResult] = Field(default_factory=list)
    readiness_assessment: str = ""
    limitations: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
