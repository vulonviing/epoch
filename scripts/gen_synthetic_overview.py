"""
gen_synthetic_overview.py
Generate data/synthetic/dist_ie_overview.csv from the energy bridge.

Grain  : one row per (location_id, fiscal_year, fiscal_quarter) = one per report_id
Rows   : ~1 600 (one per energy report_id)
Columns: 15, same order as real table
Seed   : 42 (deterministic, byte-identical across runs)
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SYNTH_DIR = ROOT / "data" / "synthetic"
ENERGY_CSV = SYNTH_DIR / "dist_ie_energy_raw.csv"
MASTER_JSON = SYNTH_DIR / ".master_dims.json"
OUT_CSV = SYNTH_DIR / "dist_ie_overview.csv"

SEED = 42
LOAD_DATE = "2026-07-10 08:00:00.000"

# ---------------------------------------------------------------------------
# Categorical maps
# ---------------------------------------------------------------------------
COUNTRY_CURRENCY: dict[str, str] = {
    "AE": "AED", "AT": "EUR", "AU": "AUD", "BE": "EUR", "BG": "BGN",
    "BR": "BRL", "CA": "CAD", "CH": "CHF", "CN": "CNY", "CZ": "CZK",
    "DE": "EUR", "DK": "DKK", "EE": "EUR", "ES": "EUR", "FI": "EUR",
    "FR": "EUR", "GB": "GBP", "HU": "HUF", "ID": "IDR", "IN": "INR",
    "IT": "EUR", "JP": "JPY", "KR": "KRW", "MX": "MXN", "MY": "MYR",
    "NL": "EUR", "NO": "NOK", "PL": "PLN", "PT": "EUR", "RO": "RON",
    "SA": "SAR", "SE": "SEK", "SG": "SGD", "SK": "EUR", "TH": "THB",
    "TR": "TRY", "TW": "TWD", "UA": "UAH", "US": "USD", "ZA": "ZAR",
}

TYPE_OF_PRODUCTION = [
    "Assembling",
    "Heavy Equipment",
    "Mechanical Production",
    "Office/Administration",
    "Other",
    "Service",
]

REPORT_SCOPE_TEMPLATES = [
    "Site-level environmental report covering all activities.",
    "All environmental data for the site premises.",
    "Covers manufacturing and support operations on-site.",
    "Report scope: full site boundary per ISO 14001.",
    "Includes all energy and emissions from site operations.",
]

REMARK_TEMPLATES = [
    "Auto-generated synthetic remark.",
    "Data verified by site manager.",
    "Partial-year data; annual figure estimated.",
]

# ---------------------------------------------------------------------------
# Deterministic hash-based pick
# ---------------------------------------------------------------------------

def _hash_pick(key: str, items: list) -> str:
    h = int(hashlib.md5(key.encode()).hexdigest(), 16)
    return items[h % len(items)]


def _fmt_date(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S.000")


# ---------------------------------------------------------------------------
# Build bridge from energy CSV
# ---------------------------------------------------------------------------

def build_bridge() -> pd.DataFrame:
    """Return unique (location_id, location_name, fiscal_year, fiscal_quarter,
    report_id) from the energy CSV — one row per report."""
    e = pd.read_csv(ENERGY_CSV)
    bridge = (
        e[["location_id", "location_name", "fiscal_year", "fiscal_quarter", "report_id"]]
        .drop_duplicates(subset=["location_id", "fiscal_year", "fiscal_quarter"])
        .sort_values(["location_id", "fiscal_year", "fiscal_quarter"])
        .reset_index(drop=True)
    )
    return bridge


# ---------------------------------------------------------------------------
# Load master dims for country info
# ---------------------------------------------------------------------------

def load_master() -> dict[int, dict]:
    with open(MASTER_JSON, "r", encoding="utf-8") as f:
        sites = json.load(f)
    return {s["location_id"]: s for s in sites}


# ---------------------------------------------------------------------------
# Date generation helpers
# ---------------------------------------------------------------------------

def _fiscal_year_start(fiscal_year: int) -> datetime:
    """Siemens fiscal year starts Oct 1 of previous calendar year."""
    return datetime(fiscal_year - 1, 10, 1)


def _make_dates(
    rng: np.random.Generator,
    fiscal_year: int,
    fiscal_quarter: str,
    status: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (creation, edition, approval, source_last_updated) strings."""

    fy_start = _fiscal_year_start(fiscal_year)
    # quarter offset: Q1=Oct, Q2=Jan, Q3=Apr, Q4=Jul
    qtr_offset = {"Q1": 0, "Q2": 3, "Q3": 6, "Q4": 9}.get(fiscal_quarter, 0)
    base = fy_start + timedelta(days=qtr_offset * 30)

    # creation: 15–60 days after quarter start
    creation_offset = int(rng.integers(15, 61))
    creation_dt = base + timedelta(days=creation_offset)

    # edition: creation + 0–60 days
    edition_offset = int(rng.integers(0, 61))
    edition_dt = creation_dt + timedelta(days=edition_offset)

    # approval: NULL for In Work; for Approved usually near edition
    if status == "In Work":
        approval_dt = None
    else:
        # ~34 % exactly edition, rest ±30 days
        delta_days = int(rng.integers(-30, 31))
        approval_dt = edition_dt + timedelta(days=delta_days)

    # source_last_updated: edition ± a few days
    src_delta = int(rng.integers(-5, 6))
    src_dt = edition_dt + timedelta(days=src_delta)

    return (
        _fmt_date(creation_dt),
        _fmt_date(edition_dt),
        _fmt_date(approval_dt),
        _fmt_date(src_dt),
    )


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build_overview(bridge: pd.DataFrame, master: dict[int, dict]) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    rows: list[dict] = []

    # Pre-assign per-location stable values
    loc_currency: dict[int, str | None] = {}
    loc_type: dict[int, str | None] = {}
    loc_report_scope_idx: dict[int, int] = {}
    loc_remark_idx: dict[int, int] = {}

    for loc_id, site in master.items():
        country = site.get("country", "DE")
        currency = COUNTRY_CURRENCY.get(country, "EUR")
        loc_currency[loc_id] = currency
        loc_type[loc_id] = _hash_pick(f"type-{loc_id}", TYPE_OF_PRODUCTION)
        loc_report_scope_idx[loc_id] = int(hashlib.md5(f"scope-{loc_id}".encode()).hexdigest(), 16) % len(REPORT_SCOPE_TEMPLATES)
        loc_remark_idx[loc_id] = int(hashlib.md5(f"remark-{loc_id}".encode()).hexdigest(), 16) % len(REMARK_TEMPLATES)

    for _, row in bridge.iterrows():
        loc_id = int(row["location_id"])
        loc_name = str(row["location_name"])
        fy = int(row["fiscal_year"])
        fq = str(row["fiscal_quarter"])
        rid = int(row["report_id"])

        # status: ~98.8 % Approved, ~1.2 % In Work
        status = "In Work" if rng.random() < 0.012 else "Approved"

        # dates
        creation, edition, approval, src_upd = _make_dates(rng, fy, fq, status)

        # currency / type_of_production with small null injection
        currency = loc_currency.get(loc_id, "EUR")
        if rng.random() < 0.022:
            currency = None
        top = loc_type.get(loc_id, "Other")
        if rng.random() < 0.017:
            top = None

        # report_scope: ~96 % null
        if rng.random() < 0.036:
            report_scope = REPORT_SCOPE_TEMPLATES[loc_report_scope_idx[loc_id]]
        else:
            report_scope = None

        # remark: ~98 % null
        if rng.random() < 0.017:
            remark = REMARK_TEMPLATES[loc_remark_idx[loc_id]]
        else:
            remark = None

        # small null injection for dates
        if rng.random() < 0.005:
            creation = None
        if rng.random() < 0.011:
            edition = None
        if rng.random() < 0.011:
            src_upd = None

        rows.append({
            "report_id": rid,
            "location_id": loc_id,
            "location_name": loc_name,
            "fiscal_year": fy,
            "fiscal_quarter": fq,
            "status": status,
            "creation_date": creation,
            "edition_date": edition,
            "approval_date": approval,
            "currency": currency,
            "type_of_production": top,
            "report_scope": report_scope,
            "remark": remark,
            "source_last_updated": src_upd,
            "load_date": LOAD_DATE,
        })

    df = pd.DataFrame(rows)
    return df


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

EXPECTED_COLS = [
    "report_id", "location_id", "location_name", "fiscal_year", "fiscal_quarter",
    "status", "creation_date", "edition_date", "approval_date", "currency",
    "type_of_production", "report_scope", "remark", "source_last_updated", "load_date",
]


def verify(df: pd.DataFrame, energy_csv: Path) -> None:
    e = pd.read_csv(energy_csv)
    energy_ids = set(e["report_id"].unique())
    overview_ids = set(df["report_id"].unique())

    errors: list[str] = []

    # 1. columns
    if list(df.columns) != EXPECTED_COLS:
        errors.append(f"Column mismatch. Got: {list(df.columns)}")

    # 2. bridge: exact overlap
    if overview_ids != energy_ids:
        extra = overview_ids - energy_ids
        missing = energy_ids - overview_ids
        errors.append(f"report_id bridge off: extra={len(extra)} missing={len(missing)}")

    # 3. grain: report_id unique
    if df["report_id"].duplicated().any():
        errors.append("report_id not unique")

    # 4. date logic: creation <= edition (where both non-null)
    tmp = df.copy()
    for c in ["creation_date", "edition_date"]:
        tmp[c] = pd.to_datetime(tmp[c], errors="coerce")
    mask = tmp["creation_date"].notna() & tmp["edition_date"].notna()
    if not (tmp.loc[mask, "creation_date"] <= tmp.loc[mask, "edition_date"]).all():
        errors.append("creation > edition in some rows")

    # 5. In Work => approval NULL
    inwork = df[df["status"] == "In Work"]
    if inwork["approval_date"].notna().any():
        errors.append("In Work rows with non-null approval_date")

    # 6. categorical consistency per location
    for col in ["currency", "type_of_production"]:
        cnt = df.dropna(subset=[col]).groupby("location_id")[col].nunique()
        bad = cnt[cnt > 1]
        if len(bad):
            errors.append(f"{col} varies within location: {bad.index.tolist()[:5]}")

    # 7. null rates
    rscope_null = df["report_scope"].isna().mean()
    if not (0.90 <= rscope_null <= 1.0):
        errors.append(f"report_scope null rate {rscope_null:.3f} outside expected 0.90-1.0")
    remark_null = df["remark"].isna().mean()
    if not (0.90 <= remark_null <= 1.0):
        errors.append(f"remark null rate {remark_null:.3f} outside expected 0.90-1.0")

    if errors:
        for e_msg in errors:
            print(f"  FAIL: {e_msg}")
        raise AssertionError("Verification failed.")

    print(f"  rows          : {len(df)}")
    print(f"  report_id     : {df['report_id'].min()} => {df['report_id'].max()}")
    print(f"  status        : {dict(df['status'].value_counts())}")
    print(f"  report_scope null% : {rscope_null:.1%}")
    print(f"  remark null%       : {remark_null:.1%}")
    print(f"  currency null%     : {df['currency'].isna().mean():.1%}")
    print(f"  type null%         : {df['type_of_production'].isna().mean():.1%}")
    print("  [OK] all checks passed")


# ---------------------------------------------------------------------------
# Determinism check
# ---------------------------------------------------------------------------

def determinism_check(df: pd.DataFrame) -> str:
    md5 = hashlib.md5(df.to_csv(index=False).encode("utf-8")).hexdigest()
    return md5


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("gen_synthetic_overview.py => dist_ie_overview.csv")

    print("  loading energy bridge ...")
    bridge = build_bridge()
    print(f"  bridge rows: {len(bridge)}")

    print("  loading master dims ...")
    master = load_master()

    print("  building overview ...")
    df = build_overview(bridge, master)

    print("  verifying ...")
    verify(df, ENERGY_CSV)

    # determinism
    md5_1 = determinism_check(df)
    df2 = build_overview(bridge, master)
    md5_2 = determinism_check(df2)
    assert md5_1 == md5_2, "Determinism check failed"
    print(f"  md5: {md5_1}  [OK] deterministic")

    # write
    df.to_csv(OUT_CSV, index=False)
    print(f"  written => {OUT_CSV}")
    print("Done.")


if __name__ == "__main__":
    main()
