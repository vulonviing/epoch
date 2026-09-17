"""Deterministic Siemens Sustainability Statement parser (RD1's report half).

Two independent extractions:

- ``parse_report``: splits each report Markdown file into heading-delimited
  ``ReportSection`` records (### / #### headings, following the file's own
  numbering, e.g. "2.2.6 Metrics").  This is the corpus RM1 grounds its
  citations in.
- ``parse_esrs_index``: a best-effort DR-code -> candidate-section lookup out
  of the ESRS index file.  data/siemens_sustainability_2025/README.md
  documents that this index is an irregular, wrapped multi-column table that
  does not reconstruct as a clean table -- so this function is deliberately
  high-recall / low-precision: it returns *candidate* section references per
  DR code, never a verified mapping.  RM1's LLM step is what turns a
  candidate into an actual exposure finding.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import ReportSection

_HEADING_RE = re.compile(r"(?m)^(?:###|####)\s+(.*)$")
_SECTION_NO_RE = re.compile(r"^(\d+(?:\.\d+)*)\s*(.*)$")
_DR_CODE_RE = re.compile(r"\b(?:DR)?(E[1-5]-\d+)\b")
_SECTION_REF_RE = re.compile(r"\b\d\.\d(?:\.\d)?\b")
_INDEX_WINDOW_CHARS = 300


def parse_report(directory: Path) -> list[ReportSection]:
    """Parse every Markdown file under a Siemens report directory into sections.

    ``topic`` is the immediate parent folder name (e.g. "E1", "00_context"),
    matching the data/siemens_sustainability_2025/ layout documented in its
    own README.
    """
    sections: list[ReportSection] = []
    for path in sorted(directory.rglob("*.md")):
        topic = path.parent.name
        text = path.read_text(encoding="utf-8")
        headings = list(_HEADING_RE.finditer(text))
        for i, heading in enumerate(headings):
            raw_title = heading.group(1).strip()
            body_start = heading.end()
            body_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[body_start:body_end].strip()

            numbered = _SECTION_NO_RE.match(raw_title)
            section_no = numbered.group(1) if numbered else ""
            title = numbered.group(2).strip() if numbered else raw_title

            sections.append(
                ReportSection(
                    topic=topic,
                    section_no=section_no,
                    title=title,
                    text=body,
                    source_file=str(path),
                )
            )
    return sections


def parse_esrs_index(path: Path) -> dict[str, list[str]]:
    """Return DR code -> candidate section-number references (low precision).

    Normalizes the "DRE3-1" spacing defect noted in the source PDF extraction
    to "E3-1".  A DR code with no nearby section-number token yields an empty
    candidate list, which RM1 should read as "no index lead -- fall back to
    reading the topic's report sections directly."
    """
    text = path.read_text(encoding="utf-8")
    candidates: dict[str, list[str]] = {}
    for match in _DR_CODE_RE.finditer(text):
        dr_id = match.group(1)
        window = text[match.end(): match.end() + _INDEX_WINDOW_CHARS]
        refs = _SECTION_REF_RE.findall(window)
        bucket = candidates.setdefault(dr_id, [])
        for ref in refs:
            if ref not in bucket:
                bucket.append(ref)
    return candidates
