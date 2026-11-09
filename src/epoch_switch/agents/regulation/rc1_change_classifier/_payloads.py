"""Shared DR-to-payload serialization for the RC1.1/RC1.2 blind-pass pair.

Both `rc1_1_blind_matcher.py` (RC1.1, blind matching) and
`rc1_change_classifier.py` (RC1.2, reconciliation) send the full authoritative
paragraph text for every DR to the LLM -- this module is the single place that
shape is built, so both agents stay byte-identical on how a DR is rendered.
"""
from __future__ import annotations

from typing import Any

from epoch_switch.corpus.models import DisclosureRequirement

_AR_TEXT_CHARS = 300  # Application-Requirement text is truncated in the LLM
# payload to keep token cost bounded -- both agents see every AR paragraph_id
# (so they know how much guidance exists) but only a preview of long
# calculation guidance text, which is rarely decisive for change classification.


def _dr_payload(dr: DisclosureRequirement) -> dict[str, Any]:
    return {
        "dr_id": dr.dr_id,
        "dr_title": dr.dr_title,
        "paragraphs": [
            {"id": p.paragraph_id, "kind": p.kind, "text": p.text} for p in dr.paragraphs
        ],
        "application_requirements": [
            {"id": p.paragraph_id, "text": p.text[:_AR_TEXT_CHARS]} for p in dr.ar_paragraphs
        ],
        "source_file": dr.source_file,
    }
