"""
sobol_analysis.py — global (variance-based) sensitivity via Sobol indices.

The tornados in plots.py move one parameter at a time, so they cannot see how
parameters interact or apportion the *variance* of LCOT to its inputs. This
module does the global complement: for each headline case it samples the full
joint uncertainty space (Saltelli quasi-random sampling, via SALib) and computes
Sobol indices —

    S1  first-order: the share of LCOT variance from a parameter *on its own*
    ST  total-order: its share including every interaction it takes part in

so ST - S1 is the interaction effect the tornados miss. Bootstrap confidence
intervals come for free from SALib (S1_conf / ST_conf).

This is far heavier than the tornados (N·(D+2) model evaluations per case, each
an inner speed optimization), so it is a SEPARATE on-demand entry point — it is
NOT wired into run.py. Run it directly:

    uv run scripts/sobol_analysis.py [N]      # N defaults to SOBOL_N (256)

A small N (e.g. 16) is a quick wiring smoke test; a large N (512–1024) is a
careful run. Output: a per-case console table plus results/sobol_<case>.{html,png}.

Factor space: a curated, wider per-case set than the tornado bounds — the
SENS_* lists in plots.py plus the efficiency/operational factors a global
analysis should see. Each factor is (label, Params-field, low, high); the base
value of every swept field lies inside its [low, high].
"""

import os
import sys
from dataclasses import replace

import numpy as np

from SALib.sample import sobol as sobol_sample
from SALib.analyze import sobol as sobol_analyze

from params import Params, load_params
from cases import cases_by_name
from cost import cost_fn
from analysis import optimize_speed
from units import CENTS_PER_USD
from style import (
    fca_template, fca_blue, sand_yellow, blue_black, dark_gray,
    header_geometry, apply_dot, apply_logo, add_trace_label,
)
from plots import _save_html_png

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.yaml")
RESULTS_DIR = os.path.join(REPO_ROOT, "results")

# ---- Analysis settings -----------------------------------------------------
SOBOL_N = 256              # base sample size; Saltelli draws N·(D+2) rows
SOBOL_SPEED_N = 81         # inner speed-grid resolution (coarser than the 141
                           #   default — the optimum LCOT is insensitive at this
                           #   level, and Sobol consumes only the ranking)
SOBOL_DMAX_KM = 1000.0     # same hop the tornados use, for comparability
SOBOL_SEED = 20240611      # fixed -> reproducible samples + bootstrap CIs
INFEASIBLE_PENALTY_MULT = 3.0   # infeasible draws -> max(finite)·this
INFEASIBLE_WARN_FRAC = 0.05     # warn (indices distorted) above this fraction


# ---- Curated per-case factor sets: (label, field, low, high) ---------------
# Wider than the plots.py SENS_* lists: each starts from that case's tornado
# factors and adds the efficiency/operational uncertainties global analysis
# should resolve. Base value of every field is inside [low, high].

SOBOL_LFP = [
    ("battery cost ($/kWh)",             "battery_usd_per_kwh",    80,    350),
    ("electricity price ($/kWh)",        "elec_usd_per_kwh",       0.03,  0.15),
    ("battery energy density (kWh/TEU)", "battery_kwh_per_teu",    2000,  5000),
    ("discount rate",                    "discount_rate",          0.05,  0.12),
    ("load factor",                      "load_factor",            0.65,  0.95),
    ("hull CAPEX ($)",                   "hull_capex_usd",         30e6,  60e6),
    ("non-crew O&M ($/yr)",              "om_elec_other_usd_yr",   0.6e6, 2e6),
    ("battery cycle life",               "battery_cycle_life",     2000,  6000),
    ("charge eff",                       "battery_eta_charge",     0.92,  0.99),
    ("discharge eff",                    "battery_eta_discharge",  0.94,  0.99),
    ("empty-slot usable frac",           "batt_empty_usable_frac", 0.20,  0.80),
    ("weather reserve",                  "weather_reserve",        0.10,  0.30),
    ("pack density (Wh/kg)",             "battery_pack_wh_per_kg", 90,    180),
]
SOBOL_IRONAIR = [
    ("pack density (Wh/kg)",          "ironair_pack_wh_per_kg",   20,    60),
    ("iron-air cost ($/kWh)",         "ironair_usd_per_kwh",      20,    60),
    ("energy density (kWh/TEU)",      "ironair_kwh_per_teu",      1000,  2500),
    ("charge eff",                    "ironair_eta_charge",       0.45,  0.70),
    ("discharge eff",                 "ironair_eta_discharge",    0.70,  0.90),
    ("depth of discharge",            "ironair_dod",              0.85,  0.98),
    ("calendar life (yr)",            "ironair_calendar_life_yr", 12,    25),
    ("electricity price ($/kWh)",     "elec_usd_per_kwh",         0.03,  0.15),
    ("discount rate",                 "discount_rate",            0.05,  0.12),
    ("load factor",                   "load_factor",              0.65,  0.95),
    ("hull CAPEX ($)",                "hull_capex_usd",           30e6,  60e6),
]
SOBOL_MOBILE = [
    ("tender reactor CAPEX ($/kW)",   "mob_tender_usd_per_kw",          4000,  20000),
    ("tender idle / escort (h)",      "tender_idle_h",                  2,     12),
    ("coastal untethered dist. (nm)", "coastal_untethered_distance_nm", 12,    200),
    ("tender reactor size (kWe)",     "mob_tender_reactor_kw",          15000, 45000),
    ("storm survival (h)",            "storm_survival_duration_h",      6,     24),
    ("tender O&M ($/yr)",             "mob_tender_om_other_usd_yr",     2e6,   10e6),
    ("tender hull CAPEX ($)",         "mob_tender_capex_hull_usd",      30e6,  100e6),
    ("cable efficiency",              "cable_efficiency",               0.90,  0.99),
    ("tender reactor->elec eff",      "mob_tender_eta_nuclear",         0.35,  0.55),
    ("cable speed cap (kn)",          "mob_cable_v_cap_kn",             12,    20),
    ("discount rate",                 "discount_rate",                  0.05,  0.12),
    ("load factor",                   "load_factor",                    0.65,  0.95),
]
SOBOL_NUCELEC = [
    ("reactor CAPEX ($/kW)",          "nucc_usd_per_kw",           4000,  20000),
    ("reactor life (yr)",             "nucc_life_yr",              10,    30),
    ("overhead density (TEU/MWe)",    "nucc_overhead_teu_per_mwe", 0.8,   2.0),
    ("non-crew O&M ($/yr)",           "nucc_om_other_usd_yr",      3e6,   8e6),
    ("reactor->elec eff",             "eta_nuclear",               0.25,  0.40),
    ("HALEU fuel ($/kWh_th)",         "nucc_fuel_usd_per_kwh_th",  0.006, 0.020),
    ("nuclear crew count",            "crew_count_nuclear",        25,    35),
    ("crew cost ($/yr)",              "crew_cost_usd_yr",          70000, 120000),
    ("hull CAPEX ($)",                "hull_capex_usd",            30e6,  60e6),
    ("discount rate",                 "discount_rate",             0.05,  0.12),
    ("load factor",                   "load_factor",               0.65,  0.95),
]
SOBOL_NUCLEASE = [
    ("reactor CAPEX ($/kW)",          "nucc_usd_per_kw",           4000,  20000),
    ("pool idle / assignment (h)",    "nucc_pool_idle_h",          2,     18),
    ("reactor pool availability",     "nucc_pool_availability",    0.80,  0.98),
    ("reactor life (yr)",             "nucc_life_yr",              10,    30),
    ("overhead density (TEU/MWe)",    "nucc_overhead_teu_per_mwe", 0.8,   2.0),
    ("reactor->elec eff",             "eta_nuclear",               0.25,  0.40),
    ("HALEU fuel ($/kWh_th)",         "nucc_fuel_usd_per_kwh_th",  0.006, 0.020),
    ("nuclear crew count",            "crew_count_nuclear",        25,    35),
    ("hull CAPEX ($)",                "hull_capex_usd",            30e6,  60e6),
    ("discount rate",                 "discount_rate",             0.05,  0.12),
    ("load factor",                   "load_factor",               0.65,  0.95),
]

# (case name, factor set, plot/title label, output stem) — stems match the
# tornados (sobol_<x> alongside tornado_<x>).
CASES = [
    ("lfp",      SOBOL_LFP,      "battery-electric (LFP)",           "sobol_lfp"),
    ("iron-air", SOBOL_IRONAIR,  "battery-electric (iron-air)",      "sobol_ironair"),
    ("mobile",   SOBOL_MOBILE,   "mobile-reactor charge",            "sobol_mobile"),
    ("nuc-ec",   SOBOL_NUCELEC,  "nuclear-electric (containerized)", "sobol_nucelec"),
    ("nuc-el",   SOBOL_NUCLEASE, "nuclear-electric (leased)",        "sobol_nuclease"),
]


# ---- Sampling + model evaluation -------------------------------------------

def _problem(sens) -> dict:
    """SALib problem dict for a factor set."""
    return {
        "num_vars": len(sens),
        "names": [field for _, field, _, _ in sens],
        "bounds": [[lo, hi] for _, _, lo, hi in sens],
    }


def _evaluate(case_name: str, sens, X: np.ndarray, p: Params, d_km: float) -> np.ndarray:
    """LCOT (cents/TEU·km) at each Saltelli sample row. Each row perturbs the
    factor fields, rebuilds the case from the modified Params (case scalars derive
    from p), and reads the speed-optimized LCOT. Non-finite where infeasible."""
    fields = [field for _, field, _, _ in sens]
    Y = np.empty(X.shape[0])
    for i in range(X.shape[0]):
        pp = replace(p, **{field: float(X[i, j]) for j, field in enumerate(fields)})
        r = optimize_speed(cost_fn(cases_by_name(pp)[case_name]), pp, d_km, n=SOBOL_SPEED_N)
        Y[i] = r["lcot"] * CENTS_PER_USD
    return Y


def _impute(Y: np.ndarray):
    """Replace non-finite (infeasible) outputs with a penalty so Sobol can run.
    Returns (imputed Y, infeasible fraction). Penalty = max finite × mult, which
    keeps infeasible draws 'expensive' without an arbitrary absolute number."""
    finite = np.isfinite(Y)
    frac = 1.0 - float(finite.mean())
    if not finite.any():
        return np.full_like(Y, np.nan), frac
    penalty = Y[finite].max() * INFEASIBLE_PENALTY_MULT
    return np.where(finite, Y, penalty), frac


# ---- Reporting -------------------------------------------------------------

def _print_table(case_label: str, sens, Si: dict, N: int, n_evals: int, infeas: float):
    """Per-case console table: factors sorted by ST, with S1/ST ± conf and the
    ST-S1 interaction term."""
    labels = [lbl for lbl, _, _, _ in sens]
    s1, s1c, st, stc = Si["S1"], Si["S1_conf"], Si["ST"], Si["ST_conf"]
    order = np.argsort(st)[::-1]  # most influential (highest ST) first

    print(f"\n=== Sobol sensitivity — {case_label} ===")
    print(f"N={N}, {n_evals} model evals, D_max {SOBOL_DMAX_KM:.0f} km, "
          f"infeasible {infeas * 100:.1f}%")
    if infeas > INFEASIBLE_WARN_FRAC:
        print(f"  WARNING: {infeas * 100:.0f}% of draws infeasible — indices are "
              f"distorted by the feasibility boundary; tighten this case's ranges.")
    print(f"  {'factor':<30}{'S1':>8}{'±':>8}{'ST':>8}{'±':>8}{'ST-S1':>9}")
    for i in order:
        print(f"  {labels[i]:<30}{s1[i]:>8.3f}{s1c[i]:>8.3f}"
              f"{st[i]:>8.3f}{stc[i]:>8.3f}{st[i] - s1[i]:>9.3f}")


def _plot(case_label: str, stem: str, sens, Si: dict, infeas: float, out_dir: str) -> list:
    """Grouped horizontal bars: ST (total) and S1 (first-order) per factor,
    sorted by ST, with bootstrap-CI error bars."""
    try:
        import plotly.graph_objects as go
    except Exception as e:
        print("plot skipped:", e)
        return []

    labels = [lbl for lbl, _, _, _ in sens]
    s1, s1c, st, stc = Si["S1"], Si["S1_conf"], Si["ST"], Si["ST_conf"]
    order = np.argsort(st)  # ascending -> largest ST at the top of a horizontal bar
    y = [labels[i] for i in order]
    s1, s1c = [s1[i] for i in order], [s1c[i] for i in order]
    st, stc = [st[i] for i in order], [stc[i] for i in order]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="ST (total)", y=y, x=st, orientation="h",
        marker_color=sand_yellow, opacity=0.9,
        error_x=dict(type="data", array=stc, visible=True, color=blue_black, thickness=1.2),
        hovertemplate="%{y}<br>ST %{x:.3f} (total, incl. interactions)<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="S1 (first-order)", y=y, x=s1, orientation="h",
        marker_color=fca_blue, opacity=0.9,
        error_x=dict(type="data", array=s1c, visible=True, color=blue_black, thickness=1.2),
        hovertemplate="%{y}<br>S1 %{x:.3f} (first-order)<extra></extra>",
    ))

    fig_width = 820
    fig_height = 320 + 30 * len(y)
    margin_l_val, margin_r_val, margin_t_val, margin_b = 260, 60, 96, 110
    geom = header_geometry(fig_width, fig_height, margin_l_val, margin_r_val, margin_t_val)

    fig.update_layout(
        template=fca_template,
        title=dict(text=f"Sobol sensitivity — {case_label}", x=geom["title_x"]),
        barmode="group",
        bargap=0.25, bargroupgap=0.1,
        showlegend=False,
        hovermode="y unified",
        margin=dict(l=margin_l_val, r=margin_r_val, t=margin_t_val, b=margin_b),
        width=fig_width, height=fig_height,
    )
    fig.update_xaxes(
        title_text="Sobol index (share of LCOT variance)",
        zeroline=True, zerolinecolor=blue_black, zerolinewidth=1.5,
        rangemode="tozero",
    )
    fig.update_yaxes(showgrid=False)

    fig.add_annotation(
        text="first-order (S1) vs total-order (ST)", xref="paper", yref="paper",
        x=0, xanchor="left", xshift=geom["header_x_shift"],
        y=1, yanchor="bottom", yshift=12, showarrow=False,
        font=dict(family="Titillium Web", size=16, color=blue_black),
    )
    fig.add_annotation(
        text=(f"{case_label}, base case at D_max {SOBOL_DMAX_KM:.0f} km — "
              f"ST&#8722;S1 is the interaction effect; {infeas * 100:.0f}% of draws infeasible"),
        xref="paper", yref="paper", x=0, xanchor="left",
        xshift=geom["header_x_shift"], y=0, yanchor="top", yshift=-72, showarrow=False,
        font=dict(family="Titillium Web", size=11, color=dark_gray),
    )

    apply_dot(fig, geom)
    add_trace_label(fig, "ST (total)",       sand_yellow, x=0.97, y=0.96)
    add_trace_label(fig, "S1 (first-order)", fca_blue,    x=0.97, y=0.88)
    apply_logo(fig, fig_width, fig_height, margin_l_val, margin_r_val, margin_t_val, margin_b)

    return _save_html_png(fig, out_dir, stem)


# ---- Per-case driver -------------------------------------------------------

def run_case(p: Params, case_name: str, sens, case_label: str, stem: str,
             N: int, out_dir: str) -> list:
    """Sample, evaluate, analyze, print, and plot one case. Returns saved paths."""
    problem = _problem(sens)
    X = sobol_sample.sample(problem, N, calc_second_order=False, seed=SOBOL_SEED)
    Y = _evaluate(case_name, sens, X, p, SOBOL_DMAX_KM)
    Yi, infeas = _impute(Y)
    if not np.isfinite(Yi).any():
        print(f"\n=== Sobol sensitivity — {case_label} ===")
        print(f"  SKIPPED: all {X.shape[0]} draws infeasible at "
              f"{SOBOL_DMAX_KM:.0f} km — nothing to analyze.")
        return []
    Si = sobol_analyze.analyze(problem, Yi, calc_second_order=False, seed=SOBOL_SEED)
    _print_table(case_label, sens, Si, N, X.shape[0], infeas)
    return _plot(case_label, stem, sens, Si, infeas, out_dir)


def main():
    N = int(sys.argv[1]) if len(sys.argv) > 1 else SOBOL_N
    p = load_params(CONFIG_PATH)
    print(f"Global Sobol sensitivity — Saltelli sampling (SALib), N={N}, "
          f"D_max {SOBOL_DMAX_KM:.0f} km, speed grid {SOBOL_SPEED_N} pts")
    saved = []
    for case_name, sens, case_label, stem in CASES:
        saved += run_case(p, case_name, sens, case_label, stem, N, RESULTS_DIR)
    for path in saved:
        print(f"Saved plot: {os.path.relpath(path, REPO_ROOT)}")


if __name__ == "__main__":
    main()
