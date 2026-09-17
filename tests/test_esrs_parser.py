"""Deterministic-parser regression tests for RD1's ESRS extraction (Phase 1).

Uses the real regulation corpus under regulations/esrs/ -- this parser has no
LLM dependency, so these are plain assertions against known document shape,
not fixtures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from epoch_switch.corpus.esrs_parser import parse_standard

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIR = REPO_ROOT / "regulations" / "esrs" / "baseline_2025_amended"
TARGET_DIR = REPO_ROOT / "regulations" / "esrs" / "2026"

# (standard, expected 2025 DR count, expected 2026 DR count)
EXPECTED_COUNTS = [
    ("E1", 9, 11),
    ("E2", 6, 5),
    ("E3", 5, 4),
    ("E4", 6, 5),
    ("E5", 6, 5),
]


@pytest.mark.parametrize("standard,n_old,n_new", EXPECTED_COUNTS)
def test_dr_counts_match_known_shape(standard: str, n_old: int, n_new: int) -> None:
    old = parse_standard(
        BASELINE_DIR / f"ESRS_{standard}_2025_amended.md", standard=standard, version="2025_amended"
    )
    new = parse_standard(
        TARGET_DIR / f"ESRS_{standard}_2026.md", standard=standard, version="2026"
    )
    assert len(old) == n_old
    assert len(new) == n_new


def test_e1_5_renumbered_to_e1_7_same_title() -> None:
    old = parse_standard(
        BASELINE_DIR / "ESRS_E1_2025_amended.md", standard="E1", version="2025_amended"
    )
    new = parse_standard(TARGET_DIR / "ESRS_E1_2026.md", standard="E1", version="2026")

    e1_5 = next(d for d in old if d.dr_id == "E1-5")
    e1_7 = next(d for d in new if d.dr_id == "E1-7")
    assert e1_5.dr_title == e1_7.dr_title == "Energy consumption and mix"

    old_ids = {p.paragraph_id for p in e1_5.paragraphs}
    assert {"35.", "36.", "37.", "37. (c) i."}.issubset(old_ids)


def test_2026_application_requirements_attach_to_owning_dr() -> None:
    new = parse_standard(TARGET_DIR / "ESRS_E1_2026.md", standard="E1", version="2026")
    e1_7 = next(d for d in new if d.dr_id == "E1-7")
    assert len(e1_7.ar_paragraphs) > 0
    assert e1_7.ar_paragraphs[0].paragraph_id.startswith("AR")
    assert e1_7.ar_paragraphs[0].kind == "application_requirement"


def test_2025_appendix_a_ars_merge_into_main_body_dr() -> None:
    old = parse_standard(
        BASELINE_DIR / "ESRS_E1_2025_amended.md", standard="E1", version="2025_amended"
    )
    e1_1 = next(d for d in old if d.dr_id == "E1-1")
    # Main-body numbered paragraphs and Appendix A's "AR n" paragraphs must
    # both land on the same DisclosureRequirement record.
    assert len(e1_1.paragraphs) > 0
    assert len(e1_1.ar_paragraphs) > 0
    assert all(p.kind == "application_requirement" for p in e1_1.ar_paragraphs)


def test_2025_e5_6_level_three_heading_owns_paragraphs_41_to_43() -> None:
    old = parse_standard(
        BASELINE_DIR / "ESRS_E5_2025_amended.md", standard="E5", version="2025_amended"
    )
    e5_5 = next(d for d in old if d.dr_id == "E5-5")
    e5_6 = next(d for d in old if d.dr_id == "E5-6")

    e5_5_ids = {p.paragraph_id.rstrip(".") for p in e5_5.paragraphs}
    e5_6_ids = {p.paragraph_id.rstrip(".") for p in e5_6.paragraphs}
    assert {"41", "42", "43"} <= e5_6_ids
    assert not {"41", "42", "43"} & e5_5_ids


def test_esrs2_cross_references_are_skipped_not_misattributed() -> None:
    old = parse_standard(
        BASELINE_DIR / "ESRS_E1_2025_amended.md", standard="E1", version="2025_amended"
    )
    # "Disclosure requirement related to ESRS 2 GOV-3 ..." has no E1-<n> code
    # and must not appear as a DR, nor swallow the following E1-1 paragraphs.
    assert all(d.dr_id.startswith("E1-") for d in old)
    e1_1 = next(d for d in old if d.dr_id == "E1-1")
    assert any(p.paragraph_id == "1." or p.paragraph_id.startswith("1.") for p in e1_1.paragraphs) or len(
        e1_1.paragraphs
    ) > 0
