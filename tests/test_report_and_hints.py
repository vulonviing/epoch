"""Phase 2: Siemens report parser + ESRS index + helper-hint extraction."""
from __future__ import annotations

from pathlib import Path

from epoch_switch.corpus.helper_hints import extract_hints
from epoch_switch.corpus.report_parser import parse_esrs_index, parse_report

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPANY_DIR = REPO_ROOT / "data" / "siemens_sustainability_2025"
HELPER_DIR = REPO_ROOT / "regulations" / "comparison_helpers"

EXPECTED_MISSING_DR_CODES = {"E1-9", "E2-4", "E2-6", "E3-5", "E4-6", "E5-6"}


def test_report_sections_cover_all_environmental_topics() -> None:
    sections = parse_report(COMPANY_DIR)
    topics = {s.topic for s in sections}
    assert {"E1", "E2", "E3", "E4", "E5"}.issubset(topics)
    assert len(sections) > 0


def test_esrs_index_finds_reported_codes_and_omits_the_known_gaps() -> None:
    index = parse_esrs_index(COMPANY_DIR / "index" / "esrs_index.md")
    assert len(index) == 26
    assert EXPECTED_MISSING_DR_CODES.isdisjoint(index.keys())
    # E1-5 (2025 numbering) is Siemens's energy-consumption disclosure.
    assert "2.2.6" in index.get("E1-5", [])


def test_helper_hints_are_never_authoritative() -> None:
    hints = extract_hints("E1", HELPER_DIR / "E1" / "log_of_amendments_2025_E1.md")
    assert len(hints) > 0
    assert all(h.authoritative is False for h in hints)
    assert all(h.baseline_mismatch is True for h in hints)
    assert all(h.standard == "E1" for h in hints)
