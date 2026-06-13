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
from cases import build_cases, cases_by_name
from cost import cost_fn
from analysis import optimize_speed
from units import CENTS_PER_USD, PERCENT_PER_FRACTION
from style import (
    fca_template, fca_blue, blue_black, dark_gray, highlight_blue,
    sand_yellow, green, very_dark_gray,
    header_geometry, apply_dot, apply_logo, add_trace_label, inject_titillium_font,
)

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
        hovermode="closest",  # only the hovered line (x-unified is too crowded at 7 traces)
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
    for case in build_cases(p):
        results = [optimize_speed(cost_fn(case), p, d) for d in DD_GRID]
        ys = [r["lcot"] * CENTS_PER_USD for r in results]
        if case.clip:
            ys = [min(y, Y_CAP_CENTS) for y in ys]
        if case.name == "fossil":
            fossil_max = max(ys)
        series.append((case.label, case.color, ys, [r["v"] for r in results]))

    return _dmax_line_plot(
        p, out_dir, title="Levelized cost of transport vs inter-swap distance",
        subtitle="US cents per TEU·km", series=series, hover=hover,
        y_range=[0, max(fossil_max, 8) * 1.3], legend_y=0.97, stem="lcot_vs_dmax")




