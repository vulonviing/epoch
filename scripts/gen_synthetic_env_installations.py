"""
gen_synthetic_env_installations.py
Generate data/synthetic/dist_ie_env_installations_raw.csv.

Grain      : annual (fiscal_quarter='Y'), one report_id per (location, year)
Years      : 2022-2026
Rows       : ~1500-2500 (1-8 installations per site, fixed across years)
Columns    : 21, same order as real table
Seed       : 42 (deterministic, byte-identical across runs)
report_id  : own namespace starting at 3000 (above energy 1000-2599)
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SYNTH_DIR = ROOT / "data" / "synthetic"
MASTER_JSON = SYNTH_DIR / ".master_dims.json"
OUT_CSV = SYNTH_DIR / "dist_ie_env_installations_raw.csv"

SEED = 42
LOAD_DATE = "2026-07-10 08:00:00.000"
REPORT_ID_START = 3000  # above energy max 2599
ENV_INST_ID_START = 8562  # matches real range start

# ---------------------------------------------------------------------------
# Country -> currency (reused from overview)
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

# ---------------------------------------------------------------------------
# Installation catalog
# Each entry: (name, impact_environment, relevance, legal_norm_or_None)
# All names are generic; legal_norm codes are public German/international law.
# ---------------------------------------------------------------------------
CATALOG: list[tuple[str, str, str, str | None]] = [
    # Waste water
    ("Sewage treatment plant",         "Waste water treatment plants", "Subject to authorization", "WHG"),
    ("Fat separator",                  "Waste water treatment plants", "Environmental relevant",   "WHG"),
    ("Oil-water separator",            "Waste water treatment plants", "Notifiable",               "WHG"),
    ("Biological treatment unit",      "Waste water treatment plants", "Subject to authorization", "WHG-VawS"),
    # Cooling / HCFC
    ("Cooling unit HCFC",              "Cooling installation with HCFC/CFC", "Subject to authorization", "31. BImSchV"),
    ("HVAC unit",                      "Cooling installation with HCFC/CFC", "Environmental relevant",   None),
    ("Chiller",                        "Cooling installation with HCFC/CFC", "Notifiable",               "42. BImSchV"),
    # Air polluting
    ("Diesel generator",               "Air polluting installations", "Notifiable",               "4. BImSchV"),
    ("Air compressor",                 "Air polluting installations", "Environmental relevant",   "BImSchV"),
    ("Fuel burning boiler",            "Air polluting installations", "Subject to authorization", "BImSchV"),
    ("Selective soldering unit",       "Air polluting installations", "Notifiable",               "clean air law"),
    ("Nitrogen tank",                  "Air polluting installations", "Environmental relevant",   None),
    # Water-polluting substances
    ("Hazardous substance storage",    "Plants for handling with waterpolluting substances", "Subject to authorization", "AwSV"),
    ("Underground storage tank",       "Plants for handling with waterpolluting substances", "Notifiable",               "AwSV"),
    ("Chemical storage area",          "Plants for handling with waterpolluting substances", "Environmental relevant",   "AwSV"),
    ("Battery storage room",           "Plants for handling with waterpolluting substances", "Environmental relevant",   None),
    # Waste treatment
    ("Waste sorting facility",         "Waste treatment plants and own landfill", "Subject to authorization", "BetrSichV"),
    ("Hazardous waste storage",        "Waste treatment plants and own landfill", "Notifiable",               "BetrSichV"),
    # Other
    ("Emergency power unit",           "Other plants",               "Notifiable",               "BetrSichV"),
    ("CHP unit",                       "Other plants",               "Subject to authorization", "BImSchV"),
    ("Transformer station",            "Other plants",               "Environmental relevant",   None),
    ("Compressed gas cylinder store",  "Other plants",               "Notifiable",               "AwSV"),
]

REMARK_TEMPLATES = [
    "Routine inspection completed; no non-conformities found.",
    "Annual review: installation within permitted limits.",
    "Minor maintenance scheduled for next quarter.",
    "Documentation updated following last audit.",
    "Permit renewal pending; operations continue under existing permit.",
]

YEARS = [2022, 2023, 2024, 2025, 2026]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _md5_int(key: str) -> int:
    return int(hashlib.md5(key.encode()).hexdigest(), 16)


def _fmt_date(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S.000")


def load_master() -> list[dict]:
    with open(MASTER_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build_installations(master: list[dict]) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)

    # Sort sites for deterministic report_id assignment
    sites_sorted = sorted(master, key=lambda s: s["location_id"])

    # Assign per-site installation set (1-8 catalog entries, fixed across years)
    site_installs: dict[int, list[int]] = {}
    for site in sites_sorted:
        loc_id = site["location_id"]
        n_inst = 1 + (_md5_int(f"ninst-{loc_id}") % 8)  # 1..8
        # pick n_inst distinct catalog entries deterministically
        indices = list(range(len(CATALOG)))
        # shuffle with loc-specific seed
        rng_loc = np.random.default_rng(_md5_int(f"cat-{loc_id}") % (2**32))
        rng_loc.shuffle(indices)
        site_installs[loc_id] = indices[:n_inst]

    # Assign report_id per (loc, year) in sorted order
    report_id_map: dict[tuple[int, int], int] = {}
    rid = REPORT_ID_START
    for site in sites_sorted:
        for year in YEARS:
            report_id_map[(site["location_id"], year)] = rid
            rid += 1

    # Build rows
    rows: list[dict] = []
    env_inst_id_counter = ENV_INST_ID_START

    for site in sites_sorted:
        loc_id = site["location_id"]
        country = site.get("country", "DE")
        currency = COUNTRY_CURRENCY.get(country, "EUR")
        bu_rc_id = site["bu_rc_id"]
        bu_rc_name = site["bu_rc_name"]
        master_active = site.get("active", True)

        catalog_indices = site_installs[loc_id]

        for year in YEARS:
            report_id = report_id_map[(loc_id, year)]

            # status
            status = "In Work" if rng.random() < 0.04 else "Approved"
            # active: mostly from master; ~7 % override to False
            active = bool(master_active) and (rng.random() >= 0.07)

            # source_last_updated: a day within the fiscal year
            fy_start = datetime(year - 1, 10, 1)  # Oct 1 prev year
            src_day_offset = int(rng.integers(30, 330))
            src_dt = fy_start + timedelta(days=src_day_offset)

            for cat_idx in catalog_indices:
                name, impact, relevance, legal_norm_base = CATALOG[cat_idx]

                # env_installation_id: per-row unique float; ~6.6 % null
                if rng.random() < 0.066:
                    env_inst_id = None
                else:
                    env_inst_id = float(env_inst_id_counter)
                env_inst_id_counter += 1

                # name + number_installations: together null ~24.5 %
                if rng.random() < 0.245:
                    row_name = None
                    n_inst_val = None
                else:
                    row_name = name
                    # skewed: mostly 1-3, rarely up to 50
                    raw = rng.exponential(scale=1.5)
                    n_inst_val = float(max(1, min(50, int(raw) + 1)))

                # impact + relevance: together null ~6.7 %
                if rng.random() < 0.067:
                    row_impact = None
                    row_relevance = None
                else:
                    row_impact = impact
                    row_relevance = relevance

                # legal_norm: target overall null ~44.3 %.
                # Catalog has 4/22 entries with legal_norm=None (always null).
                # For entries with a legal_norm value, nullify at 31.9 % to hit target.
                if legal_norm_base is None:
                    row_legal = None
                else:
                    row_legal = None if rng.random() < 0.319 else legal_norm_base

                # remark: ~66 % null
                if rng.random() < 0.34:
                    row_remark = REMARK_TEMPLATES[_md5_int(f"rem-{loc_id}-{cat_idx}") % len(REMARK_TEMPLATES)]
                else:
                    row_remark = None

                # country: ~1.1 % null
                row_country = None if rng.random() < 0.011 else country
                # currency: ~0.4 % null
                row_currency = None if rng.random() < 0.004 else currency
                # source_last_updated: ~0.9 % null
                row_src = None if rng.random() < 0.009 else _fmt_date(src_dt)
                # load_date: ~6.6 % null
                row_load = None if rng.random() < 0.066 else LOAD_DATE

                rows.append({
                    "report_id": report_id,
                    "location_id": loc_id,
                    "location_name": site["location_name"],
                    "active": active,
                    "country": row_country,
                    "bu_rc_id": bu_rc_id,
                    "bu_rc_name": bu_rc_name,
                    "fiscal_year": year,
                    "fiscal_quarter": "Y",
                    "status": status,
                    "currency": row_currency,
                    "env_installation_id": env_inst_id,
                    "name": row_name,
                    "number_installations": n_inst_val,
                    "impact_environment": row_impact,
                    "relevance": row_relevance,
                    "legal_norm": row_legal,
                    "remark": row_remark,
                    "env_installations_remark": None,  # always null
                    "source_last_updated": row_src,
                    "load_date": row_load,
                })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

EXPECTED_COLS = [
    "report_id", "location_id", "location_name", "active", "country",
    "bu_rc_id", "bu_rc_name", "fiscal_year", "fiscal_quarter", "status",
    "currency", "env_installation_id", "name", "number_installations",
    "impact_environment", "relevance", "legal_norm", "remark",
    "env_installations_remark", "source_last_updated", "load_date",
]

IMPACT_VALS = {
    "Waste water treatment plants",
    "Air polluting installations",
    "Plants for handling with waterpolluting substances",
    "Other plants",
    "Cooling installation with HCFC/CFC",
    "Waste treatment plants and own landfill",
}
RELEVANCE_VALS = {"Environmental relevant", "Subject to authorization", "Notifiable"}


def verify(df: pd.DataFrame) -> None:
    errors: list[str] = []

    # 1. columns
    if list(df.columns) != EXPECTED_COLS:
        errors.append(f"Column mismatch. Got: {list(df.columns)}")

    # 2. fiscal_quarter all 'Y'
    if not (df["fiscal_quarter"] == "Y").all():
        errors.append("fiscal_quarter contains values other than 'Y'")

    # 3. years
    bad_years = set(df["fiscal_year"].unique()) - set(YEARS)
    if bad_years:
        errors.append(f"Unexpected fiscal_years: {bad_years}")

    # 4. grain: one report_id per (loc, year)
    g = df.groupby(["location_id", "fiscal_year"])["report_id"].nunique()
    if (g > 1).any():
        errors.append("Multiple report_ids per (location, year)")

    # 5. report_id namespace clear of energy (1000-2599)
    if df["report_id"].min() < REPORT_ID_START:
        errors.append(f"report_id below 3000: {df['report_id'].min()}")

    # 6. enum check
    bad_impact = df["impact_environment"].dropna()[~df["impact_environment"].dropna().isin(IMPACT_VALS)]
    if len(bad_impact):
        errors.append(f"Invalid impact_environment values: {bad_impact.unique().tolist()[:3]}")
    bad_rel = df["relevance"].dropna()[~df["relevance"].dropna().isin(RELEVANCE_VALS)]
    if len(bad_rel):
        errors.append(f"Invalid relevance values: {bad_rel.unique().tolist()[:3]}")

    # 7. null correlation: name <-> number_installations (together null)
    name_null = df["name"].isna()
    num_null = df["number_installations"].isna()
    mismatch = (name_null != num_null).mean()
    if mismatch > 0.02:
        errors.append(f"name/number_installations null mismatch rate {mismatch:.3f} > 0.02")

    # 8. null correlation: impact <-> relevance
    imp_null = df["impact_environment"].isna()
    rel_null = df["relevance"].isna()
    mismatch2 = (imp_null != rel_null).mean()
    if mismatch2 > 0.02:
        errors.append(f"impact/relevance null mismatch rate {mismatch2:.3f} > 0.02")

    # 9. env_installations_remark: 100 % null
    if df["env_installations_remark"].notna().any():
        errors.append("env_installations_remark has non-null values")

    # 10. env_installation_id: non-null values unique
    non_null_ids = df["env_installation_id"].dropna()
    if non_null_ids.duplicated().any():
        errors.append("env_installation_id has duplicates among non-null values")

    # 11. dim consistency per location
    for col in ["country", "bu_rc_id", "bu_rc_name"]:
        cnt = df.dropna(subset=[col]).groupby("location_id")[col].nunique()
        bad = cnt[cnt > 1]
        if len(bad):
            errors.append(f"{col} varies within location: {bad.index.tolist()[:3]}")

    if errors:
        for msg in errors:
            print(f"  FAIL: {msg}")
        raise AssertionError("Verification failed.")

    # Summary
    print(f"  rows                    : {len(df)}")
    print(f"  report_id               : {df['report_id'].min()} => {df['report_id'].max()}")
    print(f"  distinct (loc,year)     : {df.groupby(['location_id','fiscal_year']).ngroups}")
    print(f"  status                  : {dict(df['status'].value_counts())}")
    print(f"  name null%              : {df['name'].isna().mean():.1%}")
    print(f"  impact null%            : {df['impact_environment'].isna().mean():.1%}")
    print(f"  legal_norm null%        : {df['legal_norm'].isna().mean():.1%}")
    print(f"  remark null%            : {df['remark'].isna().mean():.1%}")
    print(f"  env_inst_id null%       : {df['env_installation_id'].isna().mean():.1%}")
    print(f"  env_inst_remark null%   : {df['env_installations_remark'].isna().mean():.1%}")
    print("  [OK] all checks passed")


# ---------------------------------------------------------------------------
# Determinism check
# ---------------------------------------------------------------------------

def determinism_check(df: pd.DataFrame) -> str:
    return hashlib.md5(df.to_csv(index=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("gen_synthetic_env_installations.py => dist_ie_env_installations_raw.csv")

    print("  loading master dims ...")
    master = load_master()
    print(f"  sites: {len(master)}")

    print("  building installations ...")
    df = build_installations(master)

    print("  verifying ...")
    verify(df)

    # determinism
    md5_1 = determinism_check(df)
    df2 = build_installations(master)
    md5_2 = determinism_check(df2)
    assert md5_1 == md5_2, "Determinism check failed"
    print(f"  md5: {md5_1}  [OK] deterministic")

    # write
    df.to_csv(OUT_CSV, index=False)
    print(f"  written => {OUT_CSV}")
    print("Done.")


if __name__ == "__main__":
    main()
