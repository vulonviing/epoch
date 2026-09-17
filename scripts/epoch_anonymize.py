"""Anonymization for the public EPOCH evaluation disclosure.

Only the four tabular use cases (UC1, UC2, UC2.1, UC3) are exported. Their
private source packages can contain site names, addresses, coordinates, and
site-level measurements. UC4 is not processed or exported because its package
contains third-party source excerpts without an explicit redistribution grant.

For UC1/UC2/UC2.1/UC3 it masks:
  - Site identity, geography, organisational values, measurements, timestamps,
    local paths, people, and private identifiers.
  - Site-level measurement figures: `gap`, `avg_3yr`, `subtotal_t`,
    `portfolio_total_t`, and any key ending in `_mwh`/`_kwh`/`_gwh`/`_tco2e`
    become `MASK`. Regulatory constants (`threshold`) are left alone --
    see the module docstring.
  - Every free-text value is scanned for literal and pattern-based leaks.
  - Published checkpoint/source payloads are reduced separately to a one-row
    structure-only disclosure by ``mask_payload_shape``. This final allow-nothing
    layer prevents translated or inferred geography from surviving in prose.
  - Person names everywhere (`approved_by`, `reviewer`, `decided_by`,
    `reviewed_by`, `user`): masked in every use case, tabular or not.
  - Machine-local source paths (`_source_path`, `source_location`): masked in
    every use case because they can expose usernames or employee identifiers.

Everything is masked to the literal string `MASK` -- consistently, so the
independent post-build grep has one term to look for regardless of which
rule caught it.
"""
from __future__ import annotations

import re
from typing import Any

MASK = "****"
PUBLIC_SOURCE_SYSTEM = "private source system"
_INTERNAL_SOURCE_SYSTEM_RE = re.compile(r"\bSESIS\b", re.IGNORECASE)

# Registry ids whose data actually needs masking. UC4 is public-only and is
# never passed through the tabular rules below.
TABULAR_REGISTRY_IDS = {
    "uc1_enefg_threshold_check",
    "uc2_ets1_scope_memo",
    "uc2_1_ets1_scope_memo_annual",
    "uc3_csrd_scope2_measure",
}

# Keys whose value is a site identity, replaced with a stable per-registry
# token so grouping/labelling still works in the UI.
SITE_TOKEN_KEYS = {"location_name"}
SITE_ID_TOKEN_KEYS = {"location_id"}

# Public Siemens business-unit/division codes -- the exact set registry.py's
# own `site_filter.bu_rc` lists use (see registry.py:327,399,462). UC3
# aggregates some rows at this grain rather than physical-site grain, so
# these values legitimately appear under `location_name` too; masking them
# would discard public information named in the thesis. The final public-gold
# structure-only layer nevertheless masks these values inside run payloads.
PUBLIC_BUSINESS_UNITS = {"DI", "SMO", "SI", "SRE", "Advanta", "CDO"}

# Keys whose value is a site identity but carries no useful grouping role --
# masked outright. `country_name`/`country_code` are deliberately excluded:
# a country name is public information (the thesis itself says "German
# Siemens sites"), and DA1's own catalog filter lists reuse full country
# names and "RC <country>" regional groupings as legitimate public values,
# which collided constantly with a country-name denylist in testing.
IDENTITY_MASK_KEYS = {
    "address",
    "city",
    "zip_code",
    "latitude",
    "longitude",
}

# Keys whose value is a site-level measurement figure. Regulatory constants
# (`threshold`) are deliberately excluded -- see module docstring.
NUMERIC_MASK_KEYS = {"gap", "avg_3yr", "subtotal_t", "portfolio_total_t"}
NUMERIC_MASK_KEY_SUFFIXES = ("_mwh", "_kwh", "_gwh", "_tco2e", "_t")

# Numbers calculated from, or profiling, the private tabular source.  Public
# regulatory constants and package-structure fields (threshold, page,
# pipeline_order, schema_version, review_round, expected_count, tolerance) are
# intentionally not included.
DATA_DERIVED_NUMERIC_KEYS = {
    "abs_delta",
    "available_fiscal_years",
    "bu_rc_id",
    "evidence_completeness",
    "fiscal_year",
    "method_a",
    "method_b",
    "method_figure",
    "near_breach_ratio",
    "pct_diff",
    "portfolio_total_t_vs_deterministic",
    "size_bytes",
    "total_rows",
    "value",
    "values",
    "Approved",
}

# Keys whose value is a person's name, masked in every use case.
PERSON_KEYS = {"approved_by", "reviewer", "decided_by", "reviewed_by", "user"}

# Paths copied from local shelf records and gold manifests. These are never
# meaningful to a public reader and can contain a Windows GID or local username.
LOCAL_PATH_KEYS = {"_source_path", "source_location"}

# Private shelf/run identifiers are replaced with package-local public tokens.
# Exact timestamps have no disclosure value and are masked outright.
ARTIFACT_ID_KEYS = {"artifact_id", "source_artifact_id"}
RUN_ID_KEYS = {"run_id", "source_run_id", "carried_from_run_id", "source_run_ids"}
TIMESTAMP_KEYS = {
    "approved_at",
    "archived_at",
    "created_at",
    "frozen_at",
    "updated_at",
}

# The source system's own dimension tables reuse the same string space for business/
# region codes ("RC-DE DI") and for real physical site names -- a value can
# legitimately be both. Fields in this set are business-unit/region catalog
# codes, never free narrative, and are exempt from denylist scanning: they
# are the class of data this build deliberately keeps public (see
# PUBLIC_BUSINESS_UNITS above), so a coincidental string match against some
# other row's real site name is not a leak through this field.
SAFE_CODE_KEYS = {"bu_rc", "bu_rc_group", "bu_rc_name", "bu_rc_id", "bu_rc_code"}

# Free-text fields scanned for embedded identity/measurement leaks.
PROSE_KEYS = {
    "interpretation",
    "carried_caveats",
    "rationale",
    "summary",
    "finding_text",
    "narrative",
    "entry_text",
    "provenance_note",
    "headline",
    # Short but composite: builds a display string out of exactly the
    # identity fields masked elsewhere ("Germany / Europe / 12345 Musterstadt"),
    # so it must go through the same scrub regardless of its length.
    "location_display",
}
PROSE_MIN_LEN = 80

_UNIT_NUMBER_RE = re.compile(
    r"[\d][\d,]*\.?\d*\s*(?:GWh|MWh|kWh|tCO₂e|tCO2e|tonnes?|tons?|t)\b",
    re.IGNORECASE,
)
_COORD_RE = re.compile(r"-?\d{1,3}\.\d{3,}")
_GERMAN_POSTAL_CITY_RE = re.compile(r"\b\d{5}\s+[A-ZÄÖÜ][\w\-.\s]{1,40}")
_PERCENT_RE = re.compile(
    r"(?<![\w.])~?\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+(?:[.,]\d+)?)?\s*%"
    r"(?![0-9A-Fa-f])"
)
_COUNT_RE = re.compile(
    r"(?<![\w.])\d+(?:[.,]\d+)?(?:\s*[-/]?\s*[A-Za-z]+){0,3}\s+"
    r"(?:sites?|rows?|records?|entries|divisions?|locations?|installations?|"
    r"compliant|obligated|flagged)\b",
    re.IGNORECASE,
)
_NUMBER_OF_NUMBER_RE = re.compile(r"(?<![\w.-])\d+\s+of\s+\d+\b", re.IGNORECASE)
_NUMBER_WORD_RE = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)\b",
    re.IGNORECASE,
)
_LOCATION_SITE_RE = re.compile(
    r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüßÀ-ÿ'-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüßÀ-ÿ'-]+){0,2}"
    r"\s+(?:site|facility|installation)\b"
)
_DECIMAL_RE = re.compile(r"(?<![\w.-])-?\d+[.,]\d+(?![\w.-])")
_SCIENTIFIC_RE = re.compile(r"(?<![\w.-])-?\d+(?:\.\d+)?e[+-]?\d+(?![\w.-])", re.IGNORECASE)
_LARGE_NUMBER_RE = re.compile(r"(?<![\w.-])\d{3,}(?:,\d{3})*(?![\w.-])")
_SITE_PAREN_LOCATION_RE = re.compile(r"\b(site|facility|installation)\s*\([^)]+\)", re.IGNORECASE)
_BU_LOCATION_RE = re.compile(
    r"\b(DI|SMO|SI|SRE|Advanta|CDO)\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüßÀ-ÿ'-]+\b"
)
# ISO-8601 timestamps (created_at/approved_at/... on every shelf record)
# contain fractional seconds that otherwise match _COORD_RE by accident
# (e.g. "05.676402" inside "16:52:05.676402+00:00"). Recognised and skipped
# by the coordinate/postal checks, never by the denylist or unit-number
# checks, which cannot false-positive on a timestamp.
_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _is_prose(key: str, value: str) -> bool:
    if key in SAFE_CODE_KEYS:
        return False
    return True


def _mask_large_number(match: re.Match[str]) -> str:
    raw = match.group(0).replace(",", "")
    if raw.isdigit() and 1900 <= int(raw) <= 2099:
        return match.group(0)
    return MASK


def _scrub_prose(text: str, denylist: list[str]) -> str:
    for term in denylist:
        if term and term in text:
            text = text.replace(term, MASK)
    text = _UNIT_NUMBER_RE.sub(MASK, text)
    text = _PERCENT_RE.sub(MASK, text)
    text = _COUNT_RE.sub(MASK, text)
    text = _NUMBER_OF_NUMBER_RE.sub(MASK, text)
    text = _NUMBER_WORD_RE.sub(MASK, text)
    text = _LOCATION_SITE_RE.sub(MASK, text)
    text = _SITE_PAREN_LOCATION_RE.sub(r"\1 (****)", text)
    text = _BU_LOCATION_RE.sub(r"\1 ****", text)
    text = _SCIENTIFIC_RE.sub(MASK, text)
    text = _DECIMAL_RE.sub(MASK, text)
    text = _LARGE_NUMBER_RE.sub(_mask_large_number, text)
    if not _ISO_DATETIME_RE.match(text):
        text = _GERMAN_POSTAL_CITY_RE.sub(MASK, text)
        text = _COORD_RE.sub(MASK, text)
    return text


def _is_numeric_masked_key(key: str) -> bool:
    if key in NUMERIC_MASK_KEYS or key in DATA_DERIVED_NUMERIC_KEYS:
        return True
    lowered = key.lower()
    if lowered.startswith("n_"):
        return True
    if lowered.endswith("_count") and lowered not in {"checkpoint_count", "expected_count"}:
        return True
    return any(lowered.endswith(suffix) for suffix in NUMERIC_MASK_KEY_SUFFIXES)


class SiteTokenizer:
    """Deterministic, stable location_id/location_name -> site-NN mapping.

    One instance per registry_id (built fresh per use case) so tokens are
    consistent across every shelf and page for that use case, but carry no
    relation to the real id -- the index is sequential over the sorted set
    of real values seen, not derived from them.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, str] = {}
        self._by_id: dict[str, str] = {}
        self._next = 1

    def _assign(self, key: str, table: dict[str, str]) -> str:
        if key not in table:
            table[key] = f"site-{self._next:02d}"
            self._next += 1
        return table[key]

    def token_for_name(self, name: str) -> str:
        return self._assign(name, self._by_name)

    def token_for_id(self, loc_id: str) -> str:
        return self._assign(loc_id, self._by_id)


class MetadataTokenizer:
    """Stable package-local aliases for private artifact and run identifiers."""

    def __init__(self) -> None:
        self._artifacts: dict[str, str] = {}
        self._runs: dict[str, str] = {}

    @staticmethod
    def _assign(value: str, table: dict[str, str], prefix: str, width: int) -> str:
        if value not in table:
            table[value] = f"{prefix}-{len(table) + 1:0{width}d}"
        return table[value]

    def artifact(self, value: str) -> str:
        return self._assign(value, self._artifacts, "artifact", 3)

    def run(self, value: str) -> str:
        return self._assign(value, self._runs, "run", 2)


# Below this length, a value is a code (division "DI", "SI", "SMO"...) far
# more likely to collide with legitimate public text elsewhere than to be a
# meaningfully identifying site/address string on its own. Real site names,
# street addresses, cities, and 5-digit postal codes are all comfortably
# above this bar.
MIN_DENYLIST_TERM_LEN = 5


def collect_denylist(value: Any, into: set[str]) -> None:
    """Walk a payload collecting literal identity/prose values worth
    scanning for elsewhere (the denylist). Called only on tabular payloads.
    Mirrors anonymize_value's SITE_TOKEN_KEYS/IDENTITY_MASK_KEYS rules
    exactly: unconditional except for PUBLIC_BUSINESS_UNITS."""
    if isinstance(value, dict):
        for key, sub in value.items():
            eligible = key in IDENTITY_MASK_KEYS or key in SITE_TOKEN_KEYS or key == "location_city"
            if eligible:
                candidates = sub if isinstance(sub, list) else [sub]
                for candidate in candidates:
                    if not isinstance(candidate, str):
                        continue
                    stripped = candidate.strip()
                    if len(stripped) >= MIN_DENYLIST_TERM_LEN and stripped not in PUBLIC_BUSINESS_UNITS:
                        into.add(stripped)
            collect_denylist(sub, into)
    elif isinstance(value, list):
        for item in value:
            collect_denylist(item, into)


def anonymize_value(
    key: str | None,
    value: Any,
    *,
    tabular: bool,
    tokenizer: SiteTokenizer,
    metadata_tokenizer: MetadataTokenizer,
    denylist: list[str],
) -> Any:
    if isinstance(value, dict):
        return {
            k: anonymize_value(
                k,
                v,
                tabular=tabular,
                tokenizer=tokenizer,
                metadata_tokenizer=metadata_tokenizer,
                denylist=denylist,
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            anonymize_value(
                key,
                v,
                tabular=tabular,
                tokenizer=tokenizer,
                metadata_tokenizer=metadata_tokenizer,
                denylist=denylist,
            )
            for v in value
        ]

    if key in PERSON_KEYS and isinstance(value, str) and value.strip():
        return MASK
    if key in LOCAL_PATH_KEYS and isinstance(value, str) and value.strip():
        return MASK
    if key in TIMESTAMP_KEYS and isinstance(value, str) and value.strip():
        return MASK
    if key in ARTIFACT_ID_KEYS and isinstance(value, str) and value.strip():
        return metadata_tokenizer.artifact(value)
    if key in RUN_ID_KEYS and isinstance(value, str) and value.strip():
        return metadata_tokenizer.run(value)

    if isinstance(value, str):
        value = _INTERNAL_SOURCE_SYSTEM_RE.sub(PUBLIC_SOURCE_SYSTEM, value)

    if not tabular:
        return value

    # location_name/location_id are tokenized unconditionally when tabular:
    # The source system's own "locations" dimension mixes genuine physical sites with
    # business/region pseudo-locations ("RC-DE SI", "RC-FR SI")
    # in the same table and the same field, with no reliable way to tell
    # the two apart from the shelf data alone (confirmed by inspection --
    # see git history for the RealSiteRegistry approach this replaced,
    # which tried to key off a sibling "address" field and kept
    # misclassifying real region-grain rows as sites and vice versa). The
    # one deliberate carve-out is PUBLIC_BUSINESS_UNITS -- the handful of
    # division codes named in the thesis text itself.
    if key in SITE_TOKEN_KEYS and isinstance(value, str) and value.strip():
        stripped = value.strip()
        if stripped in PUBLIC_BUSINESS_UNITS:
            return value
        return tokenizer.token_for_name(stripped)
    if key in SITE_ID_TOKEN_KEYS and isinstance(value, (str, int)) and str(value).strip():
        return tokenizer.token_for_id(str(value).strip())
    if key == "location_city" and isinstance(value, str) and value.strip():
        return MASK
    if key in IDENTITY_MASK_KEYS and isinstance(value, str) and value.strip():
        return MASK
    if (
        key
        and _is_numeric_masked_key(key)
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return MASK

    if isinstance(value, str) and key and _is_prose(key, value):
        return _scrub_prose(value, denylist)

    return value


def anonymize_payload(payload: Any, registry_id: str, denylist: list[str]) -> Any:
    """Anonymize one JSON-safe payload (a shelf envelope, an active-set dict,
    a registry-source string wrapper, ...) for the given registry_id. Pass
    the *whole* active set in one call when possible (see
    build_epoch_static.py) so one SiteTokenizer assigns consistent site-NN
    tokens across every agent shelf in that use case."""
    tabular = registry_id in TABULAR_REGISTRY_IDS
    tokenizer = SiteTokenizer()
    metadata_tokenizer = MetadataTokenizer()
    return anonymize_value(
        None,
        payload,
        tabular=tabular,
        tokenizer=tokenizer,
        metadata_tokenizer=metadata_tokenizer,
        denylist=denylist,
    )


def anonymize_text(text: str, denylist: list[str]) -> str:
    """Anonymize a raw text blob (e.g. registry.py source) against a
    denylist built from every tabular use case's shelves."""
    return _scrub_prose(text, denylist)


class LeakFound(RuntimeError):
    """Raised by verify_no_leaks when a denylisted term survives the build."""


def mask_payload_shape(value: Any) -> Any:
    """Preserve JSON keys and one exemplar per list while masking every value.

    Public gold is a structural disclosure, not a pseudonymized copy of a real
    run. Collapsing lists prevents site/row cardinality from becoming a side
    channel; masking every scalar prevents LLM prose from reintroducing a city,
    address, division, or measurement that was not present in a dedicated field.
    """
    if isinstance(value, dict):
        return {key: mask_payload_shape(child) for key, child in value.items()}
    if isinstance(value, list):
        return [] if not value else [mask_payload_shape(value[0])]
    if value is None:
        return None
    return MASK


def verify_no_leaks(
    tree: Any,
    denylist: list[str],
    *,
    path: str = "$",
    key: str | None = None,
    check_patterns: bool = True,
) -> None:
    """Recursively re-scan an anonymized structure. Raises LeakFound naming
    the offending path on the first survivor -- nothing is published on a
    failed scan. Skips SAFE_CODE_KEYS -- see that set's docstring for why a
    coincidental string match there is not a leak. `check_patterns=False`
    disables the coordinate/postal/unit-number regex checks (but not the
    denylist check) -- use it for genuinely public source text (UC4's
    regulation provisions), which is full of legal-citation dates and
    ratios that collide with those tabular-only heuristics."""
    if key in SAFE_CODE_KEYS:
        return
    if isinstance(tree, dict):
        for k, v in tree.items():
            verify_no_leaks(v, denylist, path=f"{path}.{k}", key=k, check_patterns=check_patterns)
        return
    if isinstance(tree, list):
        for i, v in enumerate(tree):
            verify_no_leaks(v, denylist, path=f"{path}[{i}]", key=key, check_patterns=check_patterns)
        return
    if isinstance(tree, str):
        for term in denylist:
            if term and term in tree:
                raise LeakFound(f"denylisted term {term!r} survived at {path}: {tree!r}")
        # The unit-number/postal/coordinate patterns are only meaningful
        # within the same "prose" scope _scrub_prose actually cleans (see
        # _is_prose) -- schema/description text such as R1's
        # `expected_unit: "MWh/yr (thresholds stated as 7.5 and 2.5 GWh)"`
        # legitimately states a public regulatory constant in a short,
        # non-prose field, and was never a candidate for scrubbing in the
        # first place, so it must not be a candidate for the leak check
        # either -- the two need one shared definition of "in scope".
        if not check_patterns or not _is_prose(key or "", tree):
            return
        if _UNIT_NUMBER_RE.search(tree):
            raise LeakFound(f"unmasked measurement figure survived at {path}: {tree!r}")
        if not _ISO_DATETIME_RE.match(tree):
            if _GERMAN_POSTAL_CITY_RE.search(tree):
                raise LeakFound(f"unmasked postal address survived at {path}: {tree!r}")
            if _COORD_RE.search(tree):
                raise LeakFound(f"unmasked coordinate survived at {path}: {tree!r}")
