"""Deterministic old-DR <-> new-DR candidate mapping (RD2's core, no LLM).

DR numbers shift between the 2025-amended baseline and the 2026 revision
(e.g. 2025 "E1-5 Energy consumption and mix" is 2026 "E1-7"), so identifiers
cannot be trusted -- matching is by title-token overlap instead, weighted by
how distinctive each token is within the standard (a generic word shared by
every DR in a standard, e.g. "biodiversity" inside every E4 title, carries far
less signal than a word only one DR uses, e.g. "consumption").

This module never claims a final classification.  Its only job is to narrow
the search: pair up plausible old/new titles so RC1 (the LLM classifier) only
has to verify a short candidate list against the authoritative texts instead
of reading all of E1-E5 for every DR.  Ambiguity is preserved on purpose --
see build_candidates' docstring.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .models import CandidateMapping, DisclosureRequirement, HelperHint

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "from",
    "with", "its", "their", "is", "are", "related", "relating", "material",
    "risks", "opportunities", "anticipated", "financial", "effects",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A confident 1:1 match needs a decent absolute score *and* a clear lead over
# the runner-up; otherwise the ambiguity is preserved rather than resolved.
_CONFIDENT_SCORE = 0.45
_CONFIDENT_GAP = 0.15
_CANDIDATE_FLOOR = 0.08
_MAX_CANDIDATES_PER_OLD_DR = 3
_MIN_DISTINCTIVE_TOKENS = 2
_ANTICIPATED_FINANCIAL_EFFECTS_ANCHOR = "anticipated financial effects"


def _tokenize(title: str) -> set[str]:
    tokens = {t for t in _TOKEN_RE.findall(title.lower()) if t not in _STOPWORDS}
    return tokens


def _has_financial_effects_anchor(title: str) -> bool:
    """Preserve the DR family that generic title stopwords would erase."""
    normalized = " ".join(_TOKEN_RE.findall(title.lower()))
    return _ANTICIPATED_FINANCIAL_EFFECTS_ANCHOR in normalized


@dataclass
class _Scored:
    dr_id: str
    score: float


def _weighted_jaccard(a: set[str], b: set[str], weight: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    inter_w = sum(weight[t] for t in inter)
    union_w = sum(weight[t] for t in union)
    return inter_w / union_w if union_w else 0.0


def build_candidates(
    old_drs: list[DisclosureRequirement],
    new_drs: list[DisclosureRequirement],
    hints: list[HelperHint],
) -> list[CandidateMapping]:
    """Return candidate old<->new DR pairings for one standard.

    Every old DR yields exactly one CandidateMapping:
    - a single confident new_dr_id when one candidate clearly leads,
    - several new_dr_ids (score-ordered) when the lead is not clear --
      "mapping_uncertain" is RC1's job to set once it has read the actual
      paragraph text, not this module's,
    - an empty new_dr_ids list when nothing scores above the floor --
      Removed candidate.
    Any new DR that never appears in an old DR's candidate list gets its own
    CandidateMapping with old_dr_ids=[] -- New candidate.
    Two or more old DRs sharing the same single confident new_dr_id are
    merged into one CandidateMapping -- Merge candidate.
    """
    if not old_drs or not new_drs:
        return []
    standard = old_drs[0].standard

    old_by_id = {dr.dr_id: dr for dr in old_drs}
    new_by_id = {dr.dr_id: dr for dr in new_drs}
    old_tokens = {dr_id: _tokenize(dr.dr_title) for dr_id, dr in old_by_id.items()}
    new_tokens = {dr_id: _tokenize(dr.dr_title) for dr_id, dr in new_by_id.items()}
    doc_freq = Counter(t for tokens in (*old_tokens.values(), *new_tokens.values()) for t in tokens)
    n_docs = len(old_tokens) + len(new_tokens)
    weight = {t: math.log((n_docs + 1) / (df + 1)) + 1.0 for t, df in doc_freq.items()}

    per_old_candidates: dict[str, list[_Scored]] = {}
    for old_id, o_tok in old_tokens.items():
        old_dr = old_by_id[old_id]
        requires_financial_effects_anchor = _has_financial_effects_anchor(old_dr.dr_title)
        scored = [
            _Scored(new_id, _weighted_jaccard(o_tok, n_tok, weight))
            for new_id, n_tok in new_tokens.items()
            if not requires_financial_effects_anchor
            or _has_financial_effects_anchor(new_by_id[new_id].dr_title)
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        per_old_candidates[old_id] = [s for s in scored if s.score >= _CANDIDATE_FLOOR][
            :_MAX_CANDIDATES_PER_OLD_DR
        ]

    confident_target: dict[str, list[str]] = {}  # new_dr_id -> [old_dr_ids mapping to it confidently]
    uncertain: list[CandidateMapping] = []
    removed: list[CandidateMapping] = []
    seen_new_ids: set[str] = set()

    for old_id, candidates in per_old_candidates.items():
        if not candidates:
            removed.append(
                CandidateMapping(
                    standard=standard,
                    old_dr_ids=[old_id],
                    new_dr_ids=[],
                    basis="no 2026 title scores above the candidate floor",
                    score=0.0,
                )
            )
            continue
        if len(old_tokens[old_id]) < _MIN_DISTINCTIVE_TOKENS:
            # A boilerplate title (e.g. "Anticipated financial effects from
            # material X-related risks and opportunities" collapses to a
            # single topic word once stopwords are removed) gives a false
            # sense of similarity to every other DR that shares that one
            # word. Title matching has no real signal here -- RC1 must read
            # the authoritative paragraph text directly, not trust a score.
            uncertain.append(
                CandidateMapping(
                    standard=standard,
                    old_dr_ids=[old_id],
                    new_dr_ids=[c.dr_id for c in candidates],
                    basis=(
                        "old DR title too generic for token matching "
                        "(boilerplate wording) -- RC1 must verify against "
                        "full paragraph text, not this score"
                    ),
                    score=0.0,
                )
            )
            seen_new_ids.update(c.dr_id for c in candidates)
            continue

        top = candidates[0]
        second_score = candidates[1].score if len(candidates) > 1 else 0.0
        is_confident = top.score >= _CONFIDENT_SCORE and (top.score - second_score) >= _CONFIDENT_GAP

        if is_confident:
            confident_target.setdefault(top.dr_id, []).append(old_id)
            seen_new_ids.add(top.dr_id)
        else:
            seen_new_ids.update(c.dr_id for c in candidates)
            uncertain.append(
                CandidateMapping(
                    standard=standard,
                    old_dr_ids=[old_id],
                    new_dr_ids=[c.dr_id for c in candidates],
                    basis="ambiguous title match -- no single clearly leading 2026 candidate",
                    score=top.score,
                )
            )

    confident: list[CandidateMapping] = []
    for new_id, old_ids in confident_target.items():
        basis = "title match" if len(old_ids) == 1 else "merge candidate: multiple 2025 DRs match one 2026 DR title"
        confident.append(
            CandidateMapping(
                standard=standard,
                old_dr_ids=old_ids,
                new_dr_ids=[new_id],
                basis=basis,
                score=max(s.score for s in per_old_candidates[old_ids[0]] if s.dr_id == new_id),
            )
        )

    new_only = [
        CandidateMapping(
            standard=standard,
            old_dr_ids=[],
            new_dr_ids=[new_id],
            basis="no 2025 DR title matches this 2026 DR",
            score=0.0,
        )
        for new_id in new_tokens
        if new_id not in seen_new_ids
    ]

    all_candidates = confident + uncertain + removed + new_only
    _attach_hints(all_candidates, old_drs, hints)
    return all_candidates


def _attach_hints(
    candidates: list[CandidateMapping],
    old_drs: list[DisclosureRequirement],
    hints: list[HelperHint],
) -> None:
    """Loosely attach helper-log hints to unambiguous single-old-DR candidates.

    This is a weak, best-effort link (title-token overlap between the hint's
    rationale text and the old DR's own title/paragraph text) -- it is a lead
    for RC1 to look at, never evidence. Ambiguous or multi-DR candidates are
    skipped rather than guessing which DR a hint belongs to.
    """
    if not hints:
        return
    old_by_id = {dr.dr_id: dr for dr in old_drs}
    for candidate in candidates:
        if len(candidate.old_dr_ids) != 1:
            continue
        dr = old_by_id.get(candidate.old_dr_ids[0])
        if dr is None:
            continue
        dr_tokens = _tokenize(dr.dr_title)
        for para in dr.paragraphs[:3]:
            dr_tokens |= _tokenize(para.text[:200])
        for hint in hints:
            hint_tokens = _tokenize(hint.text)
            if dr_tokens & hint_tokens:
                candidate.hints.append(hint)
