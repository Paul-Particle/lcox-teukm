"""
plots.py — Plotly figures for the model results (interactive HTML + static PNG).

Presentation only: every figure is built from the same per-case results the
console report uses. Kept separate from report.py so the console-table logic
(the model's numeric output) reads cleanly on its own.

The two D_max line plots (LCOT and optimal speed) share `_dmax_line_plot`; the
brand chrome (header dot, logo, font embedding, base-case footnote) is factored
into small helpers.
"""

import os
from dataclasses import replace
from pathlib import Path

import numpy as np

from params import Params
from lcot import (lcot_fossil, lcot_lfp, lcot_ironair, lcot_nuclear,
                  lcot_nuclear_elec_containerized, lcot_mobile)
from analysis import optimize_speed
from units import CENTS_PER_USD, PERCENT_PER_FRACTION
from style import (
    fca_template, fca_blue, blue_black, dark_gray, highlight_blue,
    sand_yellow, green, very_dark_gray,
    header_geometry, apply_dot, apply_logo, add_trace_label, inject_titillium_font,
)
from report import CASES

# Log-spaced D_max grid shared by the line plots (short hops need the room).
DD_GRID = np.geomspace(30, 6000, 160)
# Battery LCOT curves are clipped here so the long-haul blow-up doesn't flatten
# the competitive region (viewers can still zoom the interactive HTML).
Y_CAP_CENTS = 50.0


# ---- Shared helpers --------------------------------------------------------

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


def _base_case_footnote(p: Params) -> str:
    """The base-case price line shared by the D_max plots (&#36; = literal $)."""
    rte = p.ironair_eta_charge * p.ironair_eta_discharge * PERCENT_PER_FRACTION
    return (f"Base case: LFP &#36;{p.battery_usd_per_kwh:.0f}/kWh, "
            f"iron-air &#36;{p.ironair_usd_per_kwh:.0f}/kWh @ {rte:.0f}% RTE, "
            f"electricity &#36;{p.elec_usd_per_kwh}/kWh, "
            f"SMR &#36;{p.nuclear_usd_per_kw:.0f}/kW")


def _dmax_line_plot(p: Params, out_dir: str, *, title: str, subtitle: str,
                    series: list, hover: str, y_range: list, legend_y: float,
                    stem: str, yaxis_title: str = None) -> list:
    """One line plot vs D_max (log x). `series` is a list of
    (label, color, y_values, customdata-or-None). Shared by the LCOT and
    optimal-speed plots — they differ only in data, title, y-axis and legend."""
    try:
        import plotly.graph_objects as go
    except Exception as e:
        print("plot skipped:", e)
        return []

    fig = go.Figure()
    for label, color, ys, customdata in series:
        fig.add_trace(go.Scatter(x=DD_GRID, y=ys, mode="lines", name=label,
                                 customdata=customdata,
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
        title=dict(text=title, x=geom["title_x"]),
        xaxis_title="D_max  —  longest hop between swap ports (km, log scale)",
        hovermode="x unified",
        showlegend=True,
        legend=dict(x=0.985, xanchor="right", y=legend_y,
                    yanchor="top" if legend_y >= 0.9 else "middle",
                    bgcolor="rgba(255,255,255,0.65)", borderwidth=0,
                    font=dict(family="Titillium Web", size=12)),
        margin=dict(b=margin_b),
        width=fig_width, height=fig_height,
    )
    xticks = [30, 50, 100, 200, 500, 1000, 2000, 5000]
    fig.update_xaxes(type="log", tickmode="array", tickvals=xticks,
                     ticktext=[f"{v}" for v in xticks])
    yaxis = dict(range=y_range)
    if yaxis_title:
        yaxis["title_text"] = yaxis_title
    fig.update_yaxes(**yaxis)

    fig.add_annotation(
        text=subtitle, xref="paper", yref="paper",
        x=0, xanchor="left", xshift=geom["header_x_shift"],
        y=1, yanchor="bottom", yshift=12, showarrow=False,
        font=dict(family="Titillium Web", size=18, color=blue_black),
    )
    fig.add_annotation(
        text=_base_case_footnote(p), xref="paper", yref="paper",
        x=0, xanchor="left", xshift=geom["header_x_shift"],
        y=0, yanchor="top", yshift=-98, showarrow=False,
        font=dict(family="Titillium Web", size=12, color=dark_gray),
    )

    apply_dot(fig, geom)
    apply_logo(fig, fig_width, fig_height, margin_l, margin_r, margin_t, margin_b)
    return _save_html_png(fig, out_dir, stem)


# ---- D_max line plots ------------------------------------------------------

def plot_lcot_vs_dmax(p: Params, out_dir: str) -> list:
    """LCOT vs D_max for all ships; hover shows the optimum speed at each point.
    Battery curves are clipped at Y_CAP_CENTS; the y-range follows the fossil
    incumbent so the clipped curves can exit the top without flattening it."""
    series, fossil_max = [], 0.0
    hover = ("D_max %{x:.0f} km<br>LCOT %{y:.3f} c/TEU·km"
             "<br>v_opt %{customdata:.1f} kn<extra>%{fullData.name}</extra>")
    for name, label, fn, color, clip in CASES:
        results = [optimize_speed(fn, p, d) for d in DD_GRID]
        ys = [r["lcot"] * CENTS_PER_USD for r in results]
        if clip:
            ys = [min(y, Y_CAP_CENTS) for y in ys]
        if name == "fossil":
            fossil_max = max(ys)
        series.append((label, color, ys, [r["v"] for r in results]))

    return _dmax_line_plot(
        p, out_dir, title="Levelized cost of transport vs inter-swap distance",
        subtitle="US cents per TEU·km", series=series, hover=hover,
        y_range=[0, max(fossil_max, 8) * 1.3], legend_y=0.97, stem="lcot_vs_dmax")


def plot_speed_vs_dmax(p: Params, out_dir: str) -> list:
    """Optimum speed vs D_max for all ships — battery ships slow at long range
    to shrink the pack; the power-bound iron-air ship sits near the minimum."""
    hover = "D_max %{x:.0f} km  →  v_opt %{y:.1f} kn<extra>%{fullData.name}</extra>"
    series = [(label, color, [optimize_speed(fn, p, d)["v"] for d in DD_GRID], None)
              for _, label, fn, color, _ in CASES]
    return _dmax_line_plot(
        p, out_dir, title="Optimal speed vs inter-swap distance",
        subtitle="knots", yaxis_title="knots", series=series, hover=hover,
        y_range=[p.v_min_kn * 0.9, p.v_max_kn * 1.1], legend_y=0.55,
        stem="speed_vs_dmax")


# ---- Sensitivity tornados --------------------------------------------------

# (label, field, low, high) sweeps per case. The new cases' most uncertain
# params dominate (mobile tender, iron-air mass density, modular reactor).
SENS_LFP = [
    ("battery cost ($/kWh)",             "battery_usd_per_kwh",    80,   350),
    ("electricity price ($/kWh)",        "elec_usd_per_kwh",       0.03, 0.15),
    ("battery energy density (kWh/TEU)", "battery_kwh_per_teu",    2000, 5000),
    ("discount rate",                    "discount_rate",          0.05, 0.12),
    ("load factor",                      "load_factor",            0.65, 0.95),
    ("hull CAPEX ($M)",                  "hull_capex_usd",         30e6, 60e6),
    ("non-crew O&M ($/yr)",              "om_elec_usd_yr",         0.6e6, 2e6),
    ("battery cycle life",               "battery_cycle_life",     2000, 6000),
]
SENS_IRONAIR = [
    ("pack density (Wh/kg)",          "ironair_pack_wh_per_kg", 20,   60),
    ("iron-air cost ($/kWh)",         "ironair_usd_per_kwh",    20,   60),
    ("energy density (kWh/TEU)",      "ironair_kwh_per_teu",    1000, 2500),
    ("charge eff",                    "ironair_eta_charge",     0.45, 0.70),
    ("discharge eff",                 "ironair_eta_discharge",  0.70, 0.90),
    ("electricity price ($/kWh)",     "elec_usd_per_kwh",       0.03, 0.15),
    ("discount rate",                 "discount_rate",          0.05, 0.12),
    ("load factor",                   "load_factor",            0.65, 0.95),
]
SENS_MOBILE = [
    ("tender reactor CAPEX ($/kW)",       "mob_tender_usd_per_kw",      4000, 20000),
    ("tender idle / top-up (h)",          "mob_tender_idle_h",          2,    12),
    ("EEZ rendezvous distance (nm)",      "mob_rendezvous_distance_nm", 12,   200),
    ("tender reactor size (kWe)",         "mob_tender_reactor_kw",      15000,45000),
    ("charge availability",               "mob_charge_availability",    0.70, 0.95),
    ("tender O&M ($/yr)",                 "mob_tender_om_usd_yr",       2e6,  10e6),
    ("tender hull CAPEX ($M)",            "mob_tender_capex_hull_usd",  30e6, 100e6),
    ("rendezvous spacing (h)",            "mob_rendezvous_spacing_h",   6,    24),
]
SENS_NUCELEC = [
    ("reactor CAPEX ($/kW)",              "nucc_usd_per_kw",             4000, 20000),
    ("reactor module size (kWe)",         "nucc_unit_kw",                10000,20000),
    ("reactor life (yr)",                 "nucc_life_yr",                10,   30),
    ("overhead / module (TEU)",           "nucc_overhead_slots_per_unit",30,   70),
    ("non-crew O&M ($/yr)",               "nucc_om_usd_yr",             3e6,  8e6),
    ("reactor->elec eff",                 "eta_nuclear",                 0.25, 0.40),
    ("discount rate",                     "discount_rate",               0.05, 0.12),
    ("load factor",                       "load_factor",                 0.65, 0.95),
]


def plot_tornado(p: Params, d_km: float, out_dir: str, fn, sens,
                 case_label: str, stem: str) -> list:
    """Sensitivity tornado for one case `fn` at fixed D_max: each parameter's
    LCOT swing from its low to high value, sorted by total swing (most
    influential on top). Low-value bar blue, high-value sand; both from 0."""
    try:
        import plotly.graph_objects as go
    except Exception as e:
        print("plot skipped:", e)
        return []

    base_lcot = optimize_speed(fn, p, d_km)["lcot"] * CENTS_PER_USD
    if not np.isfinite(base_lcot):
        print(f"tornado skipped ({stem}): base infeasible at {d_km:.0f} km")
        return []

    def _delta(field, val):
        r = optimize_speed(fn, replace(p, **{field: val}), d_km)["lcot"] * CENTS_PER_USD
        return r - base_lcot if np.isfinite(r) else np.nan

    rows = []
    for label, field, lo, hi in sens:
        dl, dh = _delta(field, lo), _delta(field, hi)
        swing = abs(np.nan_to_num(dh) - np.nan_to_num(dl))
        rows.append((label, dl, dh, swing))

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
        title=dict(text=f"LCOT sensitivity — {case_label}", x=geom["title_x"]),
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
        text=(f"{case_label}, base case at D_max {d_km:.0f} km — "
              f"bars span low→high parameter value"),
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

    return _save_html_png(fig, out_dir, stem)


def plot_tornados(p: Params, d_km: float, out_dir: str) -> list:
    """Tornados for the cases whose parameters carry the most uncertainty —
    LFP plus the speculative new cases (iron-air mass, mobile tender, modular
    reactor). One PNG/HTML each."""
    saved = []
    for fn, sens, label, stem in [
        (lcot_lfp,     SENS_LFP,     "battery-electric (LFP)",           "tornado_lfp"),
        (lcot_ironair, SENS_IRONAIR, "battery-electric (iron-air)",      "tornado_ironair"),
        (lcot_mobile,  SENS_MOBILE,  "mobile-reactor charge",            "tornado_mobile"),
        (lcot_nuclear_elec_containerized, SENS_NUCELEC,
         "nuclear-electric (containerized)", "tornado_nucelec"),
    ]:
        saved += plot_tornado(p, d_km, out_dir, fn, sens, label, stem)
    return saved


# ---- Technology / cargo tradeoff -------------------------------------------

def plot_teu_tech_tradeoff(p: Params, d_km: float, out_dir: str) -> list:
    """Bar chart comparing LCOT and cargo TEU for multiple technology cases.

    Left panel: LCOT at D_max = d_km. Right panel: cargo TEU capacity.
    `computed=False` entries are placeholders (lcot_c/teu_c shown as-is) for
    technologies with no model yet (e.g. H2 fuel cell)."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception as e:
        print("plot skipped:", e)
        return []

    # Each entry: label, bar color, computed flag, fn, Params, lcot_c, teu_c.
    cases = [
        dict(label="Fossil\n(baseline)", color=blue_black, computed=True,
             fn=lcot_fossil, params=p, lcot_c=None, teu_c=None),
        dict(label="Battery\n250 $/kWh", color=fca_blue, computed=True,
             fn=lcot_lfp, params=replace(p, battery_usd_per_kwh=250),
             lcot_c=None, teu_c=None),
        dict(label="Battery\n150 $/kWh", color=highlight_blue, computed=True,
             fn=lcot_lfp, params=replace(p, battery_usd_per_kwh=150),
             lcot_c=None, teu_c=None),
        dict(label="Battery\n80 $/kWh", color="#52B5D9", computed=True,
             fn=lcot_lfp, params=replace(p, battery_usd_per_kwh=80),
             lcot_c=None, teu_c=None),
        dict(label="Battery 250 $/kWh\n5 MWh/TEU", color="#70D2F0", computed=True,
             fn=lcot_lfp,
             params=replace(p, battery_usd_per_kwh=250, battery_kwh_per_teu=5000),
             lcot_c=None, teu_c=None),
        dict(label="Iron-air\n30 $/kWh", color=sand_yellow, computed=True,
             fn=lcot_ironair, params=p, lcot_c=None, teu_c=None),
        dict(label="Nuclear SMR\n6000 $/kW", color=green, computed=True,
             fn=lcot_nuclear, params=p, lcot_c=None, teu_c=None),
        # Placeholder — replace with model results when available
        dict(label="H₂ fuel cell\n(placeholder)", color=very_dark_gray,
             computed=False, fn=None, params=None, lcot_c=None, teu_c=None),
    ]

    case_labels, lcots, teus, colors = [], [], [], []
    for c in cases:
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
