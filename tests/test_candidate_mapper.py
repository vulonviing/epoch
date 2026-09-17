"""Phase 3: deterministic old<->new DR candidate mapping (RD2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from epoch_switch.corpus.candidate_mapper import build_candidates
from epoch_switch.corpus.esrs_parser import parse_standard
from epoch_switch.corpus.helper_hints import extract_hints

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIR = REPO_ROOT / "regulations" / "esrs" / "baseline_2025_amended"
TARGET_DIR = REPO_ROOT / "regulations" / "esrs" / "2026"
HELPER_DIR = REPO_ROOT / "regulations" / "comparison_helpers"


def _candidates(standard: str):
    old = parse_standard(BASELINE_DIR / f"ESRS_{standard}_2025_amended.md", standard=standard, version="2025_amended")
    new = parse_standard(TARGET_DIR / f"ESRS_{standard}_2026.md", standard=standard, version="2026")
    hints = extract_hints(standard, HELPER_DIR / standard / f"log_of_amendments_2025_{standard}.md")
    return build_candidates(old, new, hints)


def test_e1_5_renumbered_to_e1_7_is_a_confident_match() -> None:
    cands = _candidates("E1")
    match = next(c for c in cands if c.old_dr_ids == ["E1-5"])
    assert match.new_dr_ids == ["E1-7"]
    assert match.score >= 0.9


def test_e1_new_drs_have_no_old_counterpart() -> None:
    cands = _candidates("E1")
    new_only = {c.new_dr_ids[0] for c in cands if not c.old_dr_ids}
    assert {"E1-2", "E1-3"}.issubset(new_only)


def test_every_old_dr_appears_in_exactly_one_candidate_row() -> None:
    old = parse_standard(BASELINE_DIR / "ESRS_E2_2025_amended.md", standard="E2", version="2025_amended")
    cands = _candidates("E2")
    covered = [dr_id for c in cands for dr_id in c.old_dr_ids]
    assert sorted(covered) == sorted(dr.dr_id for dr in old)


@pytest.mark.parametrize(
    ("standard", "old_dr_id"),
    [("E2", "E2-6"), ("E3", "E3-5"), ("E4", "E4-6"), ("E5", "E5-6")],
)
def test_removed_financial_effects_dr_has_empty_target(
    standard: str, old_dr_id: str
) -> None:
    match = next(c for c in _candidates(standard) if c.old_dr_ids == [old_dr_id])
    assert match.new_dr_ids == []
    assert match.score == 0.0


def test_e1_financial_effects_dr_keeps_real_target_counterpart() -> None:
    match = next(c for c in _candidates("E1") if c.old_dr_ids == ["E1-9"])
    assert match.new_dr_ids == ["E1-11"]


@pytest.mark.parametrize("standard", ["E1", "E2", "E3", "E4", "E5"])
def test_no_candidate_mapping_silently_forces_a_false_one_to_one(standard: str) -> None:
    # Every ambiguous case must carry more than one new_dr_id candidate, or
    # be explicitly empty (Removed) / explicitly new (no old_dr_ids) --
    # never a single confident-looking id backed by a low score.
    for c in _candidates(standard):
        if len(c.new_dr_ids) == 1 and c.old_dr_ids and c.score < 0.45:
            assert "ambiguous" in c.basis or "generic" in c.basis or "boilerplate" in c.basis
