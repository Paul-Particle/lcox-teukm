"""
report.py — human-facing output: console tables and plots.

All number formatting and unit-for-display conversions live here so the model
modules stay free of presentation concerns.
"""

import os
from dataclasses import replace
from pathlib import Path

import numpy as np

from params import Params
from lcot import lcot_fossil, lcot_elec, lcot_ironair, lcot_nuclear
from analysis import optimize_speed, crossover_dmax
from units import CENTS_PER_USD, PERCENT_PER_FRACTION, KWH_PER_MWH, KG_PER_TONNE
from style import (
    fca_template, fca_blue, blue_black, dark_gray, highlight_blue,
    sand_yellow, green, very_dark_gray, light_blue, gray,
    header_geometry, apply_dot, apply_logo, add_trace_label, inject_titillium_font,
)

# All technology cases, in display order:
# (table_name, plot_label, cost model fn, color, clip)
# `clip` marks the battery ships, whose LCOT blows up at long D_max and is
# capped at Y_CAP_CENTS in line plots so it doesn't flatten the region of
# interest (viewers can still zoom).
CASES = [
    ("fossil",   "fossil",                      lcot_fossil,  blue_black,  False),
    ("li-ion",   "battery-electric (Li-ion)",   lcot_elec,    fca_blue,    True),
    ("iron-air", "battery-electric (iron-air)", lcot_ironair, sand_yellow, True),
    ("nuclear",  "nuclear (SMR)",               lcot_nuclear, green,       False),
]
Y_CAP_CENTS = 50.0

# Sample hop lengths (km) shown in the per-ship breakdown table.
SAMPLE_HOPS_KM = [200, 500, 1000, 2000, 4000]

# Sensitivity sweep axes (Li-ion battery cost x electricity price).
SENS_BATTERY_USD_PER_KWH = [250, 150, 80]
SENS_ELEC_USD_PER_KWH = [0.09, 0.06, 0.03]


def print_base_header(p: Params) -> None:
    print("=" * 72)
    print("BASE CASE")
    print(f"  fuel ${p.fuel_usd_per_t}/t  |  elec ${p.elec_usd_per_kwh}/kWh  "
          f"|  Li-ion ${p.battery_usd_per_kwh}/kWh  |  hull {p.gross_slots:.0f} TEU")
    print(f"  iron-air ${p.ironair_usd_per_kwh}/kWh @ "
          f"{p.ironair_eta_rt*PERCENT_PER_FRACTION:.0f}% RTE  "
          f"|  SMR ${p.nuclear_usd_per_kw:.0f}/kW")
    print("=" * 72)


def print_energy_cost(p: Params) -> None:
    """Useful-energy cost per kWh, all powertrains head to head."""
    costs = {
        "fossil": (p.fuel_usd_per_t / KG_PER_TONNE / p.fuel_lhv_kwh_per_kg)
                  / p.eta_fossil,
        "li-ion": p.elec_usd_per_kwh / (p.battery_eta_rt * p.eta_charge * p.eta_elec),
        "iron-air": p.elec_usd_per_kwh / (p.ironair_eta_rt * p.eta_charge * p.eta_elec),
        "nuclear": p.nuclear_fuel_usd_per_kwh_th / p.eta_nuclear,
    }
    cheapest = min(costs, key=costs.get)
    print("\nEnergy cost per USEFUL kWh:  "
          + "   ".join(f"{name} ${c:.3f}" for name, c in costs.items())
          + f"   ({cheapest} cheapest)")


def print_breakdown(p: Params) -> None:
    """LCOT breakdown at sample hop lengths, speed optimized per ship."""
    print("\nBreakdown at sample hop lengths (speed optimized per ship):")
    hdr = (f"{'D_max':>7} {'ship':>8} {'v_opt':>6} {'LCOT':>9} "
           f"{'$fixed':>8} {'$energy':>8} {'cargo':>6} {'batt_TEU':>9} "
           f"{'batt_MWh':>9} {'batt_yr':>7}")
    print(hdr)
    print("-" * len(hdr))
    for d in SAMPLE_HOPS_KM:
        for name, _, fn, _, _ in CASES:
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
    """Crossover vs the fossil incumbent, per battery case."""
    print()
    for name, _, fn, _, clip in CASES:
        if not clip:  # the battery cases are the ones with a D_max crossover
            continue
        co = crossover_dmax(p, d_grid, fn, lcot_fossil)
        msg = ("never cheaper in base case" if co is None
               else f"cheaper than fossil below {co:.0f} km" if np.isfinite(co)
               else "always cheaper than fossil")
        print(f"Crossover D_max: {name} {msg}")


def print_sensitivity(p: Params, d_grid) -> None:
    """Li-ion-vs-fossil crossover D_max vs battery cost and electricity price.
    (Iron-air and nuclear axes are out of scope for this table.)"""
    print("\n" + "=" * 72)
    print("SENSITIVITY: Li-ion crossover D_max (km) vs battery cost & elec price")
    print("=" * 72)
    print(f"{'':>14}" + "".join(f"  elec ${e:>4.2f}" for e in SENS_ELEC_USD_PER_KWH))
    for bc in SENS_BATTERY_USD_PER_KWH:
        row = f"batt ${bc:>3}/kWh "
        for ep in SENS_ELEC_USD_PER_KWH:
            pp = replace(p, battery_usd_per_kwh=bc, elec_usd_per_kwh=ep)
            c = crossover_dmax(pp, d_grid, lcot_elec, lcot_fossil)
            cell = "none" if c is None else (">6000" if np.isinf(c) else f"{c:.0f}")
            row += f"  {cell:>9}"
        print(row)


# ---- Shared save helper ----------------------------------------------------

def _save_html_png(fig, out_dir: str, stem: str) -> list:
    """Write fig as font-injected HTML and static PNG. Returns saved paths."""
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    html_path = os.path.join(out_dir, f"{stem}.html")
    html = inject_titillium_font(fig.to_html(include_plotlyjs=True))
    Path(html_path).write_text(html, encoding="utf-8")
    saved.append(html_path)
    png_path = os.path.join(out_dir, f"{stem}.png")
    try:
        fig.write_image(png_path, scale=2)
        saved.append(png_path)
    except Exception as e:
        print(f"PNG export skipped ({stem}):", e)
    return saved


# ---- Plots -----------------------------------------------------------------

def plot_lcot_vs_dmax(p: Params, out_dir: str) -> list:
    """LCOT vs D_max for all ships. Interactive HTML + static PNG.

    The battery curves are clipped at Y_CAP_CENTS so the long-haul blow-up
    does not flatten the region of interest; the viewer can still zoom freely.
    Hover shows the optimum speed at each D_max point.
    """
    try:
        import plotly.graph_objects as go
    except Exception as e:
        print("plot skipped:", e)
        return []

    dd = np.geomspace(30, 6000, 160)

    hover = ("D_max %{x:.0f} km<br>LCOT %{y:.3f} c/TEU·km"
             "<br>v_opt %{customdata:.1f} kn"
             "<extra>%{fullData.name}</extra>")

    fig = go.Figure()
    fossil_max = 0.0  # the incumbent sets the y scale; other curves may exit it
    for name, label, fn, color, clip in CASES:
        results = [optimize_speed(fn, p, d) for d in dd]
        ys = [r["lcot"] * CENTS_PER_USD for r in results]
        if clip:
            ys = [min(y, Y_CAP_CENTS) for y in ys]
        if name == "fossil":
            fossil_max = max(ys)
        fig.add_trace(go.Scatter(x=dd, y=ys, mode="lines", name=label,
                                 customdata=[r["v"] for r in results],
                                 line=dict(color=color, width=2.2),
                                 hovertemplate=hover))

    fig_width, fig_height = 820, 520
    margin_l = fca_template.layout.margin.l
    margin_r = fca_template.layout.margin.r
    margin_t = fca_template.layout.margin.t
    margin_b = 140
    geom = header_geometry(fig_width, fig_height, margin_l, margin_r, margin_t)

    fig.update_layout(
        template=fca_template,
        title=dict(text="Levelized cost of transport vs inter-swap distance",
                   x=geom["title_x"]),
        xaxis_title="D_max  —  longest hop between swap ports (km, log scale)",
        hovermode="x unified",
        showlegend=False,
        margin=dict(b=margin_b),
        width=fig_width, height=fig_height,
    )
    xticks = [30, 50, 100, 200, 500, 1000, 2000, 5000]
    fig.update_xaxes(type="log", tickmode="array", tickvals=xticks,
                     ticktext=[f"{v}" for v in xticks])
    fig.update_yaxes(range=[0, max(fossil_max, 8) * 1.3])

    fig.add_annotation(
        text="US cents per TEU·km", xref="paper", yref="paper",
        x=0, xanchor="left", xshift=geom["header_x_shift"],
        y=1, yanchor="bottom", yshift=12, showarrow=False,
        font=dict(family="Titillium Web", size=18, color=blue_black),
    )
    fig.add_annotation(
        text=(f"Base case: Li-ion &#36;{p.battery_usd_per_kwh:.0f}/kWh, "
              f"iron-air &#36;{p.ironair_usd_per_kwh:.0f}/kWh @ "
              f"{p.ironair_eta_rt*PERCENT_PER_FRACTION:.0f}% RTE, "
              f"electricity &#36;{p.elec_usd_per_kwh}/kWh, "
              f"SMR &#36;{p.nuclear_usd_per_kw:.0f}/kW"),
        xref="paper", yref="paper", x=0, xanchor="left",
        xshift=geom["header_x_shift"],
        y=0, yanchor="top", yshift=-98, showarrow=False,
        font=dict(family="Titillium Web", size=12, color=dark_gray),
    )

    apply_dot(fig, geom)
    for i, (_, label, _, color, _) in enumerate(CASES):
        add_trace_label(fig, label, color, x=0.97, y=0.90 - 0.11 * i)
    apply_logo(fig, fig_width, fig_height, margin_l, margin_r, margin_t, margin_b)

    return _save_html_png(fig, out_dir, "lcot_vs_dmax")


def plot_speed_vs_dmax(p: Params, out_dir: str) -> list:
    """Optimum speed vs D_max for all ships.

    Shows how each powertrain's economically optimal speed varies with the
    inter-swap distance. The battery ships slow down at longer ranges to
    reduce battery size and recover cargo capacity; the power-bound iron-air
    ship sits near the minimum speed almost everywhere.
    """
    try:
        import plotly.graph_objects as go
    except Exception as e:
        print("plot skipped:", e)
        return []

    dd = np.geomspace(30, 6000, 160)

    hover = "D_max %{x:.0f} km  →  v_opt %{y:.1f} kn<extra>%{fullData.name}</extra>"
    fig = go.Figure()
    for _, label, fn, color, _ in CASES:
        vs = [optimize_speed(fn, p, d)["v"] for d in dd]
        fig.add_trace(go.Scatter(x=dd, y=vs, mode="lines", name=label,
                                 line=dict(color=color, width=2.2),
                                 hovertemplate=hover))

    fig_width, fig_height = 820, 520
    margin_l = fca_template.layout.margin.l
    margin_r = fca_template.layout.margin.r
    margin_t = fca_template.layout.margin.t
    margin_b = 140
    geom = header_geometry(fig_width, fig_height, margin_l, margin_r, margin_t)

    fig.update_layout(
        template=fca_template,
        title=dict(text="Optimal speed vs inter-swap distance", x=geom["title_x"]),
        xaxis_title="D_max  —  longest hop between swap ports (km, log scale)",
        hovermode="x unified",
        showlegend=False,
        margin=dict(b=margin_b),
        width=fig_width, height=fig_height,
    )
    xticks = [30, 50, 100, 200, 500, 1000, 2000, 5000]
    fig.update_xaxes(type="log", tickmode="array", tickvals=xticks,
                     ticktext=[f"{v}" for v in xticks])
    fig.update_yaxes(title_text="knots",
                     range=[p.v_min_kn * 0.9, p.v_max_kn * 1.1])

    fig.add_annotation(
        text="knots", xref="paper", yref="paper",
        x=0, xanchor="left", xshift=geom["header_x_shift"],
        y=1, yanchor="bottom", yshift=12, showarrow=False,
        font=dict(family="Titillium Web", size=18, color=blue_black),
    )
    fig.add_annotation(
        text=(f"Base case: Li-ion &#36;{p.battery_usd_per_kwh:.0f}/kWh, "
              f"iron-air &#36;{p.ironair_usd_per_kwh:.0f}/kWh, "
              f"electricity &#36;{p.elec_usd_per_kwh}/kWh, "
              f"SMR &#36;{p.nuclear_usd_per_kw:.0f}/kW"),
        xref="paper", yref="paper", x=0, xanchor="left",
        xshift=geom["header_x_shift"],
        y=0, yanchor="top", yshift=-98, showarrow=False,
        font=dict(family="Titillium Web", size=12, color=dark_gray),
    )

    apply_dot(fig, geom)
    # Labels sit in the empty band between the fossil (~12 kn) and the
    # flat-out nuclear (22 kn) curves.
    for i, (_, label, _, color, _) in enumerate(CASES):
        add_trace_label(fig, label, color, x=0.97, y=0.78 - 0.11 * i)
    apply_logo(fig, fig_width, fig_height, margin_l, margin_r, margin_t, margin_b)

    return _save_html_png(fig, out_dir, "speed_vs_dmax")


def plot_lcot_tornado(p: Params, d_km: float, out_dir: str) -> list:
    """Sensitivity tornado: how much each parameter shifts LCOT for the electric
    ship at a fixed D_max. Bars extend from Δ(low param) to Δ(high param)
    relative to the base case, sorted by total swing (most influential on top).

    The low-value bar is blue (often favourable) and the high-value bar is sand
    (often unfavourable); both start from 0 on a shared x axis.
    """
    try:
        import plotly.graph_objects as go
    except Exception as e:
        print("plot skipped:", e)
        return []

    base_lcot = optimize_speed(lcot_elec, p, d_km)["lcot"] * CENTS_PER_USD

    # (label, field_name, low_value, high_value)
    sens = [
        ("battery cost ($/kWh)",          "battery_usd_per_kwh",    80,    350),
        ("electricity price ($/kWh)",     "elec_usd_per_kwh",       0.03,  0.15),
        ("battery energy density (kWh/TEU)", "battery_kwh_per_teu", 2000,  5000),
        ("discount rate",                 "discount_rate",           0.05,  0.12),
        ("load factor",                   "load_factor",             0.65,  0.95),
        ("hull CAPEX ($M)",               "hull_capex_usd",          30e6,  60e6),
        ("O&M electric ($/yr)",           "om_elec_usd_yr",          2e6,   4e6),
        ("battery cycle life",            "battery_cycle_life",      2000,  6000),
    ]

    rows = []
    for label, field, lo, hi in sens:
        dl = optimize_speed(lcot_elec, replace(p, **{field: lo}), d_km)["lcot"] * CENTS_PER_USD - base_lcot
        dh = optimize_speed(lcot_elec, replace(p, **{field: hi}), d_km)["lcot"] * CENTS_PER_USD - base_lcot
        rows.append((label, dl, dh, abs(dh - dl)))

    rows.sort(key=lambda r: r[3], reverse=True)
    labels  = [r[0] for r in rows]
    d_lows  = [r[1] for r in rows]
    d_highs = [r[2] for r in rows]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="low param", y=labels, x=d_lows, orientation="h",
        marker_color=fca_blue, opacity=0.9,
        hovertemplate="%{y}<br>Δ LCOT %{x:+.3f} c/TEU·km  (low value)<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="high param", y=labels, x=d_highs, orientation="h",
        marker_color=sand_yellow, opacity=0.9,
        hovertemplate="%{y}<br>Δ LCOT %{x:+.3f} c/TEU·km  (high value)<extra></extra>",
    ))

    fig_width, fig_height = 820, 560
    margin_l_val = 260
    margin_r_val = 60
    margin_t_val = 96
    margin_b = 120
    geom = header_geometry(fig_width, fig_height,
                           margin_l_val, margin_r_val, margin_t_val)

    fig.update_layout(
        template=fca_template,
        title=dict(
            text=f"LCOT sensitivity — battery-electric at D_max {d_km:.0f} km",
            x=geom["title_x"],
        ),
        barmode="overlay",
        showlegend=False,
        hovermode="y unified",
        margin=dict(l=margin_l_val, r=margin_r_val, t=margin_t_val, b=margin_b),
        width=fig_width, height=fig_height,
    )
    fig.update_xaxes(
        title_text="Δ LCOT  (c/TEU·km vs base case)",
        zeroline=True, zerolinecolor=blue_black, zerolinewidth=1.5,
    )
    fig.update_yaxes(showgrid=False)

    fig.add_annotation(
        text="US cents per TEU·km  vs base case", xref="paper", yref="paper",
        x=0, xanchor="left", xshift=geom["header_x_shift"],
        y=1, yanchor="bottom", yshift=12, showarrow=False,
        font=dict(family="Titillium Web", size=16, color=blue_black),
    )
    fig.add_annotation(
        text=(f"Base case: battery &#36;{p.battery_usd_per_kwh:.0f}/kWh, "
              f"electricity &#36;{p.elec_usd_per_kwh}/kWh, "
              f"D_max {d_km:.0f} km"),
        xref="paper", yref="paper", x=0, xanchor="left",
        xshift=geom["header_x_shift"],
        y=0, yanchor="top", yshift=-80, showarrow=False,
        font=dict(family="Titillium Web", size=11, color=dark_gray),
    )

    apply_dot(fig, geom)
    add_trace_label(fig, "low param value",  fca_blue,    x=0.97, y=0.96)
    add_trace_label(fig, "high param value", sand_yellow, x=0.97, y=0.87)
    apply_logo(fig, fig_width, fig_height,
               margin_l_val, margin_r_val, margin_t_val, margin_b)

    return _save_html_png(fig, out_dir, "lcot_tornado")


def plot_teu_tech_tradeoff(p: Params, d_km: float, out_dir: str) -> list:
    """Bar chart comparing LCOT and cargo TEU for multiple technology cases.

    Left panel: LCOT at D_max = d_km. Right panel: cargo TEU capacity.
    Add new cases to the CASES list; use computed=False for placeholder
    entries that have no model yet (lcot_c and teu_c are shown as-is).

    Intended to grow: add further battery chemistries, nuclear SMR, hydrogen
    fuel cell, wind-assisted, etc. as those models are developed.
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception as e:
        print("plot skipped:", e)
        return []

    # ---- Case definitions --------------------------------------------------
    # Each entry: label, bar color, compute function (or None), Params override,
    #             lcot_c (override, c/TEU·km), teu_c (override, TEU)
    # When computed=True: lcot_c and teu_c are ignored; the model is run.
    # When computed=False: provide lcot_c/teu_c directly (rough estimates OK).
    CASES = [
        dict(label="Fossil\n(baseline)", color=blue_black, computed=True,
             fn=lcot_fossil, params=p,
             lcot_c=None, teu_c=None),
        dict(label="Battery\n250 $/kWh", color=fca_blue, computed=True,
             fn=lcot_elec, params=replace(p, battery_usd_per_kwh=250),
             lcot_c=None, teu_c=None),
        dict(label="Battery\n150 $/kWh", color=highlight_blue, computed=True,
             fn=lcot_elec, params=replace(p, battery_usd_per_kwh=150),
             lcot_c=None, teu_c=None),
        dict(label="Battery\n80 $/kWh", color="#52B5D9", computed=True,
             fn=lcot_elec, params=replace(p, battery_usd_per_kwh=80),
             lcot_c=None, teu_c=None),
        dict(label="Battery 250 $/kWh\n5 MWh/TEU", color="#70D2F0", computed=True,
             fn=lcot_elec,
             params=replace(p, battery_usd_per_kwh=250, battery_kwh_per_teu=5000),
             lcot_c=None, teu_c=None),
        dict(label="Iron-air\n30 $/kWh", color=sand_yellow, computed=True,
             fn=lcot_ironair, params=p,
             lcot_c=None, teu_c=None),
        dict(label="Nuclear SMR\n6000 $/kW", color=green, computed=True,
             fn=lcot_nuclear, params=p,
             lcot_c=None, teu_c=None),
        # Placeholder — replace with model results when available
        dict(label="H₂ fuel cell\n(placeholder)", color=very_dark_gray, computed=False,
             fn=None, params=None,
             lcot_c=None, teu_c=None),
    ]

    case_labels, lcots, teus, colors = [], [], [], []
    for c in CASES:
        case_labels.append(c["label"])
        colors.append(c["color"])
        if c["computed"]:
            r = optimize_speed(c["fn"], c["params"], d_km)
            lcots.append(r["lcot"] * CENTS_PER_USD)
            teus.append(max(r["cargo_cap"], 0))
        else:
            lcots.append(c["lcot_c"])
            teus.append(c["teu_c"])

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=["LCOT (c/TEU·km)", f"Cargo TEU at D_max {d_km:.0f} km"],
        horizontal_spacing=0.12,
    )

    bar_kw = dict(showlegend=False, marker_line_width=0)

    # LCOT bars — None values plotted as zero with annotation "TBD"
    lcot_plot = [v if v is not None else 0.0 for v in lcots]
    fig.add_trace(go.Bar(
        x=case_labels, y=lcot_plot,
        marker_color=colors,
        text=[f"{v:.2f}" if v is not None else "TBD" for v in lcots],
        textposition="outside",
        hovertemplate="%{x}<br>LCOT %{y:.3f} c/TEU·km<extra></extra>",
        **bar_kw,
    ), row=1, col=1)

    # TEU bars
    teu_plot = [v if v is not None else 0.0 for v in teus]
    fig.add_trace(go.Bar(
        x=case_labels, y=teu_plot,
        marker_color=colors,
        text=[f"{v:.0f}" if v is not None else "TBD" for v in teus],
        textposition="outside",
        hovertemplate="%{x}<br>%{y:.0f} cargo TEU<extra></extra>",
        **bar_kw,
    ), row=1, col=2)

    fig_width, fig_height = 1040, 560
    margin_l = fca_template.layout.margin.l
    margin_r = fca_template.layout.margin.r
    margin_t = fca_template.layout.margin.t
    margin_b = 80
    geom = header_geometry(fig_width, fig_height, margin_l, margin_r, margin_t)

    fig.update_layout(
        template=fca_template,
        title=dict(text="Technology & TEU tradeoffs — selected powertrain cases",
                   x=geom["title_x"]),
        showlegend=False,
        margin=dict(b=margin_b),
        width=fig_width, height=fig_height,
    )
    fig.update_yaxes(title_text="c/TEU·km", row=1, col=1)
    fig.update_yaxes(title_text="TEU", row=1, col=2)

    # Push y-axis upper limit so outside-bar text is not clipped
    lcot_max = max((v for v in lcots if v is not None), default=1.0)
    teu_max  = max((v for v in teus  if v is not None), default=100.0)
    fig.update_yaxes(range=[0, lcot_max * 1.35], row=1, col=1)
    fig.update_yaxes(range=[0, teu_max  * 1.25], row=1, col=2)

    fig.add_annotation(
        text=(f"Base case: battery &#36;{p.battery_usd_per_kwh:.0f}/kWh, "
              f"electricity &#36;{p.elec_usd_per_kwh}/kWh, "
              f"D_max {d_km:.0f} km — placeholders are rough estimates"),
        xref="paper", yref="paper", x=0, xanchor="left",
        xshift=geom["header_x_shift"],
        y=0, yanchor="top", yshift=-48, showarrow=False,
        font=dict(family="Titillium Web", size=11, color=dark_gray),
    )

    apply_dot(fig, geom)
    apply_logo(fig, fig_width, fig_height, margin_l, margin_r, margin_t, margin_b)

    return _save_html_png(fig, out_dir, "teu_tech_tradeoff")
