"""Registry of user requests and their regulation sources.

The registry describes what the user wants. It deliberately does not prescribe
which operational data fields R1 must produce.

Design (2026-06-30): the active use-case set is three entries, one per
Measurement Taxonomy output type (Binary / Qualitative / Quantitative).
Each entry declares an OutputProfile ('o') and an AssuranceProfile ('a') so
the topology selector pi(case, o, a) -> topology can operate without
hard-coding the topology into the registry. expected_topology is an eval
gold anchor only — not an operative constraint on the selector.
The NLG output type is intentionally excluded (conceptual idea only).
KSG (REG-5) has no 1990 baseline in the FY2022-2025 data; its
ReductionTargetSpec was a placeholder and the seed has been removed.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, Field


class ComputationSpec(BaseModel):
    """Registry-level recipe for D3's deterministic computation motor.

    ``kind`` selects the engine:
    - ``"threshold"`` → SiteComplianceEngine (UC1 §8/§16 binary obligation check).
    - ``"dual_method"`` → DualMethodReconciliationEngine (UC2 ETS1 CO₂e A vs B).
    - ``"consolidation"`` → DivisionConsolidationEngine (UC3 CSRD ESRS E1 roll-up).

    ``source_domain`` names the D1 table key the engine reads (e.g. ``"energy"``,
    ``"emissions"``).  The actual measures inside are decided at runtime by the
    human-approved handoff — this spec only names the motor and the specific
    columns used for reconciliation.

    For ``"threshold"`` kind the threshold rules come from
    ``UsecaseSeed.threshold_parameters`` (existing UC1 path); this spec provides
    the source domain override only.  For ``"dual_method"`` the two method columns
    and their human-readable labels are declared here.  For ``"consolidation"``
    the grouping column (``group_by_column``) and the measure to sum
    (``measure_column``) are declared here; the approved handoff determines which
    rows and which entity grain D1 delivers.
    """

    kind: Literal["threshold", "dual_method", "consolidation"]
    source_domain: str = "energy"
    # dual_method fields (ignored for threshold / consolidation)
    method_a_column: str = ""
    method_b_column: str = ""
    method_a_label: str = ""
    method_b_label: str = ""
    tolerance: float = 0.05
    # consolidation fields (ignored for threshold / dual_method)
    group_by_column: str = "bu_rc_group"   # division key in the D1 table
    measure_column: str = ""               # measure to sum per division


class ThresholdSpec(BaseModel):
    """Deterministic regulation threshold for near_breach computation.

    Thresholds are regulation constants that belong in the registry
    (the "what the user wants" layer). They do not require R1 to
    re-extract them from PDFs.
    """

    value: float
    column: str
    aggregation: Literal["max", "sum", "mean"]
    near_breach_ratio: float = 0.95


class ReductionTargetSpec(BaseModel):
    """Structured percentage-reduction target relative to a reference year.

    Used for trajectory-based use cases (e.g. KSG) where the goal is a
    percentage reduction vs a historical baseline year, NOT an absolute
    threshold on a data column.  Kept separate from ThresholdSpec because
    it has no column/aggregation and is not consumed by build_derived_case_facts.

    No active seed uses this field. Retained for schema stability and in case
    a future use case requires a trajectory target.
    """

    reduction_target_pct: float  # negative = reduction, e.g. -57.0 for -57 %
    reference_year: int          # e.g. 1990
    note: str = ""               # provenance / TODO marker


class OutputProfile(BaseModel):
    """WHAT the case must produce — format and depth (the 'o' in pi(c, o, a))."""

    output_type: Literal["binary", "qualitative", "quantitative"]
    form: Literal["yes_no_alert", "structured_memo", "numeric_measurement"]
    description: str = ""


class AssuranceProfile(BaseModel):
    """HOW robust the answer must be — evidence / verification / audit depth ('a').

    Declared in the registry as a demand-side signal.  CP1 derives
    assurance_level independently from registry intent + R2/D2 evidence
    (CP1.md signal #8); this registry value is the declared demand, not an
    enforced floor.
    """

    level: Literal["inform_only", "law_reference_required", "independently_verifiable"]
    law_reference_required: bool = False
    dual_method_required: bool = False
    human_signoff_required: bool = False
    # bridge to existing CaseEnvelope.assurance_level (envelope.py:32)
    envelope_assurance_level: Literal[
        "routine", "elevated", "audit_ready", "regulatory"
    ] = "routine"


class _LegacyEmptyContract:
    """Compatibility view for legacy callers; not part of registry semantics."""

    scope_policy = "field_discovery"
    claims: list[Any] = []

    @staticmethod
    def model_dump() -> dict[str, Any]:
        return {}


class DocumentSources(BaseModel):
    """Document-family use cases (pipeline_family="document") only.

    Filesystem roots for the RD1/RD2 deterministic corpus extraction
    (src/epoch_switch/corpus/). Tabular use cases (UC1-UC3) leave this None —
    they read via D1's CSV catalog instead.
    """

    # All four dirs are relative to config.PROJECT_ROOT (repo root).
    baseline_dir: str        # e.g. "regulations/esrs/baseline_2025_amended"
    target_dir: str          # e.g. "regulations/esrs/2026"
    helper_dir: str          # e.g. "regulations/comparison_helpers"
    company_report_dir: str  # e.g. "data/siemens_sustainability_2025"
    standards: list[str]     # e.g. ["E1", "E2", "E3", "E4", "E5"]


class UsecaseSeed(BaseModel):
    id: str
    natural_request: str
    regulation_refs: list[str] = Field(min_length=1)
    regulation_sources: dict[str, str]
    site_filter: dict[str, Any]
    time_window: tuple[str, str]
    expected_output: str
    pipeline_family: Literal["tabular", "document"] = "tabular"
    # Document-family use cases (UC4+) bypass DA1/D1/D2/D3 -- see
    # AGENTS.md's UC4 note. None for every tabular seed.
    document_sources: DocumentSources | None = None
    output_profile: OutputProfile
    assurance_profile: AssuranceProfile
    expected_topology: Literal["Direct", "Debate", "Coalition"] | None = None
    # EVAL ANCHOR ONLY — selector pi is never constrained to this value.
    # Λ = {Direct, Debate, Coalition}: selector may choose but may not invent
    # a topology not in Λ (thesis proposal.tex:338).
    threshold_parameters: dict[str, ThresholdSpec] = Field(default_factory=dict)
    reduction_target: ReductionTargetSpec | None = None
    computation_spec: ComputationSpec | None = None
    # When None, D3 falls back to kind="threshold" using threshold_parameters
    # (UC1 backward-compatible default).
    time_grain: Literal["quarter", "year", "multi_year"] = "year"
    # Declared grain for the data request. DA1 and the handoff builder use this
    # as the default; the human approval gate can still override it (isolated-
    # memory principle — the approved handoff is the single source of truth).

    @property
    def expected_output_family(self) -> str:
        """Compatibility alias used by the legacy pipeline."""
        return self.expected_output

    @property
    def assessment_contract(self) -> _LegacyEmptyContract:
        """Legacy compatibility without prescribing R1 fields."""
        return _LegacyEmptyContract()

    def operative_snapshot(self) -> dict[str, Any]:
        """Seed view safe to pass to operative regulation agents (e.g. R2).

        HARD RULE: ``expected_topology`` is an EVAL ANCHOR ONLY and must NEVER
        be passed as content to an operative agent.  The selector π and the eval
        harness are the only consumers of ``expected_topology``.  Any future
        eval-only field must be added to the exclude set here, not silenced at
        call sites.
        """
        return self.model_dump(exclude={"expected_topology"})

    def demand_snapshot(self) -> dict[str, Any]:
        """Demand axis — WHAT the user asked + HOW it must be delivered.

        The ONLY registry channel that reaches CP1.  Owned solely by the
        registry; never finalized downstream (that is the scope axis).
        ``expected_topology`` is excluded (eval anchor); scope fields are
        excluded (finalized by R2/DA1/D2 — see scope_snapshot).
        """
        return {
            "registry_id": self.id,
            "natural_request": self.natural_request,
            "expected_output": self.expected_output,
            "output_profile": self.output_profile.model_dump(),
            "assurance_profile": {
                k: v
                for k, v in self.assurance_profile.model_dump().items()
                if k != "dual_method_required"
            },
        }

    def scope_snapshot(self) -> dict[str, Any]:
        """Scope axis — WHICH sites / time window / regulation + constants.

        Finalized by R1 → R2 → DA1 → D1 → D2; not sent to CP1 as raw
        registry data (CP1 receives finalized scope via approved_scope and
        r2_in_scope_findings).  Canonical home for any scope-only consumer.
        """
        return {
            "registry_id": self.id,
            "regulation_refs": list(self.regulation_refs),
            "regulation_sources": dict(self.regulation_sources),
            "site_filter": deepcopy(self.site_filter),
            "time_window": self.time_window,
            "time_grain": self.time_grain,
            "threshold_parameters": {
                k: v.model_dump() for k, v in self.threshold_parameters.items()
            },
            "reduction_target": (
                self.reduction_target.model_dump() if self.reduction_target else None
            ),
            "computation_spec": (
                self.computation_spec.model_dump() if self.computation_spec else None
            ),
            "pipeline_family": self.pipeline_family,
            "document_sources": (
                self.document_sources.model_dump() if self.document_sources else None
            ),
        }


# ---------------------------------------------------------------------------
# Use Case 1 — EnEfG Threshold Compliance Check (Binary / Direct)
# ---------------------------------------------------------------------------
UC1 = UsecaseSeed(
    id="uc1_enefg_threshold_check",
    natural_request=(
        "Site-level EnEfG prioritisation screen for all German Siemens sites. "
        "EnEfG §8 and §16 obligations are formally placed on the company "
        "(Unternehmen), not individual sites; Siemens AG as a non-SME entity "
        "trivially meets both thresholds at the company level. This analysis "
        "therefore uses the statutory thresholds as a site-level prioritisation "
        "criterion: sites whose three-year rolling average of annual final energy "
        "consumption exceeds the §8 threshold (7,500 MWh/yr) are flagged as "
        "priority candidates for EnMS implementation scope; sites exceeding the "
        "§16 threshold (2,500 MWh/yr) are flagged as priority candidates for "
        "waste-heat avoidance and public register reporting. "
        "Report per-site priority status (obligated / near-breach / compliant) "
        "with the threshold gap and, where applicable, the audit timeline. "
        "Note the grain mismatch as a limitation: strict legal applicability is "
        "at company level, not site level."
    ),
    regulation_refs=["REG-2"],
    regulation_sources={"REG-2": "EnEfG.pdf"},
    site_filter={"country": "DE"},
    time_window=("2022-01-01", "2024-12-31"),
    expected_output=(
        "Per-site priority flags for §8 (EnMS scope candidacy) and §16 "
        "(waste-heat register candidacy), three-year average energy consumption, "
        "threshold gaps, near-breach warnings, cited regulation sections, and an "
        "explicit limitation noting that the statutory obligation is at company "
        "level while this screen operates at site level."
    ),
    output_profile=OutputProfile(
        output_type="binary",
        form="yes_no_alert",
        description=(
            "Per-site binary prioritisation flag: does the site exceed the §8 or "
            "§16 energy threshold and therefore qualify as a priority candidate for "
            "EnMS implementation or waste-heat reporting? Each finding is a yes/no "
            "flag plus the numeric gap to threshold. The legal obligation rests on "
            "the company; this site-level screen is a planning instrument, not a "
            "strict legal compliance determination."
        ),
    ),
    assurance_profile=AssuranceProfile(
        level="inform_only",
        law_reference_required=False,
        dual_method_required=False,
        human_signoff_required=False,
        envelope_assurance_level="routine",
    ),
    expected_topology="Direct",
    time_grain="year",
    threshold_parameters={
        "enefg_8": ThresholdSpec(value=7500.0, column="energy_mwh", aggregation="max"),
        "enefg_16": ThresholdSpec(value=2500.0, column="energy_mwh", aggregation="max"),
    },
    computation_spec=ComputationSpec(kind="threshold", source_domain="energy"),
)

# ---------------------------------------------------------------------------
# Use Case 2 — EU ETS 1 Scope & Obligation Memo (Qualitative / Debate)
# ---------------------------------------------------------------------------
UC2 = UsecaseSeed(
    id="uc2_ets1_scope_memo",
    natural_request=(
        "Produce a structured ETS 1 monitoring-assurance memo for the Siemens "
        "large-manufacturing site portfolio. For each site: identify whether it "
        "falls within ETS 1 scope (stationary installations, energy-intensive "
        "manufacturing, Annex I activities); compute the quarterly CO₂-equivalent "
        "figure by TWO independent methods — (A) energy-consumption-derived: "
        "scope2_market_proxy_t (amount_consumed_mwh × co2_factor, Scope 2 rows) "
        "from the emissions fact table, and "
        "(B) directly-reported Scope 2 emissions: scope2_location_t (co2e_t, "
        "Scope 2 rows) from the same emissions fact table — "
        "cross-check the two figures per site per quarter (FY2025 Q4 through "
        "FY2026 Q2), flag any material discrepancy (|delta| > 5 % of the higher "
        "value), and provide the raw per-site figures for downstream regulatory "
        "interpretation. Reference the specific directive articles that ground "
        "each finding (Art. 14 monitoring, Art. 12 surrender obligation, "
        "Annex I scope)."
    ),
    regulation_refs=["REG-1"],
    regulation_sources={"REG-1": "CELEX_32003L0087_EN_TXT.pdf"},
    site_filter={"size_tier": "large", "bu_rc": ["DI", "SMO"]},
    time_window=("2025-07-01", "2026-03-31"),
    # FY2025 Q4 representative date ≈ Aug-15 2025 (≥ 2025-07-01),
    # FY2026 Q2 representative date ≈ Feb-15 2026 (≤ 2026-03-31).
    # FY2025 Q3 (May-15 2025) and FY2026 Q3 (May-15 2026) fall outside.
    time_grain="quarter",
    expected_output=(
        "A structured memo showing, per site per quarter (FY2025 Q4 through "
        "FY2026 Q2): ETS 1 scope determination, the two independently computed "
        "quarterly CO₂e figures from the emissions fact table — "
        "(A) energy-derived (scope2_market_proxy_t) and "
        "(B) reported Scope 2 (scope2_location_t) — their absolute delta, "
        "a material-discrepancy flag (|delta| > 5 % of the higher value), "
        "method provenance, directive-article citations, and residual limitations."
    ),
    output_profile=OutputProfile(
        output_type="qualitative",
        form="structured_memo",
        description=(
            "Interpretive monitoring-assurance memo: the same quarterly CO₂e claim "
            "is independently reproduced by two methods (energy-derived and "
            "reported-emissions) and reconciled into one per-site per-quarter "
            "conclusion. Every finding must cite the grounding directive article. "
            "Output is for compliance-team review, not a formal regulatory filing."
        ),
    ),
    assurance_profile=AssuranceProfile(
        level="law_reference_required",
        law_reference_required=True,
        dual_method_required=True,
        human_signoff_required=False,
        envelope_assurance_level="elevated",
    ),
    expected_topology="Debate",
    computation_spec=ComputationSpec(
        kind="dual_method",
        source_domain="emissions",
        method_a_column="scope2_market_proxy_t",
        method_b_column="scope2_location_t",
        method_a_label="Energy-derived CO₂e (amount × factor, Scope 2)",
        method_b_label="Reported Scope 2 CO₂e (co2e_t)",
        tolerance=0.05,
    ),
)

# ---------------------------------------------------------------------------
# Use Case 3 — CSRD ESRS E1 Scope 2 Dual-Method Measurement (Quantitative / Coalition)
# ---------------------------------------------------------------------------
UC3 = UsecaseSeed(
    id="uc3_csrd_scope2_measure",
    natural_request=(
        "Produce a CSRD-compliant ESRS E1 Scope 2 CO₂ group disclosure for the "
        "Siemens EMEA portfolio via group-consolidated divisional inventories. "
        "Each in-scope business-unit division (DI, SMO, SI, SRE, Advanta, CDO) "
        "independently compiles, owns, and attests its own divisional Scope 2 "
        "CO₂e sub-total covering its EMEA sites; no division can produce or "
        "sign off another division's figure. The group then consolidates the "
        "separately-owned, separately-signed sub-results into one ESRS E1 "
        "portfolio total, preserving each division's provenance chain and "
        "attestation. Any divisional sub-total not attested by its owning "
        "division must be flagged as unattested in the consolidated report. "
        "The consolidated result must be suitable for inclusion in the "
        "Siemens ESRS E1 Climate Change disclosure and carry a FY2027 "
        "mandatory-reporting readiness assessment (CSRD Art. 8 + ESRS E1-4 "
        "cross-boundary obligations)."
    ),
    regulation_refs=["REG-4"],
    regulation_sources={"REG-4": "CELEX_32022L2464_EN_TXT.pdf"},
    # bu_rc filter: the EMEA business-unit divisions in scope for this use case
    # are DI, SMO, SI, SRE, Advanta, CDO. Country-level regional-company entries
    # ("RC <country>") are not divisions and are excluded from this scope.
    site_filter={"region": "EMEA", "bu_rc": ["DI", "SMO", "SI", "SRE", "Advanta", "CDO"]},
    time_window=("2024-01-01", "2024-12-31"),
    time_grain="year",
    expected_output=(
        "A FY2024 group-consolidated ESRS E1 Scope 2 report showing, per "
        "in-scope division: divisionally-owned Scope 2 sub-total (tCO₂e), "
        "method provenance, and divisional attestation status; plus the "
        "consolidated portfolio total, an attestation-coverage flag for any "
        "unattested divisional sub-result, and a FY2027 readiness assessment."
    ),
    output_profile=OutputProfile(
        output_type="quantitative",
        form="numeric_measurement",
        description=(
            "Consolidated numeric Scope 2 CO₂ total (tCO₂e) assembled from "
            "divisionally-owned sub-totals: each in-scope division independently "
            "owns, computes, and attests its figure; no single team can own the "
            "full group disclosure. The group consolidates the separately-signed "
            "sub-results. Provenance chain and per-division attestation status "
            "must be included in the artifact."
        ),
    ),
    assurance_profile=AssuranceProfile(
        level="independently_verifiable",
        law_reference_required=True,
        dual_method_required=False,
        human_signoff_required=True,
        envelope_assurance_level="regulatory",
    ),
    expected_topology="Coalition",
    computation_spec=ComputationSpec(
        kind="consolidation",
        source_domain="emissions",
        group_by_column="bu_rc_group",
        measure_column="scope2_location_t",
    ),
)


# ---------------------------------------------------------------------------
# Use Case 2.1 — EU ETS 1 Scope & Obligation Memo, Annual Baseline (Qualitative / Debate)
# Variant of UC2 using annual grain over FY2022–FY2024 to demonstrate year-over-year
# dual-method reconciliation. UC2 (above) uses quarterly grain on the most recent data.
# ---------------------------------------------------------------------------
UC2_1 = UsecaseSeed(
    id="uc2_1_ets1_scope_memo_annual",
    natural_request=(
        "Produce a structured ETS 1 monitoring-assurance memo for the Siemens "
        "large-manufacturing site portfolio covering FY2022–FY2024. For each site: "
        "identify whether it falls within ETS 1 scope (stationary installations, "
        "energy-intensive manufacturing, Annex I activities); compute the annual "
        "CO₂-equivalent figure by TWO independent methods — (A) energy-consumption-"
        "derived: scope2_market_proxy_t (amount_consumed_mwh × co2_factor, Scope 2 "
        "rows) from the emissions fact table, and (B) directly-reported Scope 2 "
        "emissions: scope2_location_t (co2e_t, Scope 2 rows) from the same emissions "
        "fact table — cross-check the two figures per site per year, flag any material "
        "discrepancy (|delta| > 5 % of the higher value), and provide year-over-year "
        "trend and the raw per-site figures for downstream regulatory interpretation. "
        "Reference the specific directive articles that ground each finding (Art. 14 "
        "monitoring, Art. 12 surrender obligation, Annex I scope)."
    ),
    regulation_refs=["REG-1"],
    regulation_sources={"REG-1": "CELEX_32003L0087_EN_TXT.pdf"},
    site_filter={"size_tier": "large", "bu_rc": ["DI", "SMO"]},
    time_window=("2022-01-01", "2024-12-31"),
    time_grain="year",
    expected_output=(
        "A structured memo showing, per site per year (FY2022–FY2024): ETS 1 scope "
        "determination, the two independently computed annual CO₂e figures from the "
        "emissions fact table — (A) energy-derived (scope2_market_proxy_t) and "
        "(B) reported Scope 2 (scope2_location_t) — their absolute delta, "
        "a material-discrepancy flag (|delta| > 5 % of the higher value), "
        "year-over-year trend, method provenance, directive-article citations, "
        "and residual limitations."
    ),
    output_profile=OutputProfile(
        output_type="qualitative",
        form="structured_memo",
        description=(
            "Interpretive monitoring-assurance memo: the same annual CO₂e claim is "
            "independently reproduced by two methods (energy-derived and "
            "reported-emissions) and reconciled into one per-site per-year conclusion. "
            "Three-year coverage enables year-over-year trend analysis. "
            "Every finding must cite the grounding directive article. "
            "Output is for compliance-team review, not a formal regulatory filing."
        ),
    ),
    assurance_profile=AssuranceProfile(
        level="law_reference_required",
        law_reference_required=True,
        dual_method_required=True,
        human_signoff_required=False,
        envelope_assurance_level="elevated",
    ),
    expected_topology="Debate",
    computation_spec=ComputationSpec(
        kind="dual_method",
        source_domain="emissions",
        method_a_column="scope2_market_proxy_t",
        method_b_column="scope2_location_t",
        method_a_label="Energy-derived CO₂e (amount × factor, Scope 2)",
        method_b_label="Reported Scope 2 CO₂e (co2e_t)",
        tolerance=0.05,
    ),
)


# ---------------------------------------------------------------------------
# Use Case 4 — ESRS 2025-Amended -> 2026-Revised E1-E5 Impact (Qualitative /
# document pipeline; three independent risk-posture reads + one synthesis)
# ---------------------------------------------------------------------------
UC4 = UsecaseSeed(
    id="uc4_esrs_2026_impact",
    natural_request=(
        "Evaluate the impact of the 2026 Revised ESRS E1-E5 on Siemens' FY2025 "
        "Sustainability Statement, comparing it against the 2025 Amended ESRS "
        "E1-E5 (the authoritative baseline that actually governed FY2025 "
        "reporting). For every Disclosure Requirement in Climate Change (E1), "
        "Pollution (E2), Water and Marine Resources (E3), Biodiversity and "
        "Ecosystems (E4), and Resource Use and Circular Economy (E5): identify "
        "which requirements are no longer required in 2026, which are new, "
        "and which were modified, merged, relocated, renumbered, or otherwise "
        "materially changed -- verified only against the two authoritative "
        "texts, never against the non-authoritative EFRAG comparison helpers "
        "alone. Cross-reference each finding against what Siemens actually "
        "reported in its FY2025 Sustainability Statement to determine the real "
        "reporting impact: a regulatory deletion the company never reported "
        "(non-material, not applicable) implies little; simplification of a "
        "disclosure the company reports extensively can be a meaningful "
        "effort reduction. This assessment must be produced from three "
        "genuinely independent risk-posture readings of the same evidence -- "
        "one that favors minimal reporting change, one that balances cost "
        "against assurance risk, and one that treats any ambiguity as a "
        "reason for maximal, audit-ready disclosure -- reconciled into one "
        "final structured comparison table plus a plain-language business "
        "summary. Company-level only, no site or business-unit breakdown."
    ),
    regulation_refs=["REG-ESRS-E1", "REG-ESRS-E2", "REG-ESRS-E3", "REG-ESRS-E4", "REG-ESRS-E5"],
    regulation_sources={
        "REG-ESRS-E1": "esrs/baseline_2025_amended/ESRS_E1_2025_amended.md",
        "REG-ESRS-E2": "esrs/baseline_2025_amended/ESRS_E2_2025_amended.md",
        "REG-ESRS-E3": "esrs/baseline_2025_amended/ESRS_E3_2025_amended.md",
        "REG-ESRS-E4": "esrs/baseline_2025_amended/ESRS_E4_2025_amended.md",
        "REG-ESRS-E5": "esrs/baseline_2025_amended/ESRS_E5_2025_amended.md",
    },
    # Not applicable: UC4 is company-level only, no site/BU breakdown, and
    # bypasses D1's CSV catalog entirely (pipeline_family="document").
    site_filter={},
    time_window=("2024-10-01", "2025-09-30"),  # Siemens FY2025
    time_grain="year",
    expected_output=(
        "A DR/provision-level comparison table (standard, 2025 DR id and "
        "requirement, 2026 DR id and requirement, change status, change "
        "description, both source references, Siemens FY2025 reported status "
        "and source reference, resulting Siemens impact, confidence) covering "
        "every E1-E5 Disclosure Requirement, plus a plain-language executive "
        "summary highlighting the most relevant impacts for Siemens and any "
        "divergence between the three independent risk-posture assessments."
    ),
    output_profile=OutputProfile(
        output_type="qualitative",
        form="structured_memo",
        description=(
            "A DR-level regulatory-change and reporting-impact memo, "
            "independently assessed by three distinct risk postures -- "
            "minimal-change, balanced, and maximum-assurance -- over the same "
            "verified evidence, then reconciled into one final table and "
            "summary. Every row must be traceable to its authoritative source "
            "paragraph(s) and, where applicable, its Siemens report section."
        ),
    ),
    assurance_profile=AssuranceProfile(
        level="law_reference_required",
        law_reference_required=True,
        # Three risk postures are independent interpretations, not distinct
        # methods that reproduce one claim.
        dual_method_required=False,
        human_signoff_required=True,
        envelope_assurance_level="audit_ready",
    ),
    # EVAL ANCHOR ONLY (see operative_snapshot's hard rule) -- three
    # independent reads over one shared scope, reconciled by one synthesizer,
    # is the shape this registry entry describes; TS1 is never told this and
    # its actual choice is a measured signal, not enforced.
    expected_topology="Debate",
    computation_spec=None,
    pipeline_family="document",
    document_sources=DocumentSources(
        baseline_dir="regulations/esrs/baseline_2025_amended",
        target_dir="regulations/esrs/2026",
        helper_dir="regulations/comparison_helpers",
        company_report_dir="data/siemens_sustainability_2025",
        standards=["E1", "E2", "E3", "E4", "E5"],
    ),
)


_REGISTRY: dict[str, UsecaseSeed] = {
    "1": UC1,
    "uc1": UC1,
    "uc1_enefg_threshold_check": UC1,
    "2": UC2,
    "uc2": UC2,
    "uc2_ets1_scope_memo": UC2,
    "2.1": UC2_1,
    "uc2.1": UC2_1,
    "uc2_1": UC2_1,
    "uc2_1_ets1_scope_memo_annual": UC2_1,
    "3": UC3,
    "uc3": UC3,
    "uc3_csrd_scope2_measure": UC3,
    "4": UC4,
    "uc4": UC4,
    "uc4_esrs_2026_impact": UC4,
}


def get(key: str) -> UsecaseSeed:
    # Direct lookup first (full IDs like "uc1_enefg_threshold_check" are registry keys).
    seed = _REGISTRY.get(key.strip())
    if seed is not None:
        return seed
    # Fallback: strip interactive-prompt prefixes like "usecase2" or "usecase 2".
    normalized = key.strip().lower().lstrip("usecase").strip()
    seed = _REGISTRY.get(normalized)
    if seed is None:
        raise KeyError(
            f"Unknown use case: '{key}'. Valid keys: {sorted(_REGISTRY.keys())}"
        )
    return seed


def list_usecases() -> list[tuple[str, UsecaseSeed]]:
    return [
        ("1", UC1),
        ("2", UC2),
        ("2.1", UC2_1),
        ("3", UC3),
        ("4", UC4),
    ]
