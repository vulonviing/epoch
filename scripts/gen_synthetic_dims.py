"""Generate three synthetic dimension CSVs from .master_dims.json.

Produces:
    data/synthetic/regions.csv
    data/synthetic/bu_rc_groups.csv
    data/synthetic/dist_ie_locations.csv

All three are join-compatible with data/synthetic/dist_ie_energy_raw.csv.
Exact column order matches the real source files.

Usage:
    python scripts/gen_synthetic_dims.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42

# ── Region lookup (master's 27 ISO2 countries) ────────────────────────────────
# cdp_region values: Europe | Americas | Asia Australia | Africa
# emea rule in d1_data_executor.py: cdp_region in {Europe, Africa}
#   OR country in {AE, SA, TR}  — TR is mapped to Europe here so emea=True.

REGION_MAP: dict[str, tuple[str, str]] = {
    # Europe
    "AT": ("Europe",         "Austria"),
    "BE": ("Europe",         "Belgium"),
    "CH": ("Europe",         "Switzerland"),
    "CZ": ("Europe",         "Czech Republic"),
    "DE": ("Europe",         "Germany"),
    "DK": ("Europe",         "Denmark"),
    "ES": ("Europe",         "Spain"),
    "FI": ("Europe",         "Finland"),
    "FR": ("Europe",         "France"),
    "GB": ("Europe",         "United Kingdom"),
    "HU": ("Europe",         "Hungary"),
    "IT": ("Europe",         "Italy"),
    "NL": ("Europe",         "Netherlands"),
    "NO": ("Europe",         "Norway"),
    "PL": ("Europe",         "Poland"),
    "PT": ("Europe",         "Portugal"),
    "RO": ("Europe",         "Romania"),
    "SE": ("Europe",         "Sweden"),
    "TR": ("Europe",         "Turkey"),          # AE/SA/TR special-cased in emea rule
    # Americas
    "BR": ("Americas",       "Brazil"),
    "CA": ("Americas",       "Canada"),
    "US": ("Americas",       "United States"),
    # Asia Australia
    "AU": ("Asia Australia", "Australia"),
    "CN": ("Asia Australia", "China"),
    "IN": ("Asia Australia", "India"),
    "SG": ("Asia Australia", "Singapore"),
    # Africa
    "ZA": ("Africa",         "South Africa"),
}

# ── City centre coordinates (real, public) ───────────────────────────────────
# Used in dist_ie_locations.csv with a small jitter per site.
CITY_COORDS: dict[str, tuple[float, float]] = {
    # DE
    "Berlin":     (52.520, 13.405),
    "Munich":     (48.137, 11.575),
    "Hamburg":    (53.551, 9.993),
    "Frankfurt":  (50.110, 8.682),
    "Nuremberg":  (49.453, 11.077),
    "Stuttgart":  (48.775, 9.182),
    "Cologne":    (50.938, 6.960),
    "Dresden":    (51.050, 13.737),
    "Leipzig":    (51.340, 12.375),
    "Erlangen":   (49.598, 11.004),
    # AT
    "Vienna":     (48.208, 16.373),
    "Graz":       (47.070, 15.439),
    "Linz":       (48.306, 14.286),
    "Salzburg":   (47.800, 13.045),
    # CH
    "Zurich":     (47.376, 8.541),
    "Basel":      (47.558, 7.587),
    "Bern":       (46.948, 7.448),
    "Geneva":     (46.204, 6.143),
    # FR
    "Paris":      (48.857, 2.347),
    "Lyon":       (45.748, 4.847),
    "Toulouse":   (43.605, 1.444),
    "Bordeaux":   (44.841, -0.580),
    "Strasbourg": (48.574, 7.752),
    # GB
    "London":     (51.507, -0.127),
    "Manchester": (53.481, -2.243),
    "Birmingham": (52.481, -1.899),
    "Edinburgh":  (55.953, -3.188),
    "Bristol":    (51.454, -2.588),
    # IT
    "Milan":      (45.465, 9.186),
    "Rome":       (41.890, 12.492),
    "Turin":      (45.070, 7.687),
    "Bologna":    (44.494, 11.342),
    # ES
    "Madrid":     (40.416, -3.703),
    "Barcelona":  (41.385, 2.173),
    "Valencia":   (39.470, -0.376),
    "Seville":    (37.389, -5.984),
    # NL
    "Amsterdam":  (52.370, 4.895),
    "Rotterdam":  (51.922, 4.479),
    "Eindhoven":  (51.441, 5.479),
    "Utrecht":    (52.091, 5.122),
    # PL
    "Warsaw":     (52.230, 21.012),
    "Krakow":     (50.062, 19.940),
    "Wroclaw":    (51.107, 17.039),
    "Gdansk":     (54.352, 18.647),
    # CZ
    "Prague":     (50.075, 14.438),
    "Brno":       (49.195, 16.608),
    "Ostrava":    (49.833, 18.292),
    # SE
    "Stockholm":  (59.333, 18.065),
    "Gothenburg": (57.707, 11.967),
    "Malmo":      (55.605, 13.003),
    # NO
    "Oslo":       (59.913, 10.752),
    "Bergen":     (60.393, 5.324),
    "Trondheim":  (63.431, 10.395),
    # FI
    "Helsinki":   (60.169, 24.935),
    "Tampere":    (61.498, 23.761),
    "Oulu":       (65.012, 25.472),
    # DK
    "Copenhagen": (55.676, 12.568),
    "Aarhus":     (56.156, 10.210),
    # BE
    "Brussels":   (50.850, 4.352),
    "Antwerp":    (51.219, 4.402),
    "Ghent":      (51.054, 3.720),
    # PT
    "Lisbon":     (38.717, -9.139),
    "Porto":      (41.157, -8.629),
    # RO
    "Bucharest":  (44.426, 26.103),
    "Cluj-Napoca":(46.770, 23.589),
    # HU
    "Budapest":   (47.498, 19.040),
    "Debrecen":   (47.529, 21.637),
    # US
    "Chicago":    (41.878, -87.630),
    "Houston":    (29.760, -95.370),
    "Atlanta":    (33.749, -84.388),
    "Detroit":    (42.331, -83.046),
    "Boston":     (42.360, -71.059),
    # CA
    "Toronto":    (43.653, -79.383),
    "Montreal":   (45.508, -73.588),
    "Vancouver":  (49.283, -123.121),
    # CN
    "Shanghai":   (31.224, 121.469),
    "Beijing":    (39.914, 116.392),
    "Shenzhen":   (22.543, 114.058),
    "Chengdu":    (30.659, 104.065),
    # IN
    "Pune":       (18.521, 73.856),
    "Bangalore":  (12.971, 77.594),
    "Chennai":    (13.083, 80.270),
    "Mumbai":     (19.076, 72.878),
    # TR
    "Istanbul":   (41.013, 28.948),
    "Ankara":     (39.920, 32.854),
    "Izmir":      (38.423, 27.143),
    # BR
    "Sao Paulo":  (-23.549, -46.633),
    "São Paulo":  (-23.549, -46.633),
    "Curitiba":   (-25.429, -49.271),
    "Campinas":   (-22.906, -47.063),
    # AU
    "Melbourne":  (-37.814, 144.963),
    "Sydney":     (-33.869, 151.208),
    "Brisbane":   (-27.469, 153.026),
    # SG
    "Singapore":  (1.352, 103.820),
    # ZA
    "Johannesburg": (-26.205, 28.050),
    "Cape Town":  (-33.924, 18.424),
}

# ── Address templates ─────────────────────────────────────────────────────────
# Per-country street-name pools and format functions.
# zip_code format: string, country-appropriate.

STREET_NAMES_DE = [
    "Werkstraße", "Industriestraße", "Bahnhofstraße", "Hauptstraße",
    "Mühlenweg", "Schillerstraße", "Goethestraße", "Ringstraße",
]
STREET_NAMES_FR = [
    "Avenue de la Gare", "Rue de l'Industrie", "Boulevard du Marché",
    "Rue Victor Hugo", "Allée des Roses", "Chemin du Moulin",
]
STREET_NAMES_GB = [
    "Industrial Road", "Station Street", "Victoria Avenue",
    "High Street", "Mill Lane", "Park Road",
]
STREET_NAMES_US = [
    "Industrial Parkway", "Commerce Drive", "Technology Boulevard",
    "Main Street", "Oak Avenue", "Maple Drive",
]
STREET_NAMES_NL = [
    "Industrieweg", "Stationsplein", "Hoofdstraat",
    "Meerweg", "Kanaalstraat",
]
STREET_NAMES_PL = [
    "Ulica Przemysłowa", "Aleja Solidarności", "Ulica Fabryczna",
    "Ulica Dworcowa", "Ulica Lipowa",
]
STREET_NAMES_GENERIC = [
    "Industrial Road", "Commerce Street", "Factory Avenue",
    "Business Park Drive", "Enterprise Way",
]


def _street(country: str, rng: np.random.Generator, loc_id: int) -> tuple[str, str]:
    """Return (address, zip_code) for a given country."""
    num = int(rng.integers(1, 200))

    if country in {"DE", "AT", "CH"}:
        names = STREET_NAMES_DE
        name = names[loc_id % len(names)]
        addr = f"{name} {num}"
        # DE/AT/CH zip: 5 digits (DE/AT), 4 digits (CH)
        if country == "CH":
            z = f"{1000 + (loc_id * 37 + num) % 8999}"
        else:
            z = f"{10000 + (loc_id * 97 + num) % 89999:05d}"
    elif country in {"FR", "BE", "LU"}:
        names = STREET_NAMES_FR
        name = names[loc_id % len(names)]
        addr = f"{num} {name}"
        z = f"{1000 + (loc_id * 53 + num) % 98000:05d}"
    elif country in {"GB"}:
        names = STREET_NAMES_GB
        name = names[loc_id % len(names)]
        addr = f"{num} {name}"
        letters = "ABCDEFGHJKLMNPRST"
        l1 = letters[(loc_id * 3) % len(letters)]
        l2 = letters[(loc_id * 7 + 2) % len(letters)]
        d1 = (loc_id * 11 + num) % 10
        d2 = (loc_id * 13 + num) % 10
        z = f"{l1}{d1} {d2}{l2}W"
    elif country in {"US", "CA"}:
        names = STREET_NAMES_US
        name = names[loc_id % len(names)]
        addr = f"{num} {name}"
        z = f"{10000 + (loc_id * 79 + num) % 89999:05d}"
    elif country in {"NL"}:
        names = STREET_NAMES_NL
        name = names[loc_id % len(names)]
        addr = f"{name} {num}"
        d = (loc_id * 17 + num) % 9000 + 1000
        l1 = "ABCDEFGHJKLMNPRST"[(loc_id * 3) % 17]
        l2 = "ABCDEFGHJKLMNPRST"[(loc_id * 7 + 2) % 17]
        z = f"{d} {l1}{l2}"
    elif country in {"PL", "CZ", "SK", "HU", "RO"}:
        names = STREET_NAMES_PL
        name = names[loc_id % len(names)]
        addr = f"{name} {num}"
        d1 = (loc_id * 11 + num) % 90 + 10
        d2 = (loc_id * 13 + num) % 900 + 100
        z = f"{d1}-{d2}"
    else:
        names = STREET_NAMES_GENERIC
        name = names[loc_id % len(names)]
        addr = f"{num} {name}"
        z = f"{10000 + (loc_id * 61 + num) % 89999}"

    return addr, str(z)


# ── Builders ─────────────────────────────────────────────────────────────────

def build_regions(master: list[dict]) -> pd.DataFrame:
    countries = sorted({s["country"] for s in master})
    rows = []
    for iso2 in countries:
        cdp, name = REGION_MAP[iso2]
        rows.append({"country_code": iso2, "cdp_region": cdp, "country_name": name})
    return pd.DataFrame(rows)[["country_code", "cdp_region", "country_name"]]


def build_bu_rc_groups(master: list[dict]) -> pd.DataFrame:
    seen: dict[str, str] = {}
    for s in master:
        seen[s["bu_rc_name"]] = s["bu_rc_group"]
    rows = [{"bu_rc_group": grp, "bu_rc": name} for name, grp in sorted(seen.items())]
    return pd.DataFrame(rows)[["bu_rc_group", "bu_rc"]]


def build_locations(master: list[dict], rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for s in master:
        city = s["city"]
        loc_id = s["location_id"]
        country = s["country"]

        base_lat, base_lon = CITY_COORDS.get(city, (0.0, 0.0))
        jitter_lat = float(rng.uniform(-0.025, 0.025))
        jitter_lon = float(rng.uniform(-0.025, 0.025))
        lat = round(base_lat + jitter_lat, 6)
        lon = round(base_lon + jitter_lon, 6)

        address, zip_code = _street(country, rng, loc_id)

        rows.append(
            {
                "location_id":   loc_id,
                "location_name": s["location_name"],
                "bu_rc_id":      s["bu_rc_id"],
                "bu_rc_name":    s["bu_rc_name"],
                "active":        s["active"],
                "country":       country,
                "zip_code":      zip_code,
                "city":          city,
                "address":       address,
                "latitude":      lat,
                "longitude":     lon,
            }
        )

    col_order = [
        "location_id", "location_name", "bu_rc_id", "bu_rc_name",
        "active", "country", "zip_code", "city", "address", "latitude", "longitude",
    ]
    return pd.DataFrame(rows)[col_order]


# ── Verification ──────────────────────────────────────────────────────────────

def verify(
    energy: pd.DataFrame,
    regions: pd.DataFrame,
    bu_rc: pd.DataFrame,
    locations: pd.DataFrame,
) -> None:
    errors: list[str] = []

    # Column order
    expected_regions  = ["country_code", "cdp_region", "country_name"]
    expected_bu_rc    = ["bu_rc_group", "bu_rc"]
    expected_locs     = [
        "location_id", "location_name", "bu_rc_id", "bu_rc_name",
        "active", "country", "zip_code", "city", "address", "latitude", "longitude",
    ]
    if list(regions.columns) != expected_regions:
        errors.append(f"regions columns mismatch: {list(regions.columns)}")
    if list(bu_rc.columns) != expected_bu_rc:
        errors.append(f"bu_rc_groups columns mismatch: {list(bu_rc.columns)}")
    if list(locations.columns) != expected_locs:
        errors.append(f"locations columns mismatch: {list(locations.columns)}")

    # Join integrity: all energy keys covered
    energy_countries = set(energy["country"].dropna().unique())
    missing_c = energy_countries - set(regions["country_code"])
    if missing_c:
        errors.append(f"regions missing countries: {missing_c}")

    energy_bu = set(energy["bu_rc_name"].dropna().unique())
    missing_bu = energy_bu - set(bu_rc["bu_rc"])
    if missing_bu:
        errors.append(f"bu_rc_groups missing bu_rc: {missing_bu}")

    energy_lids = set(energy["location_id"].dropna().unique())
    missing_l = energy_lids - set(locations["location_id"])
    if missing_l:
        errors.append(f"locations missing location_ids: {missing_l}")

    # Coordinate sanity
    lat_ok = locations["latitude"].between(-90, 90).all()
    lon_ok = locations["longitude"].between(-180, 180).all()
    if not lat_ok:
        errors.append("latitude out of range")
    if not lon_ok:
        errors.append("longitude out of range")
    # No city should sit at (0, 0) — would mean a missing CITY_COORDS entry
    zero_coords = locations[(locations["latitude"] == 0) & (locations["longitude"] == 0)]
    if not zero_coords.empty:
        errors.append(f"zero coordinates for cities: {zero_coords['city'].unique().tolist()}")

    # Location_name + active consistency with energy
    loc_idx = locations.set_index("location_id")
    energy_idx = energy.drop_duplicates("location_id").set_index("location_id")
    for lid in energy_lids:
        if lid in loc_idx.index and lid in energy_idx.index:
            if loc_idx.loc[lid, "active"] != energy_idx.loc[lid, "active"]:
                errors.append(f"active mismatch for location_id={lid}")
                break

    print("\n" + "=" * 60)
    print("VERIFICATION")
    print(f"  regions.csv:       {len(regions)} rows, {regions['cdp_region'].nunique()} distinct cdp_regions")
    print(f"  bu_rc_groups.csv:  {len(bu_rc)} rows")
    print(f"  locations.csv:     {len(locations)} rows, active=False: {(~locations['active']).sum()}")
    print(f"  join integrity:    countries={len(missing_c)==0}  bu_rc={len(missing_bu)==0}  location_id={len(missing_l)==0}")
    print(f"  coordinates OK:    lat={lat_ok}  lon={lon_ok}  no_zero={zero_coords.empty}")
    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    ! {e}")
        raise SystemExit(1)
    else:
        print("  all checks PASSED [OK]")
    print("=" * 60)


# ── End-to-end smoke test (optional, requires package installed) ──────────────

def smoke_test_join(synth_dir: Path) -> None:
    """Load energy frame via DataRequestExecutor and check no NaN join columns."""
    try:
        from epoch_switch.agents.data.d1_loader.d1_data_executor import DataRequestExecutor
    except ImportError:
        print("\n  [smoke test skipped: epoch_switch not importable]")
        return

    executor = DataRequestExecutor(data_dir=synth_dir)
    df = executor._load_energy_frame()
    join_cols = ["cdp_region", "bu_rc_group", "city", "latitude"]
    missing = {c: df[c].isna().sum() for c in join_cols if c in df.columns}
    any_missing = any(v > 0 for v in missing.values())
    print(f"\n  Smoke test (_load_energy_frame): {len(df)} rows joined")
    for col, n in missing.items():
        status = "!" if n > 0 else "OK"
        print(f"    [{status}] {col} NaN count: {n}")
    if any_missing:
        print("  [WARN] some join columns have NaN — check lookup tables")
    else:
        print("  Smoke test PASSED [OK]")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    repo_root = Path(__file__).parent.parent
    synth_dir = repo_root / "data" / "synthetic"
    master_path = synth_dir / ".master_dims.json"

    if not master_path.exists():
        raise FileNotFoundError(
            f"{master_path} not found — run gen_synthetic_energy.py first."
        )

    print("Loading master dims ...")
    master: list[dict] = json.loads(master_path.read_text(encoding="utf-8"))
    print(f"  {len(master)} sites, {len({s['country'] for s in master})} countries")

    energy_path = synth_dir / "dist_ie_energy_raw.csv"
    if not energy_path.exists():
        raise FileNotFoundError(
            f"{energy_path} not found — run gen_synthetic_energy.py first."
        )
    energy = pd.read_csv(energy_path)

    rng = np.random.default_rng(SEED)

    print("\nBuilding regions.csv ...")
    regions = build_regions(master)

    print("Building bu_rc_groups.csv ...")
    bu_rc = build_bu_rc_groups(master)

    print("Building dist_ie_locations.csv ...")
    locations = build_locations(master, rng)

    verify(energy, regions, bu_rc, locations)

    # Write
    regions.to_csv(synth_dir / "regions.csv", index=False)
    bu_rc.to_csv(synth_dir / "bu_rc_groups.csv", index=False)
    locations.to_csv(synth_dir / "dist_ie_locations.csv", index=False)

    print(f"\n  regions.csv       => {synth_dir / 'regions.csv'}")
    print(f"  bu_rc_groups.csv  => {synth_dir / 'bu_rc_groups.csv'}")
    print(f"  locations.csv     => {synth_dir / 'dist_ie_locations.csv'}")

    smoke_test_join(synth_dir)
    print("\nDone.")


if __name__ == "__main__":
    main()
