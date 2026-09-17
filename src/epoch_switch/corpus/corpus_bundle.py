"""RD1/RD2 hoisting point -- the single place the corpus file-name convention lives.

RD1 (`extract_corpus`) parses every standard's 2025-amended and 2026-revised
Markdown, its helper-log hints, and the Siemens FY2025 report once. RD2
(`map_candidates`) then fills each standard's deterministic candidate list.
Both are pure functions over the existing corpus/ parsers -- no new parsing
logic, only a single call site instead of three (rc1_change_classifier.py,
rm1_exposure_mapper.py, regulation_cli.py's coverage report all did this
parsing independently before).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .candidate_mapper import build_candidates
from .esrs_parser import parse_standard
from .helper_hints import extract_hints
from .models import CandidateMapping, DisclosureRequirement, HelperHint, ReportSection
from .report_parser import parse_esrs_index, parse_report


@dataclass(frozen=True)
class StandardCorpus:
    standard: str
    old_drs: list[DisclosureRequirement]  # 2025-amended
    new_drs: list[DisclosureRequirement]  # 2026-revised
    hints: list[HelperHint]
    candidates: list[CandidateMapping]  # RD2 fills this; empty right after RD1


@dataclass(frozen=True)
class CorpusBundle:
    standards: dict[str, StandardCorpus]  # "E1" -> StandardCorpus
    report_sections: list[ReportSection]  # Siemens FY2025, all topics
    index_candidates: dict[str, list[str]]  # DR code -> candidate section numbers


def extract_corpus(document_sources: dict[str, Any], project_root: Path) -> CorpusBundle:
    """RD1 -- parse every standard's 2025/2026 text, hints, and the Siemens report.

    Deterministic. No candidate mapping yet (StandardCorpus.candidates is empty).
    """
    standards: list[str] = document_sources["standards"]
    baseline_dir = project_root / document_sources["baseline_dir"]
    target_dir = project_root / document_sources["target_dir"]
    helper_dir = project_root / document_sources["helper_dir"]
    company_dir = project_root / document_sources["company_report_dir"]

    def _run_one(standard: str) -> StandardCorpus:
        old = parse_standard(
            baseline_dir / f"ESRS_{standard}_2025_amended.md", standard=standard, version="2025_amended"
        )
        new = parse_standard(target_dir / f"ESRS_{standard}_2026.md", standard=standard, version="2026")
        hint_path = helper_dir / standard / f"log_of_amendments_2025_{standard}.md"
        hints = extract_hints(standard, hint_path) if hint_path.exists() else []
        return StandardCorpus(standard=standard, old_drs=old, new_drs=new, hints=hints, candidates=[])

    with ThreadPoolExecutor(max_workers=len(standards)) as pool:
        futures = {pool.submit(_run_one, standard): i for i, standard in enumerate(standards)}
        ordered: list[StandardCorpus | None] = [None] * len(standards)
        for future in futures:
            ordered[futures[future]] = future.result()

    report_sections = parse_report(company_dir)
    index_candidates = parse_esrs_index(company_dir / "index" / "esrs_index.md")

    return CorpusBundle(
        standards={sc.standard: sc for sc in ordered if sc},
        report_sections=report_sections,
        index_candidates=index_candidates,
    )


def map_candidates(bundle: CorpusBundle) -> CorpusBundle:
    """RD2 -- deterministic old<->new DR candidate mapping for every standard."""
    standards = {
        standard: replace(
            sc, candidates=build_candidates(sc.old_drs, sc.new_drs, sc.hints)
        )
        for standard, sc in bundle.standards.items()
    }
    return replace(bundle, standards=standards)
