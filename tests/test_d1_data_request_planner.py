"""Unit tests for D1 normalization/validation helpers.

Focus: composite region bucket expansion in _filters_from_envelope, and the
strengthened _allowed_filter_values that validates derived dict filters.
"""
from __future__ import annotations

import pytest

from epoch_switch.agents.data.d1_loader.d1_data_request_planner import (
    _filters_from_envelope,
    _allowed_filter_values,
)
from epoch_switch.core.data_catalog import DataCatalogBuilder


# ── _filters_from_envelope: EMEA expansion ────────────────────────────────────


def test_filters_from_envelope_emea_expands_to_derived():
    """region:'EMEA' must produce emea:[True], not cdp_region:'EMEA'."""
    result = _filters_from_envelope({"region": "EMEA"})
    assert result == {"emea": [True]}, f"Expected {{'emea': [True]}}, got: {result}"
    assert "cdp_region" not in result


def test_filters_from_envelope_emea_case_insensitive():
    """'emea' lower-case is also recognised as a composite bucket."""
    result = _filters_from_envelope({"region": "emea"})
    assert result == {"emea": [True]}


def test_filters_from_envelope_literal_region_passes_through():
    """A literal cdp_region value ('Europe') must still alias to cdp_region."""
    result = _filters_from_envelope({"region": "Europe"})
    assert "cdp_region" in result, f"Literal region must alias to cdp_region. Got: {result}"
    assert result["cdp_region"] == "Europe"
    assert "emea" not in result


def test_filters_from_envelope_emea_only_missing_already_set():
    """If emea is already in current with a value, only_missing=True must not overwrite it."""
    result = _filters_from_envelope(
        {"region": "EMEA"},
        only_missing=True,
        current={"emea": [True]},
    )
    # emea already set — nothing new should be added
    assert result == {}, f"only_missing should add nothing when emea is already set. Got: {result}"


def test_filters_from_envelope_emea_only_missing_empty_current():
    """If current has emea but it's empty/None, only_missing=True must fill it."""
    result = _filters_from_envelope(
        {"region": "EMEA"},
        only_missing=True,
        current={"emea": None},
    )
    assert result == {"emea": [True]}


def test_filters_from_envelope_non_region_keys_unaffected():
    """Non-region keys (country, bu_rc) are unaffected by the EMEA expansion."""
    result = _filters_from_envelope({"country": "DE", "bu_rc": "GP"})
    assert "country_code" in result
    assert result["country_code"] == "DE"


def test_filters_from_envelope_scope_aliases():
    """'scope' and 'emission_scope' both map to the executor 'scope' column."""
    r1 = _filters_from_envelope({"scope": "Scope 2"})
    assert r1 == {"scope": "Scope 2"}

    r2 = _filters_from_envelope({"emission_scope": "Scope 2"})
    assert r2 == {"scope": "Scope 2"}


# ── _allowed_filter_values: derived dict filters validated ────────────────────


def test_allowed_filter_values_includes_emea(catalog):
    """emea declared as a dict with 'values': [True, False] must be included."""
    allowed = _allowed_filter_values(catalog)
    assert "emea" in allowed, "emea derived filter must appear in allowed values"
    assert allowed["emea"] == {True, False}


def test_allowed_filter_values_includes_size_tier(catalog):
    """size_tier declared as a dict with 'values' must be included."""
    allowed = _allowed_filter_values(catalog)
    assert "size_tier" in allowed
    assert allowed["size_tier"] == {"large", "medium", "small"}


def test_allowed_filter_values_cdp_region_still_bare_list(catalog):
    """cdp_region is a bare list — must still be included and unchanged."""
    allowed = _allowed_filter_values(catalog)
    assert "cdp_region" in allowed
    # Must contain real region values, not EMEA (which is a composite bucket label)
    assert "Europe" in allowed["cdp_region"]
    assert "EMEA" not in allowed["cdp_region"]


@pytest.fixture(scope="module")
def catalog():
    return DataCatalogBuilder().build()
