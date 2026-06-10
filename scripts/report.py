"""
report.py — human-facing output: console tables and the LCOT-vs-D_max plot.

All number formatting and unit-for-display conversions live here so the model
modules stay free of presentation concerns.
"""

import os
from dataclasses import replace

import numpy as np

from params import Params
from lcot import lcot_fossil, lcot_elec
from analysis import optimize_speed, crossover_dmax
from units import CENTS_PER_USD, PERCENT_PER_FRACTION, KWH_PER_MWH, KG_PER_TONNE
from style import fca_template, fca_blue, blue_black

# Sample hop lengths (km) shown in the per-ship breakdown table.
SAMPLE_HOPS_KM = [200, 500, 1000, 2000, 4000]

# Sensitivity sweep axes.
SENS_BATTERY_USD_PER_KWH = [250, 150, 80]
SENS_ELEC_USD_PER_KWH = [0.09, 0.06, 0.03]


def print_base_header(p: Params) -> None:
    print("=" * 72)
    print("BASE CASE")
    print(f"  fuel ${p.fuel_usd_per_t}/t  |  elec ${p.elec_usd_per_kwh}/kWh  "
          f"|  battery ${p.battery_usd_per_kwh}/kWh  |  hull {p.gross_slots:.0f} TEU")
    print("=" * 72)


def print_energy_cost(p: Params) -> None:
    """Useful-energy cost per kWh, fossil vs electric, head to head."""
    fuel_useful = (p.fuel_usd_per_t / KG_PER_TONNE / p.fuel_lhv_kwh_per_kg) / p.eta_fossil
    elec_useful = p.elec_usd_per_kwh / p.eta_charge / p.eta_elec
    cheaper = "electric cheaper" if elec_useful < fuel_useful else "fossil cheaper"
    print(f"\nEnergy cost per USEFUL kWh:  fossil ${fuel_useful:.3f}   "
          f"electric ${elec_useful:.3f}   ({cheaper})")


def print_breakdown(p: Params) -> None:
    """LCOT breakdown at sample hop lengths, speed optimized per ship."""
    print("\nBreakdown at sample hop lengths (speed optimized per ship):")
    hdr = (f"{'D_max':>7} {'ship':>8} {'v_opt':>6} {'LCOT':>9} "
           f"{'$fixed':>8} {'$energy':>8} {'cargo':>6} {'batt_TEU':>9} "
           f"{'batt_MWh':>9} {'batt_yr':>7}")
    print(hdr)
    print("-" * len(hdr))
    for d in SAMPLE_HOPS_KM:
        for name, fn in [("fossil", lcot_fossil), ("electric", lcot_elec)]:
            r = optimize_speed(fn, p, d)
            finite = np.isfinite(r["lcot"])
            fixed_share = (r["annual_fixed"] / (r["annual_fixed"] + r["annual_energy"])
                           if finite else float("nan"))
            energy_share = 1 - fixed_share if finite else float("nan")
            print(f"{d:>7.0f} {name:>8} {r['v']:>6.1f} "
                  f"{r['lcot']*CENTS_PER_USD:>8.3f}c "
                  f"{fixed_share*PERCENT_PER_FRACTION:>7.0f}% "
                  f"{energy_share*PERCENT_PER_FRACTION:>7.0f}% "
                  f"{r['cargo_cap']:>6.0f} {r['battery_slots']:>9.0f} "
                  f"{r['battery_kwh']/KWH_PER_MWH:>9.0f} {r['battery_life']:>7.1f}")


def print_crossover(p: Params, d_grid) -> None:
    co = crossover_dmax(p, d_grid)
    msg = ("electric never cheaper in base case" if co is None
           else f"{co:.0f} km" if np.isfinite(co) else "electric always cheaper")
    print("\nCrossover D_max (electric cheaper below this):", msg)


def print_sensitivity(p: Params, d_grid) -> None:
    """Crossover D_max vs battery cost and electricity price, around base case."""
    print("\n" + "=" * 72)
    print("SENSITIVITY: crossover D_max (km) vs battery cost & electricity price")
    print("=" * 72)
    print(f"{'':>14}" + "".join(f"  elec ${e:>4.2f}" for e in SENS_ELEC_USD_PER_KWH))
    for bc in SENS_BATTERY_USD_PER_KWH:
        row = f"batt ${bc:>3}/kWh "
        for ep in SENS_ELEC_USD_PER_KWH:
            pp = replace(p, battery_usd_per_kwh=bc, elec_usd_per_kwh=ep)
            c = crossover_dmax(pp, d_grid)
            cell = "none" if c is None else (">6000" if np.isinf(c) else f"{c:.0f}")
            row += f"  {cell:>9}"
        print(row)


def plot_lcot_vs_dmax(p: Params, out_dir: str) -> list:
    """Plot base-case LCOT vs D_max for both ships with Plotly; write an
    interactive HTML and a static PNG, and return the list of saved paths
    (empty if Plotly is unavailable).

    The electric curve is clipped at 50 c/TEU·km so the long-haul blow-up does
    not flatten the region where the two ships are actually competitive (the
    viewer can still zoom freely)."""
    try:
        import plotly.graph_objects as go
    except Exception as e:
        print("plot skipped:", e)
        return []

    # Log-spaced grid + log x-axis: the electric ship is cheaper only at short
    # hops (crossover ~120 km), so a linear 100-6000 km axis crushes that whole
    # advantage region into a sliver. Geometric spacing from 30 km gives the
    # short-haul regime the room it needs to be read.
    dd = np.geomspace(30, 6000, 160)
    lf = [optimize_speed(lcot_fossil, p, d)["lcot"] * CENTS_PER_USD for d in dd]
    le = [min(optimize_speed(lcot_elec, p, d)["lcot"] * CENTS_PER_USD, 50) for d in dd]

    hover = "D_max %{x:.0f} km<br>LCOT %{y:.3f} c/TEU·km<extra>%{fullData.name}</extra>"
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dd, y=lf, mode="lines", name="fossil",
                             line=dict(color=blue_black, width=2.2), hovertemplate=hover))
    fig.add_trace(go.Scatter(x=dd, y=le, mode="lines", name="battery-electric",
                             line=dict(color=fca_blue, width=2.2), hovertemplate=hover))
    fig.update_layout(
        template=fca_template,
        # &#36; (literal "$") rather than "$" so static export does not treat
        # the dollar signs as LaTeX/MathJax math delimiters.
        title=("Levelized cost of transport vs inter-swap distance<br>"
               f"<sub>base case, battery &#36;{p.battery_usd_per_kwh:.0f}/kWh, "
               f"elec &#36;{p.elec_usd_per_kwh}/kWh</sub>"),
        xaxis_title="D_max  —  longest hop between swap ports (km, log scale)",
        yaxis_title="LCOT (US cents per TEU·km)",
        hovermode="x unified",
        legend=dict(yanchor="top", y=0.98, xanchor="left", x=0.02),
        width=820, height=520,
    )
    fig.update_xaxes(type="log")
    fig.update_yaxes(range=[0, max(max(lf), 8) * 1.3])

    os.makedirs(out_dir, exist_ok=True)
    saved = []

    # Interactive HTML. include_plotlyjs=True -> self-contained, renders offline.
    html_path = os.path.join(out_dir, "lcot_vs_dmax.html")
    fig.write_html(html_path, include_plotlyjs=True)
    saved.append(html_path)

    # Static PNG for slides/papers (requires the kaleido engine).
    png_path = os.path.join(out_dir, "lcot_vs_dmax.png")
    try:
        fig.write_image(png_path, scale=2)
        saved.append(png_path)
    except Exception as e:
        print("PNG export skipped (kaleido unavailable?):", e)

    return saved
