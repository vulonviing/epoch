"""Extract non-authoritative candidate leads from the EFRAG helper documents.

comparison_helpers/E*/log_of_amendments_2025_E*.md tags each amendment with
one or more of AMENDED / DELETED / MOVED / MERGED / NEW / UNCHANGED, followed
by a short rationale.  Per regulations/README.md's hard rule, these documents
compare 2023-enacted against the December-2025 Draft Amended text -- not our
2025-amended -> 2026-revised pair -- so every hint extracted here is tagged
``baseline_mismatch=True, authoritative=False`` and must never be the sole
basis for a classification (RC1's job is to verify against the authoritative
baseline/target texts).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, get_args

from .models import HelperHint

_TOKEN_LITERAL = Literal["AMENDED", "DELETED", "MOVED", "MERGED", "NEW", "UNCHANGED"]
_KNOWN_TOKENS = set(get_args(_TOKEN_LITERAL))
_TAG_RE = re.compile(r"\[([A-Z, ]+)\]")
_RATIONALE_WINDOW_CHARS = 240


def extract_hints(standard: str, path: Path) -> list[HelperHint]:
    """Pull every tagged amendment out of one Log of Amendments file.

    A combined tag like "[AMENDED, MOVED]" yields one HelperHint per token,
    sharing the same trailing rationale text.
    """
    text = path.read_text(encoding="utf-8")
    source_file = str(path)
    hints: list[HelperHint] = []

    for match in _TAG_RE.finditer(text):
        tokens = [t.strip() for t in match.group(1).split(",")]
        tokens = [t for t in tokens if t in _KNOWN_TOKENS]
        if not tokens:
            continue
        rationale = text[match.end(): match.end() + _RATIONALE_WINDOW_CHARS]
        rationale = rationale.split("[", 1)[0].strip()
        for token in tokens:
            hints.append(
                HelperHint(
                    standard=standard,
                    token=token,  # type: ignore[arg-type]
                    text=rationale,
                    source_file=source_file,
                )
            )
    return hints
