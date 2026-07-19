"""
plots.py — Plotly figures for the LCOT model (interactive HTML + static PNG).

Two families, two data sources:

- **Fleet views** read the `fleet` study's tidy table (results/studies/fleet/table.parquet): LCOT
  and optimal speed vs D_max (a line per case, crossovers where they cross), and cost-stack
  breakdowns.
- **Sensitivity views** read a study's store (results/studies/<study>/indices.csv): the Sobol
  S1/ST index bars with bootstrap CI whiskers. The lever landscape (LCOT vs speed, optimum
  starred) is evaluated on the fly — it is the sweep plot with op_v_kn retained instead of
  argmin-collapsed, which the unified axis model makes a one-line change.

The brand chrome (header dot, logo, font embedding) comes from style.py; every fill is a SOLID
shade (plotly's `marker.pattern` hatching renders inconsistently across versions, so it is not
used). Each plot runs the study it needs if that store is missing, so `lcot plot` is
self-contained (or use `lcot all`).
"""

import numpy as np
import pandas as pd

from common.paths import RESULTS_DIR, STUDIES_DIR, ASSUMPTIONS_PATH, STUDIES_PATH
from common.units import CENTS_PER_USD
from viz.style import (
    fca_template, fca_blue, blue_black, dark_gray, highlight_blue, light_blue,
    sand_yellow, green, turquois, magenta_red,
    BRAND_FONT, lighten, contrast_shades, apply_header, save_figure,
)

FLEET_STUDY = "fleet"                           # the study the fleet views visualize
ARTIFACT = STUDIES_DIR / FLEET_STUDY / "table.parquet"
OUT_DIR = RESULTS_DIR

# Battery LCOT curves are clipped here so the long-haul blow-up doesn't flatten the competitive
# region (viewers can still zoom the interactive HTML). None -> no clip.
Y_CAP_CENTS = 50.0

# Cost-stack y-axis cap (¢/TEU·km), shared by both distance figures so they read on the same
# scale. A case whose total LCOT exceeds it (long-haul LFP) overflows the frame and is labelled.
COST_STACK_Y_CAP = 11.0

# The two distances the cost-breakdown bars are drawn at (km): a medium regional hop and a long
# ocean crossing. Both are points on the fleet D_max sweep.
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

# Cost-stack components: (artifact column, legend label), bottom-to-top of the stack — capital
# (hull/powerplant/store), then fixed opex, then energy. Every segment of a case's bar is a SOLID
# SHADE of that CASE's color (from _DISPLAY): hue encodes the case, lightness the component. Solid
# fills only — plotly's hatch/`marker.pattern` engine renders inconsistently across plotly/kaleido
# versions (a hue-correlated light/dark inversion on some setups), so it is avoided here.
# Shades are assigned by `style.contrast_shades` (interleaved, darkest at both ends).
# NOTE the modular reactors (nuclear-cont, tender) levelize their reactor CAPEX into the per-kWh
# energy rate, so that capital shows up under "Energy", not "Powerplant" (see README).
_COST_COMPONENTS = [
    ("cost_hull",       "Hull"),
    ("cost_powerplant", "Powerplant"),
    ("cost_store",      "Energy store"),
    ("cost_crew",       "Crew"),
    ("cost_om",         "Other O&M"),
    ("cost_energy",     "Energy"),
]


# ---- Shared helpers --------------------------------------------------------

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

def _dmax_line_plot(out_dir, *, title: str, subtitle: str, series: list, hover: str,
                    y_range: list, legend_y: float, stem: str) -> list:
    """One line plot vs D_max (log x). `series` is a list of (label, color, xs, ys, customdata).
    Shared by the LCOT and optimal-speed plots — they differ only in data, title, y-axis."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for label, color, xs, ys, customdata in series:
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=label,
                                 customdata=customdata, connectgaps=False,
                                 line=dict(color=color, width=2.2),
                                 hovertemplate=hover))

    fig.update_layout(
        template=fca_template,
        xaxis_title="D_max  —  longest port-to-port hop (km, log scale)",
        hovermode="closest",  # only the hovered line (x-unified is too crowded at 8 traces)
        showlegend=True,
        legend=dict(x=0.985, xanchor="right", y=legend_y,
                    yanchor="top" if legend_y >= 0.9 else "middle"),
    )
    xticks = [500, 1000, 2000, 5000, 10000, 18000]
    fig.update_xaxes(type="log", tickmode="array", tickvals=xticks,
                     ticktext=[f"{v}" for v in xticks])
    # No y-axis title: the subtitle states the quantity and units (matches the other figures).
    fig.update_yaxes(range=y_range)

    apply_header(fig, title=title, subtitle=subtitle,
                 fig_width=820, fig_height=520, margin_b=90)
    return save_figure(fig, out_dir, stem)


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
        subtitle="knots", series=series, hover=hover,
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
    shades = contrast_shades(len(_COST_COMPONENTS))

    fig = go.Figure()
    for (col, comp_label), shade in zip(_COST_COMPONENTS, shades):
        ys = [rows[c][col] * CENTS_PER_USD / denom[c] for c in cases]
        fig.add_trace(go.Bar(
            x=labels, y=ys, showlegend=False, customdata=[comp_label] * len(cases),
            marker=dict(color=[lighten(color, shade) for color in colors]),
            hovertemplate="%{x}<br>%{customdata}: %{y:.2f} ¢/TEU·km<extra></extra>"))

    # shade key: neutral-grey dummy bars (zero height) carry only the component->shade mapping
    for (col, comp_label), shade in zip(_COST_COMPONENTS, shades):
        fig.add_trace(go.Bar(
            x=[labels[0]], y=[0], name=comp_label, showlegend=True, hoverinfo="skip",
            marker=dict(color=lighten(dark_gray, shade))))

    # total LCOT label above each bar; off-scale bars (clipped by y_cap) say so explicitly
    totals = {c: sum(rows[c][col] for col, _ in _COST_COMPONENTS) * CENTS_PER_USD / denom[c]
              for c in cases}
    for label, c in zip(labels, cases):
        total = totals[c]
        over = total > y_cap
        fig.add_annotation(
            x=label, y=min(total, y_cap), yanchor="bottom", yshift=4,
            text=(f"↑ {total:.0f}" if over else f"{total:.1f}"), showarrow=False,
            font=dict(family=BRAND_FONT, size=11,
                      color=magenta_red if over else blue_black))

    fig.update_layout(
        template=fca_template, barmode="stack", bargap=0.32, showlegend=True,
        legend=dict(title_text="cost component", x=0.985, xanchor="right", y=0.98, yanchor="top"))
    fig.update_xaxes(tickangle=-30, tickfont=dict(family=BRAND_FONT, size=12))
    fig.update_yaxes(range=[0, y_cap])  # quantity/units are in the subtitle

    apply_header(fig, title=title, subtitle=subtitle,
                 fig_width=820, fig_height=540, margin_b=110)
    return save_figure(fig, out_dir, stem)


# ---- Sensitivity: Sobol index bars + lever landscape ----------------------

_INDEX_META = {"case", "target", "param", "S1", "S1_conf", "ST", "ST_conf"}


def _short_param(path: str) -> str:
    """Compact axis label from a dotted config path — its last two segments."""
    return "·".join(path.split(".")[-2:])


def _evenly(values: list, k: int) -> list:
    """Up to `k` evenly-spaced items from `values` (endpoints included)."""
    if len(values) <= k:
        return list(values)
    picks = {round(i * (len(values) - 1) / (k - 1)) for i in range(k)}
    return [values[i] for i in sorted(picks)]


def plot_sobol_indices(study_name: str, out_dir=OUT_DIR, max_panels: int = 4) -> list:
    """First-order (S1) and total (ST) Sobol indices per parameter as horizontal bars with
    bootstrap-CI whiskers — the correct-Sobol tornado (a one-at-a-time tornado is the wrong idiom
    here). With a swept axis, small-multiples across a few of its slices show how the sensitivity
    shifts with the condition. Reads results/studies/<study>/indices.csv (run the study first)."""
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    path = STUDIES_DIR / study_name / "indices.csv"
    if not path.exists():
        print(f"[skip] no indices for {study_name!r} at {path}")
        return []
    objective = pd.read_csv(path).query("target == 'objective'")
    if objective.empty:
        print(f"[skip] {study_name!r}: no objective indices (every slice infeasible?)")
        return []

    slice_cols = [c for c in objective.columns if c not in _INDEX_META]
    if slice_cols:
        dim = slice_cols[0]     # the (single) swept condition
        chosen = _evenly(sorted(objective[dim].unique()), max_panels)
        panels = [(f"{dim} = {value:,.0f}", objective[objective[dim] == value]) for value in chosen]
    else:
        panels = [("", objective)]

    fig = make_subplots(rows=1, cols=len(panels), shared_yaxes=True,
                        subplot_titles=[title for title, _ in panels], horizontal_spacing=0.05)
    for col, (_, panel) in enumerate(panels, start=1):
        panel = panel.sort_values("ST")     # ascending -> largest bar at the top
        labels = [_short_param(p) for p in panel["param"]]
        first = col == 1
        fig.add_trace(go.Bar(
            y=labels, x=panel["ST"], orientation="h", name="ST (total)", legendgroup="ST",
            showlegend=first, marker_color=light_blue,
            error_x=dict(type="data", array=panel["ST_conf"], color=dark_gray, thickness=1),
            hovertemplate="%{y}<br>ST %{x:.3f}<extra></extra>"), row=1, col=col)
        fig.add_trace(go.Bar(
            y=labels, x=panel["S1"], orientation="h", name="S1 (first-order)", legendgroup="S1",
            showlegend=first, marker_color=fca_blue,
            error_x=dict(type="data", array=panel["S1_conf"], color=dark_gray, thickness=1),
            hovertemplate="%{y}<br>S1 %{x:.3f}<extra></extra>"), row=1, col=col)

    fig.update_layout(template=fca_template, barmode="group", bargap=0.28,
                      legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.16, yanchor="top"))
    fig.update_xaxes(range=[0, 1.05])
    fig.update_yaxes(tickfont=dict(family=BRAND_FONT, size=12))
    apply_header(fig, title="Sobol sensitivity indices",
                 subtitle=f"{study_name}  ·  share of output variance (0–1), bars = bootstrap 95% CI",
                 fig_width=max(820, 250 * len(panels)) + 80, fig_height=470, margin_b=120,
                 margin_l=160)      # room for the (horizontal) parameter labels
    return save_figure(fig, out_dir, f"sobol_{study_name}")


def plot_lever_landscape(cases=("fossil", "lfp", "nuclear-cont", "tender"),
                         d_km: float = 6000.0, n: int = 25, y_cap: float = 30.0,
                         out_dir=OUT_DIR) -> list:
    """LCOT vs cruise speed at a fixed hop — the pre-collapse lever curve, each case's cost-optimal
    speed starred. Same data as the sweep plots with the axes' roles swapped: op_v_kn is retained
    (a `sweep`) instead of argmin-collapsed (`optimize`), evaluated fresh through the study path."""
    import plotly.graph_objects as go
    from config import load_assumptions, load_studies
    import schema
    from config import Study
    from compose import build_study
    from evaluate import evaluate_design

    raw, _ranges = load_assumptions(ASSUMPTIONS_PATH)
    case_specs, _studies = load_studies(STUDIES_PATH)
    fig = go.Figure()
    for case in cases:
        if case not in _DISPLAY:
            continue
        label, color, _clip = _DISPLAY[case]
        study = Study(name=f"_landscape-{case}", sample={}, fix={"shared.d_km": float(d_km)},
                      optimize=(), sweep=(schema.Axis("shared.op_v_kn", 5.0, 22.0, n, "none"),),
                      optimize_by="lcot", decompose=(), n=0, second_order=False, cases=(case,),
                      infeasible_value=None)
        ds = evaluate_design(build_study(study, raw, case_specs))[case]
        speed = ds["op_v_kn"].values
        cents = np.where(ds["feasible"].values.astype(bool), ds["lcot"].values * CENTS_PER_USD, np.nan)
        fig.add_trace(go.Scatter(
            x=speed, y=cents, mode="lines", name=label, connectgaps=False,
            line=dict(color=color, width=2.2),
            hovertemplate="v %{x:.1f} kn<br>LCOT %{y:.3f} ¢/TEU·km<extra>" + label + "</extra>"))
        if np.isfinite(cents).any():
            best = int(np.nanargmin(cents))
            fig.add_trace(go.Scatter(
                x=[speed[best]], y=[cents[best]], mode="markers", showlegend=False,
                marker=dict(symbol="star", size=13, color=color, line=dict(color="white", width=1)),
                hovertemplate=f"{label} optimum<br>v %{{x:.1f}} kn<br>"
                              "LCOT %{y:.3f} ¢/TEU·km<extra></extra>"))

    fig.update_layout(template=fca_template, hovermode="closest", showlegend=True,
                      legend=dict(x=0.985, xanchor="right", y=0.97, yanchor="top"))
    fig.update_xaxes(title_text="operating speed (kn)")
    fig.update_yaxes(range=[0, y_cap])      # quantity/units in the subtitle
    apply_header(fig, title="LCOT vs cruise speed (lever landscape)",
                 subtitle=f"US cents per TEU·km  ·  D_max = {d_km/1000:.0f},000 km  ·  ★ cost-optimal speed",
                 fig_width=820, fig_height=520, margin_b=80)
    return save_figure(fig, out_dir, "lever_landscape")


def _ensure_study(study_name: str) -> None:
    """Run `study_name` into results/studies/ if its store is missing, so `plots.py` is
    self-contained. Gated on the tidy `table.parquet` (which every study writes) rather than the
    Sobol `indices.csv` (which a pure-sweep study never has)."""
    if (STUDIES_DIR / study_name / "table.parquet").exists():
        return
    import config
    from run import run_study
    studies = {study.name: study for study in config.get_studies(ASSUMPTIONS_PATH, STUDIES_PATH)}
    if study_name in studies:
        print(f"[study] computing {study_name!r} (no store yet)")
        run_study(studies[study_name])


def main() -> None:
    _ensure_study(FLEET_STUDY)
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
    saved += plot_lever_landscape()
    for study_name in ("tender-screening", "lfp-price-check"):
        _ensure_study(study_name)
        saved += plot_sobol_indices(study_name)
    for path in saved:
        print("wrote", path)


if __name__ == "__main__":
    main()
