"""
plots.py — Plotly figures from the results artifact (interactive HTML + static PNG).

Presentation only: reads the tidy table run.py writes (results/lcot.parquet) and traces
LCOT and optimal speed against D_max, one line per case. The brand chrome (header dot, logo,
font embedding) comes from style.py; the two D_max line plots share `_dmax_line_plot`.

Run after run.py: `python plots.py` -> results/lcot_vs_dmax.{html,png}, speed_vs_dmax.{html,png}.
"""

import os
from pathlib import Path

import pandas as pd

from units import CENTS_PER_USD
from style import (
    fca_template, fca_blue, blue_black, dark_gray, highlight_blue,
    sand_yellow, green, turquois, magenta_red,
    header_geometry, apply_dot, apply_logo, inject_titillium_font,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = REPO_ROOT / "results" / "lcot.parquet"
OUT_DIR = REPO_ROOT / "results"

# Battery LCOT curves are clipped here so the long-haul blow-up doesn't flatten the competitive
# region (viewers can still zoom the interactive HTML). None -> no clip.
Y_CAP_CENTS = 50.0

# Cost-stack y-axis cap (¢/TEU·km), shared by both distance figures so they read on the same
# scale. A case whose total LCOT exceeds it (long-haul LFP) overflows the frame and is labelled.
COST_STACK_Y_CAP = 11.0

# The two distances the cost-breakdown bars are drawn at (km): a medium regional hop and a long
# ocean crossing. Both are points on run.py's D_max sweep.
MEDIUM_HOP_KM = 2000.0
OCEAN_CROSSING_KM = 14000.0

# Per-case display: legend label + palette color + whether to clip the long-haul blow-up.
# Order sets the legend/trace order; clip on the battery-only cases that diverge long-haul.
_DISPLAY = {
    "fossil":         ("Fossil (VLSFO)",          blue_black,     False),
    "e-methanol":     ("E-methanol",              dark_gray,      False),
    "lfp":            ("LFP battery",             fca_blue,       True),
    "iron-air":       ("Iron-air battery",        highlight_blue, True),
    "nuclear-direct": ("Nuclear (direct)",        green,          False),
    "nuclear-int-el": ("Nuclear (integ. elec.)",  turquois,       False),
    "nuclear-cont":   ("Nuclear (containerized)", magenta_red,    False),
    "tender":         ("Nuclear tender",          sand_yellow,    False),
}

# Cost-stack components: artifact column -> (legend label, fill pattern). Every segment of a
# case's bar carries that CASE's color (from _DISPLAY); the PATTERN (white, half-opacity, drawn
# over the full color) distinguishes the component. Listed bottom-to-top of the stack: capital
# (hull/powerplant/store), then fixed opex, then energy.
# NOTE the modular reactors (nuclear-cont, tender) levelize their reactor CAPEX into the per-kWh
# energy rate, so that capital shows up under "Energy", not "Powerplant" (see README).
_COST_COMPONENTS = [
    ("cost_hull",       "Hull",         ""),
    ("cost_powerplant", "Powerplant",   "/"),
    ("cost_store",      "Energy store", "x"),
    ("cost_crew",       "Crew",         "."),
    ("cost_om",         "Other O&M",    "-"),
    ("cost_energy",     "Energy",       "|"),
]
# Solid-white hatch over the solid bar color. `fillmode="overlay"` => the base comes from the bar
# `marker.color` (the standard, reliably-rendered array path) and the white pattern sits on top.
# White is a single scalar at full opacity — NOT a per-bar color array and NOT semi-transparent —
# because plotly.js renders array pattern-colors and pattern opacity inconsistently across bars
# (some fell back to a dark foreground). A scalar opaque white is lighter than every case color
# and renders identically in the browser and in static PNG export.
_HATCH = dict(fillmode="overlay", fgcolor="white", fgopacity=1.0, size=10, solidity=0.4)


# ---- Shared helpers --------------------------------------------------------


def _save_html_png(fig, out_dir, stem: str) -> list:
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


def _dmax_line_plot(out_dir, *, title: str, subtitle: str, series: list, hover: str,
                    y_range: list, legend_y: float, stem: str, yaxis_title: str = None) -> list:
    """One line plot vs D_max (log x). `series` is a list of (label, color, xs, ys, customdata).
    Shared by the LCOT and optimal-speed plots — they differ only in data, title, y-axis."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for label, color, xs, ys, customdata in series:
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=label,
                                 customdata=customdata, connectgaps=False,
                                 line=dict(color=color, width=2.2),
                                 hovertemplate=hover))

    fig_width, fig_height = 820, 520
    margin_l = fca_template.layout.margin.l
    margin_r = fca_template.layout.margin.r
    margin_t = fca_template.layout.margin.t
    margin_b = 90
    geom = header_geometry(fig_width, fig_height, margin_l, margin_r, margin_t)

    fig.update_layout(
        template=fca_template,
        title=dict(text=title, x=geom["title_x"]),
        xaxis_title="D_max  —  longest port-to-port hop (km, log scale)",
        hovermode="closest",  # only the hovered line (x-unified is too crowded at 8 traces)
        showlegend=True,
        legend=dict(x=0.985, xanchor="right", y=legend_y,
                    yanchor="top" if legend_y >= 0.9 else "middle",
                    bgcolor="rgba(255,255,255,0.65)", borderwidth=0,
                    font=dict(family="Titillium Web", size=12)),
        margin=dict(b=margin_b),
        width=fig_width, height=fig_height,
    )
    xticks = [500, 1000, 2000, 5000, 10000, 18000]
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

    apply_dot(fig, geom)
    apply_logo(fig, fig_width, fig_height, margin_l, margin_r, margin_t, margin_b)
    return _save_html_png(fig, out_dir, stem)


def _series(df: pd.DataFrame, value_fn) -> list:
    """Build the (label, color, xs, ys, customdata) series list from the artifact, in
    `_DISPLAY` order. Infeasible points are masked to None so the line breaks there.
    `value_fn(case_rows) -> ys` maps a case's (feasible-masked) rows to the plotted y."""
    series = []
    for case, (label, color, clip) in _DISPLAY.items():
        rows = (df[df["case"] == case]
                .sort_values("d_km")
                .assign(_feas=lambda d: d["feasible"]))
        if rows.empty:
            continue
        xs = rows["d_km"].tolist()
        ys = value_fn(rows, clip)
        speed = rows["op_v_kn"].where(rows["_feas"]).tolist()
        series.append((label, color, xs, ys, speed))
    return series


# ---- D_max line plots ------------------------------------------------------

def plot_lcot_vs_dmax(df: pd.DataFrame, out_dir=OUT_DIR) -> list:
    """LCOT vs D_max for all cases; hover shows the optimal speed at each point. Battery curves
    are clipped at Y_CAP_CENTS; the y-range follows the fossil incumbent so clipped curves can
    exit the top without flattening it."""
    def lcot_cents(rows, clip):
        cents = (rows["lcot"] * CENTS_PER_USD).where(rows["_feas"])
        if clip and Y_CAP_CENTS:
            cents = cents.clip(upper=Y_CAP_CENTS)
        return cents.tolist()

    series = _series(df, lcot_cents)
    fossil = df[(df["case"] == "fossil") & df["feasible"]]
    fossil_max = (fossil["lcot"] * CENTS_PER_USD).max() if not fossil.empty else 8.0

    hover = ("D_max %{x:.0f} km<br>LCOT %{y:.3f} ¢/TEU·km"
             "<br>v_opt %{customdata:.1f} kn<extra>%{fullData.name}</extra>")
    return _dmax_line_plot(
        out_dir, title="Levelized cost of transport vs hop distance",
        subtitle="US cents per TEU·km", series=series, hover=hover,
        y_range=[0, max(fossil_max, 8) * 1.3], legend_y=0.97, stem="lcot_vs_dmax")


def plot_speed_vs_dmax(df: pd.DataFrame, out_dir=OUT_DIR) -> list:
    """Optimal cruise speed vs D_max for all cases."""
    def speed(rows, _clip):
        return rows["op_v_kn"].where(rows["_feas"]).tolist()

    series = _series(df, speed)
    hover = ("D_max %{x:.0f} km<br>v_opt %{y:.1f} kn<extra>%{fullData.name}</extra>")
    return _dmax_line_plot(
        out_dir, title="Cost-optimal cruise speed vs hop distance",
        subtitle="knots", series=series, hover=hover, yaxis_title="optimal speed (kn)",
        y_range=[0, 24], legend_y=0.5, stem="speed_vs_dmax")


# ---- Cost-breakdown stacked bars ------------------------------------------

def plot_cost_stack(df: pd.DataFrame, d_km: float, *, title: str, subtitle: str,
                    stem: str, y_cap: float = COST_STACK_Y_CAP, out_dir=OUT_DIR) -> list:
    """Absolute LCOT broken into cost components, one stacked bar per case, at a fixed `d_km`.
    Bar height is the case's total LCOT (¢/TEU·km); every segment carries the case color and the
    component is read off the fill pattern. Cases are drawn in `_DISPLAY` order, infeasible ones
    dropped. A bar taller than `y_cap` overflows the frame and gets an off-scale total label."""
    import plotly.graph_objects as go

    feasible = df[(df["d_km"] == d_km) & df["feasible"]]
    cases = [c for c in _DISPLAY if c in set(feasible["case"])]
    labels = [_DISPLAY[c][0] for c in cases]
    colors = [_DISPLAY[c][1] for c in cases]
    # one row per case at this distance; LCOT contribution = annualized cost / annual cargo·km
    rows = {c: feasible[feasible["case"] == c].iloc[0] for c in cases}
    denom = {c: rows[c]["legs"] * rows[c]["d_km"] * rows[c]["carried"] for c in cases}
    fig = go.Figure()
    for col, comp_label, shape in _COST_COMPONENTS:
        ys = [rows[c][col] * CENTS_PER_USD / denom[c] for c in cases]
        fig.add_trace(go.Bar(
            x=labels, y=ys, showlegend=False, customdata=[comp_label] * len(cases),
            marker=dict(color=colors, pattern=dict(shape=shape, **_HATCH)),
            hovertemplate="%{x}<br>%{customdata}: %{y:.2f} ¢/TEU·km<extra></extra>"))

    # pattern key: neutral-grey dummy bars (zero height) carry only the component->pattern mapping
    for col, comp_label, shape in _COST_COMPONENTS:
        fig.add_trace(go.Bar(
            x=[labels[0]], y=[0], name=comp_label, showlegend=True, hoverinfo="skip",
            marker=dict(color=dark_gray, pattern=dict(shape=shape, **_HATCH))))

    # total LCOT label above each bar; off-scale bars (clipped by y_cap) say so explicitly
    totals = {c: sum(rows[c][col] for col, *_ in _COST_COMPONENTS) * CENTS_PER_USD / denom[c]
              for c in cases}
    for label, c in zip(labels, cases):
        total = totals[c]
        over = total > y_cap
        fig.add_annotation(
            x=label, y=min(total, y_cap), yanchor="bottom", yshift=4,
            text=(f"↑ {total:.0f}" if over else f"{total:.1f}"), showarrow=False,
            font=dict(family="Titillium Web", size=11,
                      color=magenta_red if over else blue_black))

    fig_width, fig_height = 820, 540
    margin_l = fca_template.layout.margin.l
    margin_r = fca_template.layout.margin.r
    margin_t = fca_template.layout.margin.t
    margin_b = 110
    geom = header_geometry(fig_width, fig_height, margin_l, margin_r, margin_t)

    fig.update_layout(
        template=fca_template, barmode="stack", bargap=0.32,
        title=dict(text=title, x=geom["title_x"]),
        showlegend=True,
        legend=dict(title_text="cost component", x=0.985, xanchor="right", y=0.98,
                    yanchor="top", bgcolor="rgba(255,255,255,0.65)", borderwidth=0,
                    font=dict(family="Titillium Web", size=12)),
        margin=dict(b=margin_b), width=fig_width, height=fig_height)
    fig.update_xaxes(tickangle=-30, tickfont=dict(family="Titillium Web", size=12))
    fig.update_yaxes(title_text="LCOT  (US¢ / TEU·km)", range=[0, y_cap])

    fig.add_annotation(
        text=subtitle, xref="paper", yref="paper",
        x=0, xanchor="left", xshift=geom["header_x_shift"],
        y=1, yanchor="bottom", yshift=12, showarrow=False,
        font=dict(family="Titillium Web", size=18, color=blue_black))

    apply_dot(fig, geom)
    apply_logo(fig, fig_width, fig_height, margin_l, margin_r, margin_t, margin_b)
    return _save_html_png(fig, out_dir, stem)


def main() -> None:
    df = pd.read_parquet(ARTIFACT)
    saved = plot_lcot_vs_dmax(df) + plot_speed_vs_dmax(df)
    saved += plot_cost_stack(
        df, MEDIUM_HOP_KM, stem="cost_stack_medium",
        title="Cost breakdown — medium hop",
        subtitle=f"US cents per TEU·km  ·  D_max = {MEDIUM_HOP_KM/1000:.0f},000 km")
    saved += plot_cost_stack(
        df, OCEAN_CROSSING_KM, stem="cost_stack_ocean",
        title="Cost breakdown — ocean crossing",
        subtitle=f"US cents per TEU·km  ·  D_max = {OCEAN_CROSSING_KM/1000:.0f},000 km")
    for path in saved:
        print("wrote", path)


if __name__ == "__main__":
    main()
