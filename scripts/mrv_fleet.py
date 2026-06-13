"""
mrv_fleet.py — load the EU MRV (THETIS-MRV) fleet emissions data and summarize the
container-ship subset: deadweight distribution and fuel-per-distance.

A standalone data utility — NOT imported by the model (no coupling to params/cases/
cost). Its job is to turn the public fleet dataset into a few grounded numbers you can
compare against the eyeballed config (ship size, energy intensity), then decide what to
fold in by hand.

Get the data (free):
  1. Open https://mrv.emsa.europa.eu/#public/emission-report
  2. Download the public annual report file (one .xlsx per reporting year; large 5000+GT
     ships only). The portal also offers a combined file.
  3. Run:  uv run scripts/mrv_fleet.py <path-to-file.xlsx> [--type container]

The MRV column headers drift between export years and have a few preamble rows above the
real header, so this matches columns by fuzzy keyword (and prints what it matched) rather
than hard-coding names. Per-ship annual fields it looks for: ship type, reporting period,
deadweight (DWT), total fuel consumption [t], total CO2 [t], time at sea [h], distance
[n mile], fuel-consumption-per-distance [kg/n mile], CO2-per-distance, technical
efficiency (EEDI/EIV). Whatever is present gets summarized; missing columns are reported,
not fatal.
"""

import argparse
import os
import re
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(REPO_ROOT, "results")

NM_PER_KM = 1.0 / 1.852   # nautical miles per km (1 n mile = 1.852 km)


def _norm(x) -> str:
    """Lowercase, strip non-alphanumerics to single spaces — so 'CO₂ emissions
    per distance [kg CO₂ / n mile]' and 'co2_emissions_per_distance' both match."""
    return re.sub(r"[^a-z0-9]+", " ", str(x).lower()).strip()


def _find(columns, *needles, exclude=()):
    """First column whose normalized name contains every needle (and no `exclude`
    token). Needles/excludes are matched as normalized substrings. Returns None."""
    for col in columns:
        n = _norm(col)
        if all(k in n for k in needles) and not any(x in n for x in exclude):
            return col
    return None


def _read_table(path: Path):
    """Read the MRV file into a DataFrame, locating the real header row (the public
    file carries a couple of title/preamble rows above the column names)."""
    import pandas as pd

    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        raw = pd.read_excel(path, header=None, dtype=object)
    else:  # csv / tsv — sniff the separator
        raw = pd.read_csv(path, header=None, dtype=object, sep=None, engine="python")

    header_row = None
    for i in range(min(20, len(raw))):
        cells = [_norm(c) for c in raw.iloc[i].tolist()]
        if any("imo number" in c for c in cells) or any(
                ("ship" in c and "type" in c) for c in cells):
            header_row = i
            break
    if header_row is None:
        header_row = 0  # assume the first row is the header

    df = raw.iloc[header_row + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[header_row].tolist()]
    return df.reset_index(drop=True)


def _numeric(df, col):
    """Coerce a column to float (MRV uses 'Division by zero!', 'Not Applicable', etc.
    for missing values — those become NaN). Returns None if the column is absent."""
    if col is None or col not in df.columns:
        return None
    import pandas as pd
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)


def _describe(name: str, a, unit: str):
    """Print a compact distribution (n, min, p10/25/50/75/90, mean, max) of the
    positive, finite values of `a`. Returns the cleaned array (or None)."""
    if a is None:
        print(f"  {name:<26} column not found")
        return None
    v = a[np.isfinite(a) & (a > 0)]
    if v.size == 0:
        print(f"  {name:<26} no positive values")
        return None
    p = np.percentile(v, [10, 25, 50, 75, 90])
    print(f"  {name} ({unit}): n={v.size}")
    print(f"    min {v.min():,.1f}  p10 {p[0]:,.1f}  p25 {p[1]:,.1f}  "
          f"median {p[2]:,.1f}  p75 {p[3]:,.1f}  p90 {p[4]:,.1f}  "
          f"max {v.max():,.1f}  mean {v.mean():,.1f}")
    return v


def _plot(dwt, fuel_per_nm, out_dir: str) -> list:
    """Two histograms (DWT, fuel per distance) in the house style. Best-effort —
    skips silently if plotly/style aren't importable. Only the model imports nothing
    from here, so importing the standalone style module keeps this decoupled."""
    series = [(dwt, "Deadweight (DWT, tonnes)", "dwt"),
              (fuel_per_nm, "Fuel per distance (kg / n mile)", "fuel")]
    series = [(v, t, k) for v, t, k in series if v is not None and v.size]
    if not series:
        return []
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        from style import (fca_template, fca_blue, sand_yellow, blue_black,
                           dark_gray, inject_titillium_font)
    except Exception as e:
        print("plot skipped:", e)
        return []

    fig = make_subplots(rows=1, cols=len(series),
                        subplot_titles=[t for _, t, _ in series])
    colors = [fca_blue, sand_yellow]
    for i, (v, _, _) in enumerate(series, start=1):
        fig.add_trace(go.Histogram(x=v, nbinsx=40, marker_color=colors[(i - 1) % 2],
                                   showlegend=False), row=1, col=i)
        fig.update_xaxes(title_text="median " + f"{np.median(v):,.0f}", row=1, col=i)

    fig.update_layout(template=fca_template, width=460 * len(series), height=460,
                      title=dict(text="EU MRV container fleet", x=0.04),
                      bargap=0.05, margin=dict(t=90, b=70))

    os.makedirs(out_dir, exist_ok=True)
    saved = []
    html_path = os.path.join(out_dir, "mrv_fleet.html")
    Path(html_path).write_text(inject_titillium_font(fig.to_html(include_plotlyjs=True)),
                               encoding="utf-8")
    saved.append(html_path)
    png_path = os.path.join(out_dir, "mrv_fleet.png")
    try:
        fig.write_image(png_path, scale=2)
        saved.append(png_path)
    except Exception as e:
        print(f"PNG export skipped: {e}")
    return saved


def summarize(df, type_kw: str) -> dict:
    """Filter to the requested ship type and print the grounded summary. Returns the
    cleaned arrays for plotting."""
    cols = list(df.columns)
    c_type = _find(cols, "ship", "type")
    c_period = _find(cols, "reporting", "period") or _find(cols, "year")
    c_dwt = _find(cols, "deadweight") or _find(cols, "dwt")
    c_fuel = _find(cols, "total", "fuel", "consumption")
    c_co2 = _find(cols, "total", "co", "emissions")
    c_time = _find(cols, "time", "sea")
    c_dist = _find(cols, "distance", exclude=("per",))
    c_fpd = _find(cols, "fuel", "consumption", "per", "distance")
    c_cpd = _find(cols, "co", "emissions", "per", "distance")
    c_eff = _find(cols, "technical", "efficiency")

    print("Matched columns:")
    for label, c in [("ship type", c_type), ("reporting period", c_period),
                     ("deadweight", c_dwt), ("total fuel [t]", c_fuel),
                     ("total CO2 [t]", c_co2), ("time at sea [h]", c_time),
                     ("distance [nm]", c_dist), ("fuel/distance [kg/nm]", c_fpd),
                     ("CO2/distance", c_cpd), ("technical efficiency", c_eff)]:
        print(f"  {label:<22} {c if c else '— not found'}")

    if c_type is None:
        print("\nNo 'ship type' column found — cannot filter to container ships. "
              "Check the file/header; columns seen:")
        print("  " + " | ".join(map(str, cols[:20])) + (" ..." if len(cols) > 20 else ""))
        return {}

    mask = df[c_type].astype(str).str.lower().str.contains(type_kw, na=False)
    sub = df[mask]
    print(f"\n'{type_kw}' rows: {len(sub)} of {len(df)} total")
    if c_period is not None:
        yrs = sorted(set(str(x) for x in sub[c_period].dropna().tolist()))
        print(f"reporting period(s): {', '.join(yrs) if yrs else '—'}")
    if len(sub) == 0:
        ship_types = sorted(set(str(x) for x in df[c_type].dropna().tolist()))[:15]
        print(f"  (ship types present include: {', '.join(ship_types)})")
        return {}

    print("\nDistributions (positive, finite values only):")
    dwt = _describe("DWT", _numeric(sub, c_dwt), "tonnes")
    fpd = _describe("fuel / distance", _numeric(sub, c_fpd), "kg / n mile")
    _describe("CO2 / distance", _numeric(sub, c_cpd), "kg / n mile")
    _describe("technical efficiency", _numeric(sub, c_eff), "g CO2 / t·nm")

    # Light, model-decoupled cross-checks (just arithmetic on the fleet numbers).
    if fpd is not None:
        kg_per_km = np.median(fpd) * NM_PER_KM
        print(f"\nCross-check: median fuel {np.median(fpd):,.0f} kg/nm "
              f"= {kg_per_km:,.1f} kg/km. At ~11.1 kWh/kg (VLSFO LHV) that is "
              f"~{kg_per_km * 11.1:,.0f} kWh-fuel/km — an empirical anchor for "
              f"p_ref_kw / eta_fossil (config base ~20 MW at v_ref).")
    if dwt is not None:
        print(f"Cross-check: median DWT {np.median(dwt):,.0f} t. The model sizes by TEU "
              f"(gross_slots=3000); at cargo_t_per_teu=12 that's ~36,000 t of cargo, "
              f"cf. deadweight_t=41,000 t (cargo + energy carrier).")

    return {"dwt": dwt, "fuel_per_nm": fpd}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Summarize EU MRV fleet data (container subset).")
    ap.add_argument("path", help="path to the THETIS-MRV .xlsx / .csv file")
    ap.add_argument("--type", default="container",
                    help="ship-type keyword to filter on (default: container)")
    ap.add_argument("--no-plot", action="store_true", help="skip the histogram output")
    args = ap.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"file not found: {path}")
        print("Download the public file from https://mrv.emsa.europa.eu/#public/emission-report")
        sys.exit(1)

    print(f"Loading {path.name} ...")
    df = _read_table(path)
    print(f"rows: {len(df)}  columns: {len(df.columns)}\n")
    out = summarize(df, args.type.lower())

    if not args.no_plot and out:
        for p in _plot(out.get("dwt"), out.get("fuel_per_nm"), RESULTS_DIR):
            print(f"Saved plot: {os.path.relpath(p, REPO_ROOT)}")


if __name__ == "__main__":
    main()
