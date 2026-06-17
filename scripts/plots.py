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


def main() -> None:
    df = pd.read_parquet(ARTIFACT)
    saved = plot_lcot_vs_dmax(df) + plot_speed_vs_dmax(df)
    for path in saved:
        print("wrote", path)


if __name__ == "__main__":
    main()
