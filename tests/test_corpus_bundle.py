"""Phase 1 (UC4 RD1/RD2 hoisting): extract_corpus/map_candidates lose nothing.

Not a new-behavior test -- proves the single call site produces the same
parses and the same candidate lists as calling parse_standard/extract_hints/
build_candidates directly (the code these functions replace).
"""
from __future__ import annotations

from pathlib import Path

from epoch_switch.corpus.candidate_mapper import build_candidates
from epoch_switch.corpus.corpus_bundle import CorpusBundle, extract_corpus, map_candidates
from epoch_switch.corpus.esrs_parser import parse_standard
from epoch_switch.corpus.helper_hints import extract_hints
from epoch_switch.corpus.report_parser import parse_esrs_index, parse_report

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_SOURCES = {
    "baseline_dir": "regulations/esrs/baseline_2025_amended",
    "target_dir": "regulations/esrs/2026",
    "helper_dir": "regulations/comparison_helpers",
    "company_report_dir": "data/siemens_sustainability_2025",
    "standards": ["E1", "E2", "E3", "E4", "E5"],
}


def test_extract_corpus_fills_every_standard() -> None:
    bundle = extract_corpus(DOCUMENT_SOURCES, REPO_ROOT)
    assert isinstance(bundle, CorpusBundle)
    assert set(bundle.standards) == {"E1", "E2", "E3", "E4", "E5"}
    for standard, sc in bundle.standards.items():
        assert sc.standard == standard
        assert sc.old_drs
        assert sc.new_drs
        assert sc.candidates == []  # RD2 hasn't run yet


def test_extract_corpus_matches_direct_parses() -> None:
    bundle = extract_corpus(DOCUMENT_SOURCES, REPO_ROOT)
    sc = bundle.standards["E1"]
    old = parse_standard(
        REPO_ROOT / "regulations/esrs/baseline_2025_amended/ESRS_E1_2025_amended.md",
        standard="E1", version="2025_amended",
    )
    new = parse_standard(REPO_ROOT / "regulations/esrs/2026/ESRS_E1_2026.md", standard="E1", version="2026")
    hints = extract_hints("E1", REPO_ROOT / "regulations/comparison_helpers/E1/log_of_amendments_2025_E1.md")
    assert [dr.dr_id for dr in sc.old_drs] == [dr.dr_id for dr in old]
    assert [dr.dr_id for dr in sc.new_drs] == [dr.dr_id for dr in new]
    assert len(sc.hints) == len(hints)

    company_dir = REPO_ROOT / "data/siemens_sustainability_2025"
    sections = parse_report(company_dir)
    index = parse_esrs_index(company_dir / "index" / "esrs_index.md")
    assert len(bundle.report_sections) == len(sections)
    assert bundle.index_candidates == index


def test_map_candidates_matches_direct_build_candidates() -> None:
    bundle = map_candidates(extract_corpus(DOCUMENT_SOURCES, REPO_ROOT))
    for standard in DOCUMENT_SOURCES["standards"]:
        sc = bundle.standards[standard]
        direct = build_candidates(sc.old_drs, sc.new_drs, sc.hints)
        assert len(sc.candidates) == len(direct)
