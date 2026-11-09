"""Unit tests for HandoffDataExecutor._normalize_handoff.

Focus: the inline envelope→filter alias loop was removed from _normalize_handoff
to prevent it from re-injecting composite bucket labels (e.g. "EMEA" →
cdp_region) after the handoff has already been expanded by build_handoff_request.
These tests verify that _normalize_handoff delegates backfill entirely to
normalize_data_request/_filters_from_envelope and does NOT produce invalid
cdp_region values.
"""
from __future__ import annotations

import pytest

from epoch_switch.agents.data.d1_loader.d1_handoff_executor import HandoffDataExecutor
from epoch_switch.core.data_catalog import DataCatalogBuilder
from epoch_switch.core.envelope import CaseEnvelope


@pytest.fixture(scope="module")
def catalog():
    return DataCatalogBuilder().build()


def _make_envelope(site_filter: dict) -> CaseEnvelope:
    return CaseEnvelope(
        case_id="test-uc3",
        usecase_ref="uc3_csrd_scope2",
        natural_request="Compute Scope 2 emissions for EMEA sites.",
        regulation_refs=["REG-4"],
        site_filter=site_filter,
        time_window=("2022-01-01", "2024-12-31"),
        expected_output_family="quantitative",
    )


def test_normalize_handoff_emea_no_cdp_region_injected(catalog):
    """_normalize_handoff must not inject cdp_region:'EMEA' when the handoff carries emea:[True].

    Root cause of UC3 failure: the former inline alias_map loop aliased
    region → cdp_region before _filters_from_envelope could expand 'EMEA'.
    The loop has been removed; backfill now goes through the single
    EMEA-aware _filters_from_envelope.
    """
    executor = HandoffDataExecutor()
    envelope = _make_envelope({"region": "EMEA"})
    # This is the kind of handoff build_handoff_request produces after the fix:
    # emea is already present, no cdp_region.
    handoff = {
        "request_id": "test-uc3",
        "intent": "Scope 2 EMEA.",
        "source_domain": "emissions",
        "entity_grain": "site",
        "time_grain": "year",
        "time_window": {"start": "2022-01-01", "end": "2024-12-31"},
        "measures": ["scope2_market_proxy_t"],
        "filters": {"emea": [True]},
        "quality_policy": "exclude_inconsistent",
    }

    normalized = executor._normalize_handoff(handoff, envelope, catalog)
    filters = normalized.get("filters", {})

    assert "cdp_region" not in filters, (
        f"_normalize_handoff must not inject cdp_region from EMEA envelope. "
        f"Filters: {filters}"
    )
    assert "emea" in filters, f"emea filter must survive normalization. Filters: {filters}"
    assert filters["emea"] == [True]

    validation = normalized.get("validation", {})
    assert validation.get("ok") is True, (
        f"Normalized request must pass validation. Errors: {validation.get('errors')}"
    )


def test_normalize_handoff_literal_region_still_works(catalog):
    """A literal cdp_region value ('Europe') still works correctly.

    When site_filter={"region": "Europe"} (not a composite bucket), the backfill
    should produce cdp_region:'Europe' and validation must pass.
    """
    executor = HandoffDataExecutor()
    envelope = _make_envelope({"region": "Europe"})
    handoff = {
        "request_id": "test-uc1",
        "intent": "Energy for Europe.",
        "source_domain": "energy",
        "entity_grain": "site",
        "time_grain": "year",
        "time_window": {"start": "2022-01-01", "end": "2024-12-31"},
        "measures": ["energy_mwh"],
        "filters": {},
        "quality_policy": "exclude_inconsistent",
    }

    normalized = executor._normalize_handoff(handoff, envelope, catalog)
    filters = normalized.get("filters", {})
    # Literal region alias → cdp_region is expected here
    assert filters.get("cdp_region") == "Europe", (
        f"Literal cdp_region value must pass through. Filters: {filters}"
    )
    validation = normalized.get("validation", {})
    assert validation.get("ok") is True, (
        f"Europe is a valid cdp_region. Errors: {validation.get('errors')}"
    )
