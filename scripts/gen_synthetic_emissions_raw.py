"""
gen_synthetic_emissions_raw.py
Generate data/synthetic/dist_ie_emissions_raw.csv.

Pure Scope 1 process-gas table — 26 columns, same order as real.
All 5 media types (Halogenated Carbons + 4 Kyoto gases).
Grain  : quarterly (Q1-Q4), 2022-2026
Rows   : ~9 000 (~5.7 rows/report, 1600 reports)
Seed   : 42 (deterministic, byte-identical across runs)
report_id: shared with energy bridge (energy max 2599)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SYNTH_DIR = ROOT / "data" / "synthetic"
ENERGY_CSV = SYNTH_DIR / "dist_ie_energy_raw.csv"
MASTER_JSON = SYNTH_DIR / ".master_dims.json"
OUT_CSV = SYNTH_DIR / "dist_ie_emissions_raw.csv"

SEED = 42
LOAD_DATE = "2026-07-10 08:00:00.000"

YEARS = [2022, 2023, 2024, 2025, 2026]
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]

# Seasonal source_last_updated base dates (fiscal-year convention)
QUARTER_SRC_DATE = {
    "Q1": "{fy_prev}-11-01 00:00:00.000",  # Q1 starts Oct prior year
    "Q2": "{fy}-02-01 00:00:00.000",
    "Q3": "{fy}-05-01 00:00:00.000",
    "Q4": "{fy}-08-01 00:00:00.000",
}

# ---------------------------------------------------------------------------
# Refrigerant catalogue: (name, gwp, odp_factor, subtype)
# ODP values from UNEP/EPA; GWP from AR5.
# ---------------------------------------------------------------------------
class Refrigerant(NamedTuple):
    name: str
    gwp: float
    odp: float       # ODP (R11 equivalent) factor
    subtype: str     # HFC / HCFC / CFC / HC / HFO


REFRIGERANTS: list[Refrigerant] = [
    Refrigerant("R410A",           2088,  0.0,   "HFC"),
    Refrigerant("R134a (CH2FCF3)", 1430,  0.0,   "HFC"),
    Refrigerant("R407C",           1774,  0.0,   "HFC"),
    Refrigerant("R404A",           3922,  0.0,   "HFC"),
    Refrigerant("R32 (CH2F2)",      675,  0.0,   "HFC"),
    Refrigerant("R32",              675,  0.0,   "HFC"),
    Refrigerant("R23 (CHF3)",     14800,  0.0,   "HFC"),
    Refrigerant("R290",               3,  0.0,   "HC"),
    Refrigerant("R1234yf",            4,  0.0,   "HFO"),
    Refrigerant("R22 (CHClF2)",    1810,  0.055, "HCFC"),  # ODP nonzero
    Refrigerant("R123",              79,  0.02,  "HCFC"),  # ODP nonzero
    Refrigerant("R11",             4750,  1.0,   "CFC"),   # ODP=1 (reference)
]

# ---------------------------------------------------------------------------
# Kyoto gas catalogue: (media_type, gwp, mass_lo, mass_hi)
# ---------------------------------------------------------------------------
class KyotoGas(NamedTuple):
    media: str
    gwp: float
    mass_lo: float
    mass_hi: float


KYOTO_GASES: list[KyotoGas] = [
    KyotoGas("SF6 (emitted amount = eM)",  24300.0, 0.0001, 0.01),
    KyotoGas("Nitrous Oxide (N2O)",          273.0, 0.001,  0.05),
    KyotoGas("Methane",                       27.0, 0.01,   1.0),
    KyotoGas("Technical Carbon Dioxide",       1.0, 0.1,    5.0),
]

# Mass range for Halogenated Carbons (tonnes physical)
HALO_MASS_LO = 0.0001
HALO_MASS_HI = 0.3

KYOTO_REMARK_TEMPLATES = [
    "Routine annual measurement; values within expected range.",
    "Data sourced from metered measurements at site level.",
    "Estimation based on activity data and IPCC emission factors.",
]

HALO_REMARK_TEMPLATES = [
    "Annual refrigerant top-up recorded; leak-check performed.",
    "Refrigerant charge calculated from equipment log.",
    "Minor leak detected and repaired during maintenance.",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _md5(key: str) -> str:
    return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()


def _md5_int(key: str) -> int:
    return int(_md5(key), 16)


def load_master() -> list[dict]:
    with open(MASTER_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def build_bridge(energy: pd.DataFrame) -> dict[tuple[int, int, str], int]:
    """Return (location_id, fiscal_year, fiscal_quarter) -> report_id."""
    bridge: dict[tuple[int, int, str], int] = {}
    for _, row in energy.iterrows():
        key = (int(row["location_id"]), int(row["fiscal_year"]), str(row["fiscal_quarter"]))
        if key not in bridge:
            bridge[key] = int(row["report_id"])
    return bridge


def _src_date(fiscal_year: int, quarter: str, rng: np.random.Generator) -> str:
    fy_prev = fiscal_year - 1
    tmpl = QUARTER_SRC_DATE[quarter]
    base = tmpl.format(fy=fiscal_year, fy_prev=fy_prev)
    # jitter ±15 days
    jitter = int(rng.integers(-15, 16))
    from datetime import datetime, timedelta
    dt = datetime.strptime(base[:10], "%Y-%m-%d") + timedelta(days=jitter)
    return dt.strftime("%Y-%m-%d") + base[10:]


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build_emissions(
    master: list[dict],
    energy: pd.DataFrame,
    bridge: dict[tuple[int, int, str], int],
) -> pd.DataFrame:

    rng = np.random.default_rng(SEED)

    # Per-site: assign a stable Refrigerant for Halogenated Carbons rows
    # Some sites get 2 refrigerants (heavier sites)
    site_refrig: dict[int, list[Refrigerant]] = {}
    for site in sorted(master, key=lambda s: s["location_id"]):
        lid = site["location_id"]
        n_halo = 1 + (_md5_int(f"nhalo-{lid}") % 2)  # 1 or 2
        all_idx = list(range(len(REFRIGERANTS)))
        rng_loc = np.random.default_rng(_md5_int(f"halo-{lid}") % (2**32))
        rng_loc.shuffle(all_idx)
        site_refrig[lid] = [REFRIGERANTS[i] for i in all_idx[:n_halo]]

    # Per-site per Kyoto gas: stable base mass
    site_gas_base: dict[tuple[int, str], float] = {}
    for site in sorted(master, key=lambda s: s["location_id"]):
        lid = site["location_id"]
        for kg in KYOTO_GASES:
            h = _md5_int(f"kbase-{lid}-{kg.media}")
            frac = (h % 10000) / 10000.0
            site_gas_base[(lid, kg.media)] = kg.mass_lo + frac * (kg.mass_hi - kg.mass_lo)

    # Build location->energy dim lookup (first occurrence per loc)
    energy_loc: dict[int, dict] = {}
    for _, row in energy.iterrows():
        lid = int(row["location_id"])
        if lid not in energy_loc:
            energy_loc[lid] = row.to_dict()

    rows: list[dict] = []
    row_counter = 0  # for pk_emissions indexing

    for site in sorted(master, key=lambda s: s["location_id"]):
        lid      = site["location_id"]
        loc_name = site["location_name"]
        country  = site.get("country", "DE")
        bu_rc_id   = site["bu_rc_id"]
        bu_rc_name = site["bu_rc_name"]
        active   = bool(site.get("active", True))
        halo_refs = site_refrig[lid]

        for year in YEARS:
            for qtr in QUARTERS:
                bridge_key = (lid, year, qtr)
                if bridge_key not in bridge:
                    continue  # only emit rows for known reports
                report_id = bridge[bridge_key]

                # status: ~95 % Approved
                status = "In Work" if rng.random() < 0.05 else "Approved"

                # source_last_updated
                src_base = _src_date(year, qtr, rng)

                # --- Kyoto gas rows (4 per report) ---
                for kg in KYOTO_GASES:
                    base_mass = site_gas_base[(lid, kg.media)]
                    noise     = float(rng.uniform(0.85, 1.15))
                    mass      = round(base_mass * noise, 6)
                    mass      = max(kg.mass_lo * 0.01, mass)

                    gwp_noise = float(rng.uniform(0.97, 1.03))
                    co2f      = round(kg.gwp * gwp_noise, 4)
                    co2e      = round(mass * co2f, 6)

                    # pk_emissions: ~27.7 % null
                    pk = None if rng.random() < 0.277 else _md5(f"EMRAW-K-{lid}-{year}-{qtr}-{kg.media}-{row_counter}")

                    # halo_name: "NO HALO" ~23 % of Kyoto rows; else null (-> ~54.6 % null total)
                    halo_name = "NO HALO" if rng.random() < 0.23 else None

                    # halos_subtype: null for all Kyoto rows
                    # refrigerant: null for Kyoto
                    # kyoto_gases_remark: halo rows are always null; Kyoto rows ~25.1 % filled.
                    kyoto_remark = None
                    if rng.random() < 0.251:
                        idx = _md5_int(f"kr-{lid}-{kg.media}") % len(KYOTO_REMARK_TEMPLATES)
                        kyoto_remark = KYOTO_REMARK_TEMPLATES[idx]

                    # halogenated_carbons_remark: ~31.5 % filled in Kyoto rows
                    halo_remark_kyoto = None
                    if rng.random() < 0.315:
                        idx = _md5_int(f"hrc-{lid}-{kg.media}") % len(HALO_REMARK_TEMPLATES)
                        halo_remark_kyoto = HALO_REMARK_TEMPLATES[idx]

                    # country: ~0.9 % null
                    row_country = None if rng.random() < 0.009 else country
                    # source_last_updated: ~1.2 % null
                    row_src = None if rng.random() < 0.012 else src_base

                    rows.append({
                        "pk_emissions":               pk,
                        "report_id":                  report_id,
                        "location_id":                lid,
                        "location_name":              loc_name,
                        "active":                     active,
                        "country":                    row_country,
                        "bu_rc_id":                   bu_rc_id,
                        "bu_rc_name":                 bu_rc_name,
                        "fiscal_year":                year,
                        "fiscal_quarter":             qtr,
                        "status":                     status,
                        "scope":                      "Scope 1",
                        "media_type":                 kg.media,
                        "halo_name":                  halo_name,
                        "halos_subtype":              None,
                        "emission_type":              "Kyoto Gases",
                        "emissions_t":                mass,
                        "co2_factor":                 co2f,
                        "co2e_t":                     co2e,
                        "r11e_t":                     0.0,
                        "r11_factor":                 0.0,
                        "refrigerant":                None,
                        "kyoto_gases_remark":         kyoto_remark,
                        "halogenated_carbons_remark": halo_remark_kyoto,
                        "source_last_updated":        row_src,
                        "load_date":                  LOAD_DATE,
                    })
                    row_counter += 1

                # --- Halogenated Carbons rows (1-2 per report, one per refrigerant) ---
                for refrig in halo_refs:
                    base_mass = float(
                        HALO_MASS_LO
                        + (_md5_int(f"hmass-{lid}-{refrig.name}") % 10000) / 10000.0
                        * (HALO_MASS_HI - HALO_MASS_LO)
                    )
                    noise = float(rng.uniform(0.85, 1.15))
                    mass  = round(base_mass * noise, 6)
                    mass  = max(HALO_MASS_LO * 0.01, mass)

                    gwp_noise = float(rng.uniform(0.97, 1.03))
                    co2f      = round(refrig.gwp * gwp_noise, 2)
                    co2e      = round(mass * co2f, 6)
                    r11f      = refrig.odp
                    r11e      = round(mass * r11f, 8)

                    # pk_emissions: ~27.7 % null
                    pk = None if rng.random() < 0.277 else _md5(f"EMRAW-H-{lid}-{year}-{qtr}-{refrig.name}-{row_counter}")

                    # halos_subtype: ~82.4 % of halo rows filled
                    halos_sub = refrig.subtype if rng.random() < 0.824 else None

                    # refrigerant: "True" ~93 %, "False" ~7 %
                    refrigerant_val = "True" if rng.random() < 0.93 else "False"

                    # halogenated_carbons_remark: ~76.9 % filled
                    halo_remark = None
                    if rng.random() < 0.769:
                        idx = _md5_int(f"hr-{lid}-{refrig.name}") % len(HALO_REMARK_TEMPLATES)
                        halo_remark = HALO_REMARK_TEMPLATES[idx]

                    row_country = None if rng.random() < 0.009 else country
                    row_src     = None if rng.random() < 0.012 else src_base

                    rows.append({
                        "pk_emissions":               pk,
                        "report_id":                  report_id,
                        "location_id":                lid,
                        "location_name":              loc_name,
                        "active":                     active,
                        "country":                    row_country,
                        "bu_rc_id":                   bu_rc_id,
                        "bu_rc_name":                 bu_rc_name,
                        "fiscal_year":                year,
                        "fiscal_quarter":             qtr,
                        "status":                     status,
                        "scope":                      "Scope 1",
                        "media_type":                 "Halogenated Carbons",
                        "halo_name":                  refrig.name,
                        "halos_subtype":              halos_sub,
                        "emission_type":              "Halogenated Carbons",
                        "emissions_t":                mass,
                        "co2_factor":                 co2f,
                        "co2e_t":                     co2e,
                        "r11e_t":                     r11e,
                        "r11_factor":                 r11f,
                        "refrigerant":                refrigerant_val,
                        "kyoto_gases_remark":         None,
                        "halogenated_carbons_remark": halo_remark,
                        "source_last_updated":        row_src,
                        "load_date":                  LOAD_DATE,
                    })
                    row_counter += 1

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

EXPECTED_COLS = [
    "pk_emissions", "report_id", "location_id", "location_name", "active",
    "country", "bu_rc_id", "bu_rc_name", "fiscal_year", "fiscal_quarter",
    "status", "scope", "media_type", "halo_name", "halos_subtype",
    "emission_type", "emissions_t", "co2_factor", "co2e_t", "r11e_t",
    "r11_factor", "refrigerant", "kyoto_gases_remark",
    "halogenated_carbons_remark", "source_last_updated", "load_date",
]

MEDIA_TYPES = {
    "Halogenated Carbons",
    "SF6 (emitted amount = eM)",
    "Nitrous Oxide (N2O)",
    "Methane",
    "Technical Carbon Dioxide",
}


def verify(df: pd.DataFrame, energy: pd.DataFrame) -> None:
    errors: list[str] = []

    # 1. columns
    if list(df.columns) != EXPECTED_COLS:
        errors.append(f"Column mismatch. Got: {list(df.columns)}")

    # 2. scope: all Scope 1
    if not (df["scope"] == "Scope 1").all():
        errors.append("scope contains values other than Scope 1")

    # 3. media_type
    bad = set(df["media_type"].unique()) - MEDIA_TYPES
    if bad:
        errors.append(f"unexpected media_type: {bad}")

    # 4. emission_type
    halo_mask = df["media_type"] == "Halogenated Carbons"
    if not (df.loc[halo_mask, "emission_type"] == "Halogenated Carbons").all():
        errors.append("Halogenated Carbons rows have wrong emission_type")
    if not (df.loc[~halo_mask, "emission_type"] == "Kyoto Gases").all():
        errors.append("Kyoto rows have wrong emission_type")

    # 5. formula: co2e_t == emissions_t * co2_factor
    tmp = df.copy()
    computed_co2e = (tmp["emissions_t"] * tmp["co2_factor"]).round(4)
    actual_co2e   = tmp["co2e_t"].round(4)
    bad_co2e = (computed_co2e - actual_co2e).abs() > 0.01
    if bad_co2e.any():
        errors.append(f"co2e_t formula error in {bad_co2e.sum()} rows")

    # 6. r11 formula
    computed_r11e = (tmp["emissions_t"] * tmp["r11_factor"]).round(6)
    bad_r11 = (computed_r11e - tmp["r11e_t"]).abs() > 1e-5
    if bad_r11.any():
        errors.append(f"r11e_t formula error in {bad_r11.sum()} rows")

    # 7. r11_factor nonzero only for halo rows with ODP refrigerants
    kyoto_r11 = df.loc[~halo_mask, "r11_factor"]
    if (kyoto_r11 != 0).any():
        errors.append(f"r11_factor nonzero in Kyoto rows ({(kyoto_r11!=0).sum()})")

    # 8. refrigerant: null in non-halo rows
    non_halo_refrig = df.loc[~halo_mask, "refrigerant"]
    if non_halo_refrig.notna().any():
        errors.append(f"refrigerant non-null in {non_halo_refrig.notna().sum()} Kyoto rows")

    # 9. report_id bridge
    energy_ids = set(energy["report_id"].unique())
    emis_ids   = set(df["report_id"].unique())
    missing    = emis_ids - energy_ids
    if missing:
        errors.append(f"report_id bridge broken: {len(missing)} ids not in energy CSV")

    # 10. pk_emissions: non-null values unique
    non_null_pk = df["pk_emissions"].dropna()
    if non_null_pk.duplicated().any():
        errors.append("pk_emissions has duplicates among non-null values")

    if errors:
        for msg in errors:
            print(f"  FAIL: {msg}")
        raise AssertionError("Verification failed.")

    # Summary
    n_halo   = halo_mask.sum()
    n_kyoto  = (~halo_mask).sum()
    n_report = df["report_id"].nunique()
    print(f"  rows                 : {len(df)}")
    print(f"  reports covered      : {n_report}  (~{len(df)/n_report:.1f} rows/report)")
    print(f"  Kyoto rows           : {n_kyoto}  Halo rows: {n_halo}")
    print(f"  media_type counts    : {dict(df['media_type'].value_counts())}")
    print(f"  scope                : {dict(df['scope'].value_counts())}")
    print(f"  pk_emissions null%   : {df['pk_emissions'].isna().mean():.1%}")
    print(f"  halo_name null%      : {df['halo_name'].isna().mean():.1%}")
    print(f"  halos_subtype null%  : {df['halos_subtype'].isna().mean():.1%}")
    print(f"  refrigerant null%    : {df['refrigerant'].isna().mean():.1%}")
    print(f"  kyoto_remark null%   : {df['kyoto_gases_remark'].isna().mean():.1%}")
    print(f"  halo_remark null%    : {df['halogenated_carbons_remark'].isna().mean():.1%}")
    r11_nonzero = (df["r11_factor"] != 0).sum()
    print(f"  r11_factor nonzero   : {r11_nonzero} rows")
    print("  [OK] all checks passed")


def determinism_check(df: pd.DataFrame) -> str:
    return hashlib.md5(df.to_csv(index=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("gen_synthetic_emissions_raw.py => dist_ie_emissions_raw.csv")

    print("  loading energy CSV and master ...")
    energy = pd.read_csv(ENERGY_CSV)
    master = load_master()
    print(f"  energy rows: {len(energy)}  sites: {len(master)}")

    print("  building report_id bridge ...")
    bridge = build_bridge(energy)
    print(f"  bridge entries: {len(bridge)}")

    print("  building emissions rows ...")
    df = build_emissions(master, energy, bridge)

    print("  verifying ...")
    verify(df, energy)

    # determinism
    md5_1 = determinism_check(df)
    df2   = build_emissions(master, energy, bridge)
    md5_2 = determinism_check(df2)
    assert md5_1 == md5_2, "Determinism check failed"
    print(f"  md5: {md5_1}  [OK] deterministic")

    df.to_csv(OUT_CSV, index=False)
    print(f"  written => {OUT_CSV}")
    print("Done.")


if __name__ == "__main__":
    main()
