"""
mrv_fleet.py — turn the EU MRV (THETIS-MRV) fleet emissions data into grounded anchors for
the config, plus the empirical size-scaling relations the scale-factor feature will rest on.

A STANDALONE data utility — NOT imported by the model. It imports only `units` (conversion
factors, shared with the model so the arithmetic agrees) and, best-effort, `style` (house
plot chrome). It reads nothing from the model and writes nothing into it: its job is to print
a handful of fleet numbers you can compare against config.yaml by hand, and to fit the
size-scaling exponents (power, speed) that ground a narrow-band ship scale factor.

What it extracts for the container subset:
  - operating speed         — distance / time-at-sea  (the fleet slow-steams; grounds design_v_kn + the op_v_kn sweep)
  - operating useful power  — fuel/distance x speed x LHV x drive-eff (propulsion + hotel; admiralty anchor for p_ref_kw)
  - energy intensity        — fuel/distance in kWh-fuel/km
  - cargo carried           — fuel/distance / fuel-per-transport-work(mass)  (a size proxy; grounds gross/deadweight)
  - technical efficiency    — the published EEDI/EIV value (gCO2/t.nm), parsed from the label
  - scaling fits            — power and speed vs cargo-carried, log-log (the scale-factor exponents)

The MRV column headers drift between export years (and sit a couple of preamble rows below the
sheet title), so columns are matched by fuzzy keyword and the match is printed. Missing columns
are reported, not fatal. The public file carries no nameplate DWT, so "cargo carried" is DERIVED
from the two published intensities — it is the cargo actually moved (size x utilization), a real
fleet anchor but NOT max deadweight; treated as a size proxy with that caveat.

Get the data (free): https://mrv.emsa.europa.eu/#public/emission-report — one .xlsx per year
(>=5000 GT ships). Drop them in data/ (gitignored).

Run:  uv run scripts/mrv_fleet.py [paths-or-glob ...] [--type container] [--no-plot]
      (no path -> pools every data/*.xlsx; multiple years are concatenated)
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from units import KM_PER_NM, KG_PER_TONNE

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"

# Burner assumptions for the energy/power back-calc — VLSFO, mirrors config.yaml's
# vlsfo.price.lhv_kwh_per_kg and mechanical-fossil.efficiency.drive. Stated here (not
# imported) to keep the utility decoupled from the model; printed so they're auditable.
LHV_KWH_PER_KG = 11.1     # VLSFO lower heating value
DRIVE_EFFICIENCY = 0.48   # chemical -> shaft (2-stroke)

# Config anchors this utility cross-checks against (read off config.yaml by hand, not imported).
CONFIG_P_REF_KW = 20000.0
CONFIG_V_REF_KN = 18.0
CONFIG_GROSS_TEU = 3000.0
CONFIG_UNIT_MASS_T = 12.0
CONFIG_LOAD_FACTOR = 0.80


# ---- column matching (headers drift across export years) -------------------

def _norm(text) -> str:
    """Lowercase, collapse non-alphanumerics to single spaces — so 'CO2 emissions per
    distance [kg CO2 / n mile]' and 'co2_emissions_per_distance' both match the same needles."""
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _find(columns, *needles, exclude=()):
    """First column whose normalized name contains every needle and no `exclude` token."""
    for col in columns:
        name = _norm(col)
        if all(needle in name for needle in needles) and not any(x in name for x in exclude):
            return col
    return None


def _read_table(path: Path) -> pd.DataFrame:
    """Read one MRV export, locating the real header row (a couple of title rows sit above the
    column names) and keeping the first (full-year) sheet."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path, sheet_name=0, header=None, dtype=object)
    else:  # csv / tsv — sniff the separator
        raw = pd.read_csv(path, header=None, dtype=object, sep=None, engine="python")

    header_row = next(
        (i for i in range(min(20, len(raw)))
         if any("imo number" in _norm(c) for c in raw.iloc[i])
         or any("ship" in _norm(c) and "type" in _norm(c) for c in raw.iloc[i])),
        0,
    )
    table = raw.iloc[header_row + 1:].copy()
    table.columns = [str(c).strip() for c in raw.iloc[header_row]]
    return table.reset_index(drop=True)


def _numeric(frame: pd.DataFrame, col):
    """Coerce a column to float (MRV writes 'Division by zero!', 'Not Applicable', etc. for
    missing values -> NaN). Returns an all-NaN series if the column is absent, so derived
    quantities stay aligned to the frame."""
    if col is None or col not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    return pd.to_numeric(frame[col], errors="coerce")


# ---- loading & derivation --------------------------------------------------

def load_container_fleet(paths: list[Path], type_keyword: str) -> pd.DataFrame:
    """Pool the given MRV files, filter to the requested ship type, and return a tidy frame of
    the derived quantities (one row per ship-year). Pure arithmetic on the published columns."""
    frames = []
    for path in paths:
        table = _read_table(path)
        type_col = _find(table.columns, "ship", "type")
        if type_col is None:
            print(f"  {path.name}: no 'ship type' column — skipped")
            continue
        subset = table[table[type_col].astype(str).str.lower().str.contains(type_keyword, na=False)]
        frames.append(_derive(subset))
        print(f"  {path.name}: {len(subset)} '{type_keyword}' rows")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _derive(subset: pd.DataFrame) -> pd.DataFrame:
    """Derive the grounded quantities from one file's container subset.

    Distance is not published, so it's recovered as total-fuel / fuel-per-distance; speed is
    then distance / time-at-sea. Operating useful power is fuel/distance x speed converted to a
    fuel-energy rate and multiplied by the drive efficiency (so it's propulsion + hotel, a touch
    above pure propulsion). Cargo carried is fuel/distance / fuel-per-transport-work(mass)."""
    fuel_t = _numeric(subset, _find(subset.columns, "total", "fuel", "consumption"))
    fuel_per_nm = _numeric(subset, _find(subset.columns, "fuel", "consumption", "per", "distance",
                                         exclude=("laden", "transport")))
    time_at_sea_h = _numeric(subset, _find(subset.columns, "time", "sea", exclude=("ice",)))
    fuel_per_tw_mass = _numeric(subset, _find(subset.columns, "fuel", "per", "transport", "work", "mass",
                                              exclude=("laden",)))
    eedi_label = subset[_find(subset.columns, "technical", "efficiency")] \
        if _find(subset.columns, "technical", "efficiency") else pd.Series(np.nan, index=subset.index)

    distance_nm = (fuel_t * KG_PER_TONNE / fuel_per_nm).replace([np.inf, -np.inf], np.nan)
    speed_kn = (distance_nm / time_at_sea_h).replace([np.inf, -np.inf], np.nan)
    # kg/nm -> kg/km -> kWh-fuel/km
    energy_per_km = fuel_per_nm / KM_PER_NM * LHV_KWH_PER_KG
    # kg/nm * nm/h = kg/h; * kWh/kg * drive-eff = useful kW (propulsion + hotel)
    useful_power_kw = fuel_per_nm * speed_kn * LHV_KWH_PER_KG * DRIVE_EFFICIENCY
    # (kg/nm) / (g / (t.nm)) * 1000 g/kg = tonnes carried
    cargo_carried_t = (fuel_per_nm / fuel_per_tw_mass * 1000.0).replace([np.inf, -np.inf], np.nan)
    eedi = pd.to_numeric(eedi_label.astype(str).str.extract(r"([\d.]+)\s*gCO")[0], errors="coerce")

    return pd.DataFrame({
        "speed_kn": speed_kn,
        "useful_power_kw": useful_power_kw,
        "energy_per_km": energy_per_km,
        "cargo_carried_t": cargo_carried_t,
        "eedi": eedi,
    })


# ---- summary, scaling fits, cross-checks -----------------------------------

def _percentiles(name: str, series: pd.Series, unit: str, lo: float = 0.0) -> pd.Series | None:
    """Print p10/p25/median/p75/p90 over the finite values above `lo`. Returns the cleaned values."""
    values = series[np.isfinite(series) & (series > lo)]
    if values.empty:
        print(f"  {name:<26} no values")
        return None
    p10, p25, p50, p75, p90 = np.percentile(values, [10, 25, 50, 75, 90])
    print(f"  {name} ({unit})  n={len(values)}")
    print(f"    p10 {p10:>10,.1f} | p25 {p25:>10,.1f} | median {p50:>10,.1f} | "
          f"p75 {p75:>10,.1f} | p90 {p90:>10,.1f}")
    return values


def _scaling_fit(size_t: pd.Series, value: pd.Series, label: str) -> None:
    """Fit value ~ size^exponent (log-log least squares) and print the exponent, prefactor at
    a 30,000 t reference, and correlation — the empirical basis for a narrow-band scale factor."""
    mask = np.isfinite(size_t) & np.isfinite(value) & (size_t > 500) & (value > 0)
    if mask.sum() < 20:
        print(f"  {label}: too few points ({mask.sum()})")
        return
    log_size, log_value = np.log(size_t[mask]), np.log(value[mask])
    exponent, intercept = np.polyfit(log_size, log_value, 1)
    correlation = np.corrcoef(log_size, log_value)[0, 1]
    at_ref = np.exp(intercept + exponent * np.log(30000.0))
    print(f"  {label}: ~ size^{exponent:+.2f}  (={at_ref:,.0f} at 30,000 t carried; "
          f"r={correlation:.2f}, n={mask.sum()})")


def summarize(fleet: pd.DataFrame) -> None:
    """Print the grounded distributions, size-scaling fits, and config cross-checks."""
    if fleet.empty:
        print("\nNo rows — check the file(s)/ship-type keyword.")
        return

    print(f"\n=== fleet distributions  (n={len(fleet)} ship-years; "
          f"LHV {LHV_KWH_PER_KG} kWh/kg, drive-eff {DRIVE_EFFICIENCY}) ===")
    speed = _percentiles("operating speed", fleet["speed_kn"], "kn", lo=3.0)
    power = _percentiles("operating useful power", fleet["useful_power_kw"], "kW (propulsion+hotel)")
    _percentiles("energy intensity", fleet["energy_per_km"], "kWh-fuel/km")
    cargo = _percentiles("cargo carried (derived)", fleet["cargo_carried_t"], "t")
    _percentiles("technical efficiency", fleet["eedi"], "gCO2/t.nm")

    # Speed clamp on the power fit: above ~30 kn the derived speed is a data error.
    sane = fleet[(fleet["speed_kn"] > 5) & (fleet["speed_kn"] < 30)]
    print("\n=== size-scaling fits (basis for a narrow-band ship scale factor) ===")
    _scaling_fit(sane["cargo_carried_t"], sane["useful_power_kw"], "operating power")
    _scaling_fit(sane["cargo_carried_t"], sane["speed_kn"], "operating speed")

    print("\n=== power vs size, binned (admiralty anchor; extrapolated to v_ref via cube law) ===")
    bins = [(500, 10000), (10000, 25000), (25000, 45000),
            (45000, 75000), (75000, 120000), (120000, np.inf)]
    for lo, hi in bins:
        b = sane[(sane["cargo_carried_t"] >= lo) & (sane["cargo_carried_t"] < hi)]
        if len(b) < 5:
            continue
        med_power, med_speed = b["useful_power_kw"].median(), b["speed_kn"].median()
        power_at_vref = med_power * (CONFIG_V_REF_KN / med_speed) ** 3
        hi_label = "+" if np.isinf(hi) else f"{hi:,.0f}"
        print(f"  cargo {lo:>7,.0f}-{hi_label:<8}t  n={len(b):>4} | "
              f"med {med_power:>7,.0f} kW @ {med_speed:4.1f} kn | "
              f"{power_at_vref:>8,.0f} kW @ {CONFIG_V_REF_KN:.0f} kn (cube-law)")

    _cross_checks(speed, power, cargo)


def _cross_checks(speed, power, cargo) -> None:
    """A few lines comparing the fleet medians against the current config anchors."""
    print("\n=== config cross-checks ===")
    config_cargo_t = CONFIG_GROSS_TEU * CONFIG_UNIT_MASS_T * CONFIG_LOAD_FACTOR
    print(f"  config 3000-TEU ship carries ~ {config_cargo_t:,.0f} t "
          f"(gross {CONFIG_GROSS_TEU:.0f} x {CONFIG_UNIT_MASS_T:.0f} t/TEU x load {CONFIG_LOAD_FACTOR})")
    if cargo is not None:
        print(f"    fleet median cargo carried {np.median(cargo):,.0f} t — config ship sits "
              f"around the {(cargo < config_cargo_t).mean() * 100:.0f}th percentile of the fleet")
    if speed is not None:
        print(f"  config design/ref speed {CONFIG_V_REF_KN:.0f} kn vs fleet median operating "
              f"{np.median(speed):.1f} kn — the fleet slow-steams below design")
    print(f"  config p_ref_kw {CONFIG_P_REF_KW:,.0f} kW @ {CONFIG_V_REF_KN:.0f} kn is PROPULSION only; "
          f"the binned power above is propulsion+hotel — subtract ~1.5-2 MW hotel to compare")


# ---- plot (best-effort, house style) ---------------------------------------

def plot_fleet(fleet: pd.DataFrame, out_dir: Path) -> list:
    """Histograms of cargo carried, operating speed, and operating power, clipped to [p1,p99]
    so data-error outliers don't flatten them. Skips silently if plotly/style aren't importable
    (the model imports nothing from here, so importing style keeps it decoupled)."""
    panels = [("cargo_carried_t", "Cargo carried (t)"),
              ("speed_kn", "Operating speed (kn)"),
              ("useful_power_kw", "Operating power (kW)")]
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        from style import fca_template, fca_blue, sand_yellow, green, inject_titillium_font
    except Exception as e:
        print("plot skipped:", e)
        return []

    fig = make_subplots(rows=1, cols=len(panels), subplot_titles=[t for _, t in panels])
    colors = [fca_blue, sand_yellow, green]
    for i, (col, _) in enumerate(panels, start=1):
        v = fleet[col][np.isfinite(fleet[col]) & (fleet[col] > 0)]
        if col == "speed_kn":
            v = v[(v > 3) & (v < 30)]
        if v.empty:
            continue
        lo, hi = np.percentile(v, [1, 99])
        v = v[(v >= lo) & (v <= hi)]
        fig.add_trace(go.Histogram(x=v, nbinsx=40, marker_color=colors[i - 1], showlegend=False),
                      row=1, col=i)
        fig.update_xaxes(title_text=f"median {np.median(v):,.0f}", row=1, col=i)

    fig.update_layout(template=fca_template, width=460 * len(panels), height=460,
                      title=dict(text="EU MRV container fleet", x=0.04),
                      bargap=0.05, margin=dict(t=90, b=70))

    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    html_path = out_dir / "mrv_fleet.html"
    html_path.write_text(inject_titillium_font(fig.to_html(include_plotlyjs=True)), encoding="utf-8")
    saved.append(html_path)
    png_path = out_dir / "mrv_fleet.png"
    try:
        fig.write_image(str(png_path), scale=2)
        saved.append(png_path)
    except Exception as e:
        print(f"PNG export skipped: {e}")
    return saved


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Ground config anchors from EU MRV fleet data.")
    parser.add_argument("paths", nargs="*",
                        help="MRV .xlsx/.csv files or globs (default: every data/*.xlsx)")
    parser.add_argument("--type", default="container", help="ship-type keyword (default: container)")
    parser.add_argument("--no-plot", action="store_true", help="skip the histograms")
    args = parser.parse_args(argv)

    patterns = args.paths or [str(DATA_DIR / "*.xlsx")]
    paths = sorted({Path(p) for pattern in patterns for p in glob.glob(pattern)})
    if not paths:
        print(f"no files matched {patterns}")
        print("Download the public files from https://mrv.emsa.europa.eu/#public/emission-report "
              "into data/")
        sys.exit(1)

    print(f"Loading {len(paths)} file(s):")
    fleet = load_container_fleet(paths, args.type.lower())
    summarize(fleet)

    if not args.no_plot and not fleet.empty:
        for path in plot_fleet(fleet, RESULTS_DIR):
            print(f"\nSaved plot: {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
