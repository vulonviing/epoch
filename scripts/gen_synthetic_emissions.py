"""Generate data/synthetic/dist_ie_energy_emissions_raw.csv.

Produces a synthetic replica of the real emissions table using the exact
20-column schema. Two source blocks:

  A) Energy-derived rows (1:1 from dist_ie_energy_raw.csv)
     scope/halo/emissions_t follow real GHG reporting conventions.
     emissions_t = NULL (only MWh-based energy carriers).

  B) Process-gas rows (~0.44 x energy rows)
     media_type in {Halogenated Carbons, SF6, Nitrous Oxide (N2O),
                    Methane, Technical Carbon Dioxide}
     amount_consumed_mwh = NULL; emissions_t = physical mass; co2e_t via GWP.

Usage:
    python scripts/gen_synthetic_emissions.py

Inputs:
    data/synthetic/dist_ie_energy_raw.csv
    data/synthetic/.master_dims.json

Output:
    data/synthetic/dist_ie_energy_emissions_raw.csv
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
LOAD_DATE = "2026-07-10 08:00:00.000"

# ── Scope mapping ─────────────────────────────────────────────────────────────
SCOPE_MAP: dict[str, str] = {
    "Electricity":                  "Scope 2",
    "District Heating":             "Scope 2",
    "District Cooling":             "Scope 2",
    "Photovoltaics - Own Consumed": "Other",
    # All combustion fuels -> Scope 1
    "Acetylene":                    "Scope 1",
    "Biogas":                       "Scope 1",
    "Diesel":                       "Scope 1",
    "Heating Oil":                  "Scope 1",
    "Hydrogen":                     "Scope 1",
    "Liquid Gas":                   "Scope 1",
    "Natural Gas":                  "Scope 1",
    "Petrol":                       "Scope 1",
    "Wood Pellets":                 "Scope 1",
    # Process gases -> Scope 1
    "Halogenated Carbons":          "Scope 1",
    "SF6 (emitted amount = eM)":    "Scope 1",
    "Nitrous Oxide (N2O)":          "Scope 1",
    "Methane":                      "Scope 1",
    "Technical Carbon Dioxide":     "Scope 1",
}

# ── Process gas catalogue ─────────────────────────────────────────────────────
# GWP (AR5/AR6 approximate), physical emission mass range (tonnes/site/quarter)
GAS_CATALOG = {
    "Halogenated Carbons": {
        "gwp": 1960.0,       # representative for mixed HFC fleet
        "mass_lo": 0.001,
        "mass_hi": 0.5,
        "gwp_noise": 0.10,   # ±10 % within gas family
        "use_halo": True,
    },
    "SF6 (emitted amount = eM)": {
        "gwp": 24300.0,
        "mass_lo": 0.0001,
        "mass_hi": 0.01,
        "gwp_noise": 0.02,
        "use_halo": False,
    },
    "Nitrous Oxide (N2O)": {
        "gwp": 246.0,
        "mass_lo": 0.001,
        "mass_hi": 0.05,
        "gwp_noise": 0.05,
        "use_halo": False,
    },
    "Methane": {
        "gwp": 27.0,
        "mass_lo": 0.01,
        "mass_hi": 1.0,
        "gwp_noise": 0.05,
        "use_halo": False,
    },
    "Technical Carbon Dioxide": {
        "gwp": 1.0,
        "mass_lo": 0.1,
        "mass_hi": 5.0,
        "gwp_noise": 0.0,
        "use_halo": False,
    },
}

# Halogenated Carbons refrigerant name pool
HALO_NAMES = [
    "R134a (CH2FCF3)", "R407F", "R448a", "R449a", "R32",
    "R410a", "R22 (CHClF2)", "R1234yf", "R1234ze", "R290",
    "R143a (C2H3F3)", "R227ea (C3HF7)", "R134 (C2H2F4)",
]

# ── Deterministic helpers ─────────────────────────────────────────────────────

def _det_hex(seed_str: str) -> str:
    return hashlib.md5(seed_str.encode(), usedforsecurity=False).hexdigest()


# ── Section A: energy-derived rows ───────────────────────────────────────────

def build_energy_rows(energy: pd.DataFrame) -> pd.DataFrame:
    """One emissions row per energy row; carry over common fields."""
    col_order = [
        "pk_energy", "report_id", "location_id", "location_name", "active",
        "country", "bu_rc_id", "bu_rc_name", "fiscal_year", "fiscal_quarter",
        "scope", "status", "media_type", "halo_name",
        "amount_consumed_mwh", "emissions_t", "co2_factor", "co2e_t",
        "source_last_updated", "load_date",
    ]

    rows = []
    for _, e in energy.iterrows():
        media = str(e["media_type"])
        scope = SCOPE_MAP.get(media, "Scope 1")
        loc_id = int(e["location_id"])
        yr     = int(e["fiscal_year"])
        qtr    = str(e["fiscal_quarter"])

        pk = _det_hex(f"EMI-A-{loc_id}-{yr}-{qtr}-{media}")

        rows.append({
            "pk_energy":           pk,
            "report_id":           int(e["report_id"]),
            "location_id":         loc_id,
            "location_name":       str(e["location_name"]),
            "active":              bool(e["active"]),
            "country":             str(e["country"]),
            "bu_rc_id":            int(e["bu_rc_id"]),
            "bu_rc_name":          str(e["bu_rc_name"]),
            "fiscal_year":         yr,
            "fiscal_quarter":      qtr,
            "scope":               scope,
            "status":              str(e["status"]),
            "media_type":          media,
            "halo_name":           None,           # always NULL for energy rows
            "amount_consumed_mwh": float(e["amount_consumed_mwh"]),
            "emissions_t":         None,           # NULL for energy rows (real pattern)
            "co2_factor":          float(e["co2_factor"]),
            "co2e_t":              float(e["co2e_t"]),
            "source_last_updated": str(e["source_last_updated"]),
            "load_date":           LOAD_DATE,
        })

    return pd.DataFrame(rows)[col_order]


# ── Section B: process-gas rows ───────────────────────────────────────────────

def build_gas_rows(
    master: list[dict],
    report_id_map: dict[tuple, int],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Add process-gas rows (~0.44 x energy row count), gerçekçi seyreklik."""
    col_order = [
        "pk_energy", "report_id", "location_id", "location_name", "active",
        "country", "bu_rc_id", "bu_rc_name", "fiscal_year", "fiscal_quarter",
        "scope", "status", "media_type", "halo_name",
        "amount_consumed_mwh", "emissions_t", "co2_factor", "co2e_t",
        "source_last_updated", "load_date",
    ]

    years    = [2022, 2023, 2024, 2025, 2026]
    quarters = ["Q1", "Q2", "Q3", "Q4"]

    # Seasonal source_last_updated mirrors energy convention
    quarter_date = {
        "Q1": "{y}-11-01 00:00:00",
        "Q2": "{y}-02-01 00:00:00",
        "Q3": "{y}-05-01 00:00:00",
        "Q4": "{y}-08-01 00:00:00",
    }

    rows = []
    gas_names = list(GAS_CATALOG.keys())

    # Each site gets a random subset of gases (1–3 types, stable across time)
    # We assign gas subsets per site deterministically from RNG state set here.
    site_gases: dict[int, list[str]] = {}
    for site in master:
        lid = site["location_id"]
        n = int(rng.integers(1, 4))   # 1–3 gas types per site
        chosen = list(rng.choice(gas_names, size=min(n, len(gas_names)), replace=False))
        site_gases[lid] = chosen

    # Each (site, gas) gets a base physical mass level (stable trend)
    site_gas_base: dict[tuple, float] = {}
    for site in master:
        lid = site["location_id"]
        for gas in site_gases[lid]:
            cfg = GAS_CATALOG[gas]
            site_gas_base[(lid, gas)] = float(
                rng.uniform(cfg["mass_lo"], cfg["mass_hi"])
            )

    next_report_id = 9000  # distinct namespace from energy

    for site in master:
        lid  = site["location_id"]
        name = site["location_name"]
        iso2 = site["country"]
        bu_id   = site["bu_rc_id"]
        bu_name = site["bu_rc_name"]
        active  = site["active"]
        gases   = site_gases[lid]

        for yr in years:
            cal_yr = yr - 1  # Q1 falls in prior calendar year
            for qtr in quarters:
                cal_y = cal_yr if qtr == "Q1" else yr
                src_ts = quarter_date[qtr].format(y=cal_y)

                rpt_key = (lid, yr, qtr)
                if rpt_key in report_id_map:
                    rpt_id = report_id_map[rpt_key]
                else:
                    rpt_id = next_report_id
                    next_report_id += 1

                for gas in gases:
                    cfg  = GAS_CATALOG[gas]
                    base = site_gas_base[(lid, gas)]

                    # Slight year noise
                    noise = float(rng.uniform(0.85, 1.15))
                    mass  = round(base * noise, 6)
                    mass  = max(cfg["mass_lo"] * 0.01, mass)

                    gwp_noise = float(rng.uniform(1 - cfg["gwp_noise"], 1 + cfg["gwp_noise"]))
                    co2f  = round(cfg["gwp"] * gwp_noise, 4)
                    co2e  = round(mass * co2f, 4)

                    if cfg["use_halo"]:
                        halo = HALO_NAMES[int(rng.integers(0, len(HALO_NAMES)))]
                    else:
                        halo = "NO HALO"

                    # ~5 % In Work
                    status = "In Work" if float(rng.random()) < 0.05 else "Approved"

                    pk = _det_hex(f"EMI-B-{lid}-{yr}-{qtr}-{gas}")

                    rows.append({
                        "pk_energy":           pk,
                        "report_id":           rpt_id,
                        "location_id":         lid,
                        "location_name":       name,
                        "active":              active,
                        "country":             iso2,
                        "bu_rc_id":            bu_id,
                        "bu_rc_name":          bu_name,
                        "fiscal_year":         yr,
                        "fiscal_quarter":      qtr,
                        "scope":               "Scope 1",
                        "status":              status,
                        "media_type":          gas,
                        "halo_name":           halo,
                        "amount_consumed_mwh": None,    # always NULL for gas rows
                        "emissions_t":         mass,
                        "co2_factor":          co2f,
                        "co2e_t":              co2e,
                        "source_last_updated": src_ts,
                        "load_date":           LOAD_DATE,
                    })

    return pd.DataFrame(rows)[col_order]


# ── Verification ──────────────────────────────────────────────────────────────

def verify(df: pd.DataFrame, energy: pd.DataFrame) -> None:
    errors: list[str] = []

    expected_cols = [
        "pk_energy", "report_id", "location_id", "location_name", "active",
        "country", "bu_rc_id", "bu_rc_name", "fiscal_year", "fiscal_quarter",
        "scope", "status", "media_type", "halo_name",
        "amount_consumed_mwh", "emissions_t", "co2_factor", "co2e_t",
        "source_last_updated", "load_date",
    ]
    if list(df.columns) != expected_cols:
        errors.append(f"column mismatch: {list(df.columns)}")

    # pk_energy uniqueness
    if df["pk_energy"].duplicated().any():
        errors.append("duplicate pk_energy values")

    # Scope correctness
    scope2 = {"Electricity", "District Heating", "District Cooling"}
    other  = {"Photovoltaics - Own Consumed"}
    for media, expected_scope in {
        "Electricity": "Scope 2", "Natural Gas": "Scope 1",
        "Photovoltaics - Own Consumed": "Other", "Halogenated Carbons": "Scope 1",
    }.items():
        actual = df[df["media_type"] == media]["scope"].unique()
        if len(actual) > 0 and list(actual) != [expected_scope]:
            errors.append(f"scope mismatch for {media}: {actual}")

    # Formula checks
    energy_rows = df[df["amount_consumed_mwh"].notna() & df["co2_factor"].notna() & (df["amount_consumed_mwh"] != 0)]
    if not energy_rows.empty:
        formula_ok = ((energy_rows["co2e_t"] - energy_rows["amount_consumed_mwh"] * energy_rows["co2_factor"]).abs() < 0.01).all()
        if not formula_ok:
            errors.append("FORMULA ERROR: co2e_t != mwh*co2_factor for energy rows")

    gas_rows = df[df["emissions_t"].notna() & df["co2_factor"].notna() & (df["emissions_t"] != 0)]
    if not gas_rows.empty:
        gas_formula_ok = ((gas_rows["co2e_t"] - gas_rows["emissions_t"] * gas_rows["co2_factor"]).abs() < 0.01).all()
        if not gas_formula_ok:
            errors.append("FORMULA ERROR: co2e_t != emissions_t*co2_factor for gas rows")

    # Null patterns
    energy_mask = df["amount_consumed_mwh"].notna()
    gas_mask    = ~energy_mask

    if energy_mask.any() and df.loc[energy_mask, "emissions_t"].notna().any():
        errors.append("emissions_t should be NULL for all energy rows")

    if gas_mask.any() and df.loc[gas_mask, "amount_consumed_mwh"].notna().any():
        errors.append("amount_consumed_mwh should be NULL for all gas rows")

    # halo_name: only Halogenated Carbons rows have real names
    non_halo = df[(df["media_type"] != "Halogenated Carbons") & df["halo_name"].notna() & (df["halo_name"] != "NO HALO")]
    if not non_halo.empty:
        errors.append(f"unexpected halo_name in non-Halogenated rows ({len(non_halo)})")

    # report_id bridge: all energy-derived rows report_ids present in energy CSV
    energy_rids = set(energy["report_id"].unique())
    emis_energy_rids = set(df.loc[energy_mask, "report_id"].unique())
    missing = emis_energy_rids - energy_rids
    if missing:
        errors.append(f"report_id bridge broken: {len(missing)} ids not in energy CSV")

    # Hacim (gas/energy ratio)
    n_energy_rows = energy_mask.sum()
    n_gas_rows    = gas_mask.sum()
    ratio = n_gas_rows / n_energy_rows if n_energy_rows else 0
    ratio_ok = 0.35 <= ratio <= 0.55

    print("\n" + "=" * 60)
    print("VERIFICATION")
    print(f"  total rows:         {len(df):,}")
    print(f"  energy-derived:     {n_energy_rows:,}")
    print(f"  gas rows:           {n_gas_rows:,}")
    print(f"  gas/energy ratio:   {ratio:.3f}  (target 0.35-0.55) {'OK' if ratio_ok else 'WARN'}")
    print(f"  distinct locations: {df['location_id'].nunique()}")
    print(f"  scope counts:       {df['scope'].value_counts().to_dict()}")
    print(f"  media types:        {df['media_type'].nunique()}")
    print(f"  pk_energy unique:   {not df['pk_energy'].duplicated().any()}")
    print(f"  formula energy OK:  {not any('energy' in e for e in errors)}")
    print(f"  formula gas OK:     {not any('gas' in e for e in errors)}")
    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    ! {e}")
        raise SystemExit(1)
    else:
        print("  all checks PASSED [OK]")
    print("=" * 60)


# ── End-to-end smoke test ─────────────────────────────────────────────────────

def smoke_test_emissions(synth_dir: Path) -> None:
    try:
        import sys
        sys.path.insert(0, str(synth_dir.parent.parent / "src"))
        from epoch_switch.agents.data.d1_loader.d1_data_executor import DataRequestExecutor
    except ImportError:
        print("\n  [smoke test skipped: epoch_switch not importable]")
        return

    exec_ = DataRequestExecutor(data_dir=synth_dir)
    energy_df = exec_._load_energy_frame()
    em_df = exec_._load_emissions_frame(energy_df=energy_df)
    join_cols = ["cdp_region", "bu_rc_group", "city", "latitude"]
    print(f"\n  Smoke test (_load_emissions_frame): {len(em_df)} rows")
    ok = True
    for col in join_cols:
        n = em_df[col].isna().sum() if col in em_df.columns else -1
        status = "OK" if n == 0 else "FAIL"
        if n != 0:
            ok = False
        print(f"    [{status}] {col}: {n} NaN")
    print("  Smoke test PASSED [OK]" if ok else "  Smoke test FAILED")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    repo_root = Path(__file__).parent.parent
    synth_dir = repo_root / "data" / "synthetic"

    energy_path = synth_dir / "dist_ie_energy_raw.csv"
    master_path = synth_dir / ".master_dims.json"
    for p in [energy_path, master_path]:
        if not p.exists():
            raise FileNotFoundError(f"{p} not found — run gen_synthetic_energy.py first.")

    print("Loading inputs ...")
    energy = pd.read_csv(energy_path)
    master: list[dict] = json.loads(master_path.read_text(encoding="utf-8"))
    print(f"  energy rows: {len(energy):,}   sites: {len(master)}")

    # Build report_id bridge from energy CSV
    report_id_map: dict[tuple, int] = {}
    for _, row in energy.iterrows():
        key = (int(row["location_id"]), int(row["fiscal_year"]), str(row["fiscal_quarter"]))
        if key not in report_id_map:
            report_id_map[key] = int(row["report_id"])

    rng = np.random.default_rng(SEED)

    print("Building energy-derived rows (Section A) ...")
    part_a = build_energy_rows(energy)

    print("Building process-gas rows (Section B) ...")
    part_b = build_gas_rows(master, report_id_map, rng)

    df = pd.concat([part_a, part_b], ignore_index=True)
    print(f"  combined: {len(df):,} rows")

    verify(df, energy)

    out_path = synth_dir / "dist_ie_energy_emissions_raw.csv"
    df.to_csv(out_path, index=False)
    kb = out_path.stat().st_size // 1024
    print(f"\n  CSV => {out_path}  ({kb} KB)")

    smoke_test_emissions(synth_dir)
    print("\nDone.")


if __name__ == "__main__":
    main()
