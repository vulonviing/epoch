"""Deterministic ESRS Disclosure Requirement parser (RD1's core, no LLM).

Both the 2025-amended baseline and the 2026 revised standards share the same
Markdown convention (see regulations/esrs/README.md):

    ### Disclosure Requirement E1-5 - Energy consumption and mix
    **35.** <top-level paragraph text>
      **37. (a)** <sub-point text>
        **37. (c) i.** <nested sub-point text>
    **AR 18** *for para. 26 (...)* <application-requirement text>

2025 repeats a DR's heading a second time under "## Appendix A" to carry its
Application Requirements; 2026 nests an "AR" block directly inside the DR's
first occurrence.  Both layouts are handled uniformly here: paragraphs are
classified by their own marker text ("AR ..." vs a plain number), not by
which heading occurrence they appear under, then merged into the same
DisclosureRequirement by dr_id.

DR numbering shifts between versions (2025 E1-5 "Energy consumption and mix"
is 2026 E1-7) -- this parser does not attempt to reconcile that; it only
extracts each version's own DR list.  Cross-version matching is
candidate_mapper.py's job.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import DisclosureRequirement, Provision, ProvisionKind, Version

_DR_HEADING_RE = re.compile(
    r"(?m)^#{3,4}\s+Disclosure [Rr]equirements?\s+(.*)$"
)
_DR_CODE_RE = re.compile(r"\bE[1-5]-\d+\b")
_PARA_MARKER_RE = re.compile(r"(?m)^[ \t]*\*\*([^\n*]{1,40}?)\*\*")
_PARA_MARKER_SHAPE_RE = re.compile(r"^(AR\s*)?\d+[a-zA-Z0-9.() ]*$", re.IGNORECASE)


def _classify(marker: str) -> ProvisionKind:
    marker = marker.strip()
    if re.match(r"^AR\b", marker, re.IGNORECASE):
        return "application_requirement"
    if "(" in marker:
        return "sub_point"
    return "dr_body"


def _extract_dr_id_and_title(heading_rest: str) -> tuple[str, str] | None:
    """Return (dr_id, dr_title) for a heading, or None if it has no E-code.

    Headings without an E{n}-{digits} code (e.g. "related to ESRS 2 GOV-3 ...",
    "SBM 3 - Material impacts ...") are cross-cutting ESRS-1/2 references, out
    of the E1-E5 comparison scope per regulations/README.md -- skipped here.
    """
    match = _DR_CODE_RE.search(heading_rest)
    if match is None:
        return None
    dr_id = match.group(0)
    title = heading_rest[match.end():]
    title = re.sub(r"^[\s–—-]+", "", title).strip()
    if not title:
        title = heading_rest.replace(dr_id, "").strip(" –—-")
    return dr_id, title


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def parse_standard(path: Path, *, standard: str, version: Version) -> list[DisclosureRequirement]:
    """Parse one ESRS standard Markdown file into its Disclosure Requirements.

    ``standard`` is the short code (e.g. "E1"); ``version`` distinguishes the
    2025-amended baseline from the 2026 revision so downstream records carry
    their own provenance without re-deriving it from the file path.
    """
    text = path.read_text(encoding="utf-8")
    source_file = str(path)

    headings = list(_DR_HEADING_RE.finditer(text))
    drs: dict[str, DisclosureRequirement] = {}

    for i, heading in enumerate(headings):
        parsed = _extract_dr_id_and_title(heading.group(1))
        if parsed is None:
            continue
        dr_id, dr_title = parsed

        block_start = heading.end()
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        block_text = text[block_start:block_end]

        dr = drs.get(dr_id)
        if dr is None:
            dr = DisclosureRequirement(
                standard=standard,
                version=version,
                dr_id=dr_id,
                dr_title=dr_title,
                source_file=source_file,
            )
            drs[dr_id] = dr

        markers = [
            m for m in _PARA_MARKER_RE.finditer(block_text)
            if _PARA_MARKER_SHAPE_RE.match(m.group(1).strip())
        ]
        for j, marker in enumerate(markers):
            para_id = re.sub(r"\s+", " ", marker.group(1)).strip()
            body_start = marker.end()
            body_end = markers[j + 1].start() if j + 1 < len(markers) else len(block_text)
            body = block_text[body_start:body_end].strip().lstrip(":").strip()
            kind = _classify(para_id)
            provision = Provision(
                standard=standard,
                version=version,
                dr_id=dr_id,
                paragraph_id=para_id,
                kind=kind,
                text=body,
                source_file=source_file,
                source_line=_line_number(text, block_start + marker.start()),
            )
            if kind == "application_requirement":
                dr.ar_paragraphs.append(provision)
            else:
                dr.paragraphs.append(provision)

    return list(drs.values())
