"""Generate data/synthetic/dist_ie_energy_raw.csv.

Produces a fully synthetic replica of the real energy table using the
exact 24-column schema. No real data is read or referenced.

Design decisions (all approved by user):
- 80 sites  ×  5 fiscal years (2022-2026)  ×  4 quarters  ×  ~4-6 media/site
  → ~15 k rows.
- Real country/city names, invented site names + values (coğrafya gerçek, tesis uydurma).
- Deterministic: single numpy RNG with SEED=42; byte-identical on every run.
- UC1 threshold bands (EnEfG §8/§16):
    upper  (~30 %) → 3-yr avg final energy > 7 500 MWh   (§8 + §16 hit)
    middle (~40 %) → 2 500 – 7 500 MWh                   (§16 hit only)
    lower  (~30 %) → < 2 500 MWh                          (none)
- ~5 % of rows have status="In Work"; ~4 % of sites are active=False.
- Also writes data/synthetic/.master_dims.json for downstream tables.

Usage:
    python scripts/gen_synthetic_energy.py

Output:
    data/synthetic/dist_ie_energy_raw.csv
    data/synthetic/.master_dims.json
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

# ── Constants ─────────────────────────────────────────────────────────────────

SEED = 42
N_SITES = 80
YEARS = [2022, 2023, 2024, 2025, 2026]
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
LOAD_DATE = "2026-07-10 08:00:00.000"

# Siemens fiscal year: Oct–Sep.  Q1=Oct-Dec, Q2=Jan-Mar, Q3=Apr-Jun, Q4=Jul-Sep.
# source_last_updated: first day of the quarter's middle month.
QUARTER_DATE = {
    "Q1": "{year}-11-01 00:00:00",
    "Q2": "{year}-02-01 00:00:00",  # shifts to +1 calendar year
    "Q3": "{year}-05-01 00:00:00",
    "Q4": "{year}-08-01 00:00:00",
}


def quarter_date(fiscal_year: int, quarter: str) -> str:
    """Return a deterministic source_last_updated string for a fiscal quarter."""
    # Q1 of FY2023 = Oct 2022 = calendar year 2022
    if quarter == "Q1":
        cal_year = fiscal_year - 1
    else:
        cal_year = fiscal_year
    return QUARTER_DATE[quarter].format(year=cal_year)


# Country pool (real ISO-2, DE-weighted to reflect EnEfG scope)
COUNTRY_POOL = [
    ("DE", "Germany"),
    ("DE", "Germany"),
    ("DE", "Germany"),
    ("DE", "Germany"),
    ("AT", "Austria"),
    ("AT", "Austria"),
    ("CH", "Switzerland"),
    ("FR", "France"),
    ("FR", "France"),
    ("GB", "United Kingdom"),
    ("IT", "Italy"),
    ("ES", "Spain"),
    ("NL", "Netherlands"),
    ("PL", "Poland"),
    ("CZ", "Czech Republic"),
    ("SE", "Sweden"),
    ("NO", "Norway"),
    ("FI", "Finland"),
    ("DK", "Denmark"),
    ("BE", "Belgium"),
    ("PT", "Portugal"),
    ("RO", "Romania"),
    ("HU", "Hungary"),
    ("US", "United States"),
    ("CA", "Canada"),
    ("CN", "China"),
    ("IN", "India"),
    ("TR", "Turkey"),
    ("BR", "Brazil"),
    ("AU", "Australia"),
    ("SG", "Singapore"),
    ("ZA", "South Africa"),
]

# City pool per country (real cities, not tied to real Siemens sites)
CITIES = {
    "DE": ["Berlin", "Munich", "Hamburg", "Frankfurt", "Nuremberg",
           "Stuttgart", "Cologne", "Dresden", "Leipzig", "Erlangen"],
    "AT": ["Vienna", "Graz", "Linz", "Salzburg"],
    "CH": ["Zurich", "Basel", "Bern", "Geneva"],
    "FR": ["Paris", "Lyon", "Toulouse", "Bordeaux", "Strasbourg"],
    "GB": ["London", "Manchester", "Birmingham", "Edinburgh", "Bristol"],
    "IT": ["Milan", "Rome", "Turin", "Bologna"],
    "ES": ["Madrid", "Barcelona", "Valencia", "Seville"],
    "NL": ["Amsterdam", "Rotterdam", "Eindhoven", "Utrecht"],
    "PL": ["Warsaw", "Krakow", "Wroclaw", "Gdansk"],
    "CZ": ["Prague", "Brno", "Ostrava"],
    "SE": ["Stockholm", "Gothenburg", "Malmo"],
    "NO": ["Oslo", "Bergen", "Trondheim"],
    "FI": ["Helsinki", "Tampere", "Oulu"],
    "DK": ["Copenhagen", "Aarhus"],
    "BE": ["Brussels", "Antwerp", "Ghent"],
    "PT": ["Lisbon", "Porto"],
    "RO": ["Bucharest", "Cluj-Napoca"],
    "HU": ["Budapest", "Debrecen"],
    "US": ["Chicago", "Houston", "Atlanta", "Detroit", "Boston"],
    "CA": ["Toronto", "Montreal", "Vancouver"],
    "CN": ["Shanghai", "Beijing", "Shenzhen", "Chengdu"],
    "IN": ["Pune", "Bangalore", "Chennai", "Mumbai"],
    "TR": ["Istanbul", "Ankara", "Izmir"],
    "BR": ["São Paulo", "Curitiba", "Campinas"],
    "AU": ["Melbourne", "Sydney", "Brisbane"],
    "SG": ["Singapore"],
    "ZA": ["Johannesburg", "Cape Town"],
}

# BU pool (~15 distinct BU groups)
BU_POOL = [
    (3001, "SI_D_DI",  "DI"),
    (3002, "SI_D_ED",  "ED"),
    (3003, "SI_D_FS",  "FS"),
    (3004, "SI_D_HC",  "HC"),
    (3005, "SI_D_MO",  "MO"),
    (3006, "SI_D_SI",  "SI"),
    (3007, "SI_D_SW",  "SW"),
    (3008, "SI_D_TI",  "TI"),
    (3009, "SI_D_EV",  "EV"),
    (3010, "SI_D_IN",  "IN"),
    (3011, "SI_D_OP",  "OP"),
    (3012, "SI_D_PM",  "PM"),
    (3013, "SI_D_RE",  "RE"),
    (3014, "SI_D_SS",  "SS"),
    (3015, "SI_D_TR",  "TR"),
]

# Media types available for sites.
# Each entry: (media_type, energy_classification, base_co2_factor, is_renewable_eligible)
# co2_factor in t CO2e / MWh (approximate, public values)
MEDIA_CATALOG = {
    "Electricity":                {"cls": "Secondary Energy", "co2f": 0.30,  "renew": True,  "cost_per_mwh": 150.0},
    "Natural Gas":                {"cls": "Primary Energy",   "co2f": 0.202, "renew": False, "cost_per_mwh": 80.0},
    "Diesel":                     {"cls": "Primary Energy",   "co2f": 0.267, "renew": False, "cost_per_mwh": 120.0},
    "Heating Oil":                {"cls": "Primary Energy",   "co2f": 0.266, "renew": False, "cost_per_mwh": 100.0},
    "District Heating":           {"cls": "Secondary Energy", "co2f": 0.18,  "renew": False, "cost_per_mwh": 90.0},
    "District Cooling":           {"cls": "Secondary Energy", "co2f": 0.10,  "renew": False, "cost_per_mwh": 70.0},
    "Hydrogen":                   {"cls": "Primary Energy",   "co2f": 0.0,   "renew": True,  "cost_per_mwh": 200.0},
    "Biogas":                     {"cls": "Primary Energy",   "co2f": 0.045, "renew": True,  "cost_per_mwh": 95.0},
    "Photovoltaics - Own Consumed": {"cls": "Secondary Energy", "co2f": 0.0, "renew": True,  "cost_per_mwh": 20.0},
    "Wood Pellets":               {"cls": "Primary Energy",   "co2f": 0.025, "renew": True,  "cost_per_mwh": 60.0},
    "Liquid Gas":                 {"cls": "Primary Energy",   "co2f": 0.234, "renew": False, "cost_per_mwh": 110.0},
    "Petrol":                     {"cls": "Primary Energy",   "co2f": 0.249, "renew": False, "cost_per_mwh": 130.0},
    "Acetylene":                  {"cls": "Primary Energy",   "co2f": 0.26,  "renew": False, "cost_per_mwh": 250.0},
}
ALL_MEDIA = list(MEDIA_CATALOG.keys())

# Country-specific electricity emission factors (t CO2e / MWh)
ELEC_CO2_BY_COUNTRY = {
    "DE": 0.38, "AT": 0.12, "CH": 0.03, "FR": 0.06, "GB": 0.23,
    "IT": 0.32, "ES": 0.20, "NL": 0.36, "PL": 0.77, "CZ": 0.43,
    "SE": 0.02, "NO": 0.01, "FI": 0.12, "DK": 0.16, "BE": 0.18,
    "PT": 0.22, "RO": 0.28, "HU": 0.24,
    "US": 0.39, "CA": 0.13, "CN": 0.58, "IN": 0.71, "TR": 0.45,
    "BR": 0.08, "AU": 0.63, "SG": 0.42, "ZA": 0.90,
}
DEFAULT_ELEC_CO2 = 0.35

# Seasonal mwh weights per quarter (Q1=Oct-Dec heaviest for heating)
SEASONAL_WEIGHTS = {"Q1": 1.20, "Q2": 0.95, "Q3": 0.85, "Q4": 1.00}

# Primary remark pool (~94 % rows will get one)
PRIMARY_REMARKS = [
    "Verified by local energy manager.",
    "Extrapolated from previous quarter.",
    "Meter reading confirmed.",
    "Estimate based on floor area.",
    "Data sourced from utility invoice.",
    "Provisional; final invoice pending.",
    "Based on smart meter export.",
    "Reviewed and approved by site controller.",
]
SECONDARY_REMARKS = [
    "Scope 2 allocation applied.",
    "Market-based method used.",
    "Grid factor from national registry.",
    "Supplier guarantee of origin applied.",
]


# ── RNG seed helper ────────────────────────────────────────────────────────────

def _det_uuid(seed_str: str) -> str:
    """Deterministic 32-hex UUID from an arbitrary seed string."""
    return hashlib.md5(seed_str.encode(), usedforsecurity=False).hexdigest()


# ── Site master generation ─────────────────────────────────────────────────────

def build_sites(rng: np.random.Generator) -> list[dict]:
    """Return 80 synthetic site records with stable IDs."""
    sites: list[dict] = []
    country_seq = [COUNTRY_POOL[i % len(COUNTRY_POOL)] for i in range(N_SITES)]
    rng.shuffle(country_seq)  # type: ignore[arg-type]

    for idx, (iso2, _) in enumerate(country_seq):
        city_list = CITIES.get(iso2, ["City"])
        city = city_list[int(rng.integers(0, len(city_list)))]
        name = f"Synthetic Site {idx + 1:03d}"

        bu = BU_POOL[int(rng.integers(0, len(BU_POOL)))]
        sites.append(
            {
                "location_id": idx + 1,
                "location_name": name,
                "country": iso2,
                "city": city,
                "bu_rc_id": bu[0],
                "bu_rc_name": bu[1],
                "bu_rc_group": bu[2],
                "active": True,  # patched below for ~4 % inactive
            }
        )

    # Mark ~4 % of sites inactive (~3 sites)
    n_inactive = max(1, round(N_SITES * 0.04))
    inactive_idx = rng.choice(N_SITES, size=n_inactive, replace=False)
    for i in inactive_idx:
        sites[i]["active"] = False

    return sites


# ── Threshold band assignment ──────────────────────────────────────────────────

def assign_threshold_bands(sites: list[dict], rng: np.random.Generator) -> list[dict]:
    """Assign annual target MWh (total final energy) per site per year.

    Three bands (UC1 §8/§16):
      upper  (30 %) → avg > 7 500 MWh   target range: 8 000 – 40 000
      middle (40 %) → 2 500 – 7 500     target range: 2 600 – 7 400
      lower  (30 %) → < 2 500           target range:   200 – 2 400
    """
    n_upper  = round(N_SITES * 0.30)
    n_middle = round(N_SITES * 0.40)
    # n_lower  = N_SITES - n_upper - n_middle  # noqa: F841

    indices = list(range(N_SITES))
    rng.shuffle(indices)
    upper_idx  = set(indices[:n_upper])
    middle_idx = set(indices[n_upper : n_upper + n_middle])

    for i, site in enumerate(sites):
        if i in upper_idx:
            band = "upper"
            base_mwh = float(rng.uniform(8_000, 40_000))
        elif i in middle_idx:
            band = "middle"
            base_mwh = float(rng.uniform(2_600, 7_400))
        else:
            band = "lower"
            base_mwh = float(rng.uniform(200, 2_400))

        # Slight trend per year: ±2–5 % annual drift
        trend = float(rng.uniform(-0.03, 0.05))
        site["_band"] = band
        site["_annual_mwh"] = {}
        for yr in YEARS:
            noise = float(rng.uniform(-0.04, 0.04))
            yr_val = base_mwh * ((1 + trend) ** (yr - YEARS[0])) * (1 + noise)
            site["_annual_mwh"][yr] = max(50.0, yr_val)

    return sites


# ── Media subset assignment ────────────────────────────────────────────────────

def assign_media(sites: list[dict], rng: np.random.Generator) -> list[dict]:
    """Give each site a realistic subset of 4–6 media types."""
    optional = [m for m in ALL_MEDIA if m not in {"Electricity", "Natural Gas"}]
    for site in sites:
        n_extra = int(rng.integers(2, 5))  # 2–4 optional extras
        extras = list(
            rng.choice(optional, size=min(n_extra, len(optional)), replace=False)
        )
        site["_media"] = ["Electricity", "Natural Gas"] + extras
    return sites


# ── Row builder ───────────────────────────────────────────────────────────────

def build_rows(sites: list[dict], rng: np.random.Generator) -> list[dict]:
    """Expand each site into (year, quarter, media) rows."""
    rows: list[dict] = []
    report_id_map: dict[tuple, int] = {}
    next_report_id = 1000

    for site in sites:
        iso2        = site["country"]
        media_list  = site["_media"]
        annual_mwh  = site["_annual_mwh"]
        loc_id      = site["location_id"]

        # Electricity co2 factor (country-specific)
        elec_co2 = ELEC_CO2_BY_COUNTRY.get(iso2, DEFAULT_ELEC_CO2)

        # Media share weights (electricity dominant, nat gas secondary)
        raw_weights = []
        for m in media_list:
            if m == "Electricity":
                raw_weights.append(0.55)
            elif m == "Natural Gas":
                raw_weights.append(0.25)
            else:
                raw_weights.append(float(rng.uniform(0.02, 0.10)))
        total_w = sum(raw_weights)
        media_shares = [w / total_w for w in raw_weights]

        for yr in YEARS:
            yr_total = annual_mwh[yr]
            # Quarter weights (seasonal)
            q_weights = [SEASONAL_WEIGHTS[q] for q in QUARTERS]
            q_sum = sum(q_weights)
            q_fractions = [w / q_sum for w in q_weights]

            # Stable report_id per (site, year, quarter)
            for qi, qtr in enumerate(QUARTERS):
                rpt_key = (loc_id, yr, qtr)
                if rpt_key not in report_id_map:
                    report_id_map[rpt_key] = next_report_id
                    next_report_id += 1
                report_id = report_id_map[rpt_key]
                q_total_mwh = yr_total * q_fractions[qi]

                for mi, media in enumerate(media_list):
                    cfg       = MEDIA_CATALOG[media]
                    cls_label = cfg["cls"]
                    is_renew  = cfg["renew"]
                    base_cost = cfg["cost_per_mwh"]

                    # co2_factor: electricity uses country factor; others use catalog ± small noise
                    if media == "Electricity":
                        co2f = elec_co2 * float(rng.uniform(0.95, 1.05))
                    else:
                        co2f = cfg["co2f"] * float(rng.uniform(0.90, 1.10))
                    co2f = round(max(0.0, co2f), 4)

                    mwh = q_total_mwh * media_shares[mi] * float(rng.uniform(0.92, 1.08))
                    mwh = round(max(0.0, mwh), 3)

                    # Renewable energy
                    if is_renew and mwh > 0:
                        if media == "Photovoltaics - Own Consumed":
                            renew = mwh
                        elif media in {"Biogas", "Wood Pellets", "Hydrogen"}:
                            renew = mwh
                        else:  # Electricity: partial share
                            renew_share = float(rng.uniform(0.05, 0.40))
                            renew = round(mwh * renew_share, 3)
                    else:
                        renew = 0.0

                    share_renew = round((renew / mwh * 100) if mwh > 0 else 0.0, 6)

                    gj      = round(mwh * 3.6, 3)
                    co2e    = round(mwh * co2f, 4)
                    cost    = round(mwh * base_cost * float(rng.uniform(0.85, 1.15)), 2)

                    # pk_energy: deterministic UUID from (loc, yr, qtr, media)
                    pk_seed = f"LOC{loc_id}-{yr}-{qtr}-{media}"
                    pk_energy = _det_uuid(pk_seed)

                    # Timestamps
                    src_ts = quarter_date(yr, qtr)

                    # Remarks
                    do_primary = float(rng.random()) < 0.94
                    primary_remark = (
                        PRIMARY_REMARKS[int(rng.integers(0, len(PRIMARY_REMARKS)))]
                        if do_primary
                        else None
                    )
                    do_secondary = float(rng.random()) < 0.24
                    secondary_remark = (
                        SECONDARY_REMARKS[int(rng.integers(0, len(SECONDARY_REMARKS)))]
                        if do_secondary
                        else None
                    )

                    # Status: ~5 % In Work
                    status = "In Work" if float(rng.random()) < 0.05 else "Approved"

                    rows.append(
                        {
                            "pk_energy":               pk_energy,
                            "report_id":               report_id,
                            "location_id":             loc_id,
                            "location_name":           site["location_name"],
                            "active":                  site["active"],
                            "country":                 iso2,
                            "bu_rc_id":                site["bu_rc_id"],
                            "bu_rc_name":              site["bu_rc_name"],
                            "fiscal_year":             yr,
                            "fiscal_quarter":          qtr,
                            "status":                  status,
                            "energy_classification":   cls_label,
                            "media_type":              media,
                            "amount_consumed_mwh":     mwh,
                            "renewable_energy":        renew,
                            "share_renewable":         share_renew,
                            "amount_consumed_gj":      gj,
                            "co2_factor":              co2f,
                            "co2e_t":                  co2e,
                            "costs":                   cost,
                            "primary_energy_remark":   primary_remark,
                            "secondary_energy_remark": secondary_remark,
                            "source_last_updated":     src_ts,
                            "load_date":               LOAD_DATE,
                        }
                    )

    return rows


# ── Master dims export ─────────────────────────────────────────────────────────

def export_master_dims(sites: list[dict], out_dir: Path) -> None:
    """Write .master_dims.json for use by downstream synthetic generators."""
    master = []
    for s in sites:
        master.append(
            {
                "location_id": s["location_id"],
                "location_name": s["location_name"],
                "country": s["country"],
                "city": s["city"],
                "bu_rc_id": s["bu_rc_id"],
                "bu_rc_name": s["bu_rc_name"],
                "bu_rc_group": s["bu_rc_group"],
                "active": s["active"],
            }
        )
    with open(out_dir / ".master_dims.json", "w", encoding="utf-8") as f:
        json.dump(master, f, indent=2, ensure_ascii=False)
    print(f"  master dims => {out_dir / '.master_dims.json'}  ({len(master)} sites)")


# ── Verification ──────────────────────────────────────────────────────────────

def verify(df: pd.DataFrame) -> None:
    """Run structural checks and print a concise report."""
    errors = []

    expected_cols = [
        "pk_energy", "report_id", "location_id", "location_name", "active",
        "country", "bu_rc_id", "bu_rc_name", "fiscal_year", "fiscal_quarter",
        "status", "energy_classification", "media_type", "amount_consumed_mwh",
        "renewable_energy", "share_renewable", "amount_consumed_gj", "co2_factor",
        "co2e_t", "costs", "primary_energy_remark", "secondary_energy_remark",
        "source_last_updated", "load_date",
    ]
    if list(df.columns) != expected_cols:
        errors.append(f"COLUMN MISMATCH: {list(df.columns)}")

    # Formula: gj = mwh * 3.6
    gj_ok = ((df["amount_consumed_gj"] - df["amount_consumed_mwh"] * 3.6).abs() < 0.01).all()
    if not gj_ok:
        errors.append("FORMULA ERROR: gj != mwh*3.6")

    # Formula: co2e = mwh * co2_factor (allow small float delta)
    co2_ok = ((df["co2e_t"] - df["amount_consumed_mwh"] * df["co2_factor"]).abs() < 0.01).all()
    if not co2_ok:
        errors.append("FORMULA ERROR: co2e_t != mwh*co2_factor")

    # Threshold bands present
    energy_by_loc_yr = (
        df.groupby(["location_id", "fiscal_year"])["amount_consumed_mwh"]
        .sum()
        .reset_index()
    )
    rolling = (
        energy_by_loc_yr.groupby("location_id")["amount_consumed_mwh"]
        .apply(lambda s: s.rolling(3, min_periods=1).mean().max())
    )
    n_upper  = (rolling > 7_500).sum()
    n_middle = ((rolling >= 2_500) & (rolling <= 7_500)).sum()
    n_lower  = (rolling < 2_500).sum()
    if n_upper == 0 or n_lower == 0:
        errors.append(f"THRESHOLD BAND MISSING: upper={n_upper} middle={n_middle} lower={n_lower}")

    # pk_energy uniqueness
    if df["pk_energy"].duplicated().any():
        errors.append("DUPLICATE pk_energy values")

    print(f"\n{'='*60}")
    print("VERIFICATION")
    print(f"  rows:             {len(df):,}")
    print(f"  distinct sites:   {df['location_id'].nunique()}")
    print(f"  fiscal years:     {sorted(df['fiscal_year'].unique())}")
    print(f"  quarters:         {sorted(df['fiscal_quarter'].unique())}")
    print(f"  media types:      {df['media_type'].nunique()} / {len(MEDIA_CATALOG)}")
    print(f"  status 'In Work': {(df['status']=='In Work').sum()} rows ({(df['status']=='In Work').mean()*100:.1f} %)")
    print(f"  active=False sites: {df[~df['active']]['location_id'].nunique()}")
    print(f"  threshold bands:  upper={n_upper}  middle={n_middle}  lower={n_lower}")
    print(f"  formula gj OK:    {gj_ok}")
    print(f"  formula co2e OK:  {co2_ok}")
    print(f"  pk_energy unique: {not df['pk_energy'].duplicated().any()}")
    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    ✗ {e}")
        raise SystemExit(1)
    else:
        print("  all checks PASSED [OK]")
    print("=" * 60)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    repo_root = Path(__file__).parent.parent
    out_dir   = repo_root / "data" / "synthetic"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Generating synthetic energy data …")
    print(f"  SEED={SEED}  sites={N_SITES}  years={YEARS}")

    rng = np.random.default_rng(SEED)

    sites = build_sites(rng)
    sites = assign_threshold_bands(sites, rng)
    sites = assign_media(sites, rng)

    rows = build_rows(sites, rng)
    df   = pd.DataFrame(rows)

    # Enforce correct column order
    col_order = [
        "pk_energy", "report_id", "location_id", "location_name", "active",
        "country", "bu_rc_id", "bu_rc_name", "fiscal_year", "fiscal_quarter",
        "status", "energy_classification", "media_type", "amount_consumed_mwh",
        "renewable_energy", "share_renewable", "amount_consumed_gj", "co2_factor",
        "co2e_t", "costs", "primary_energy_remark", "secondary_energy_remark",
        "source_last_updated", "load_date",
    ]
    df = df[col_order]

    verify(df)

    out_path = out_dir / "dist_ie_energy_raw.csv"
    df.to_csv(out_path, index=False)
    print(f"\n  CSV => {out_path}  ({out_path.stat().st_size // 1024} KB)")

    export_master_dims(sites, out_dir)
    print("\nDone.")


if __name__ == "__main__":
    main()
