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

    uv run scripts/sobol_analysis.py [N]           # N defaults to SOBOL_N (256)
    uv run scripts/sobol_analysis.py [N] --all     # vary EVERY input (see below)
    uv run scripts/sobol_analysis.py [N] --groups  # vary every input, report BY LEVER

A small N (e.g. 16) is a quick wiring smoke test; a large N (512–1024) is a
careful run. Output: a per-case console table plus results/sobol_<case>.{html,png}
(the --all variant writes sobol_<case>_all.{html,png}, beside the curated ones).

Two factor spaces:
  * default — a curated, wider per-case set than the tornado bounds: the SENS_*
    lists in plots.py plus the efficiency/operational factors a global analysis
    should see. Each factor is (label, Params-field, low, high); the base value
    of every swept field lies inside its [low, high].
  * --all — EVERY Params field swept ±SOBOL_ALL_FRAC of its base (less the
    solver/scenario knobs in SOBOL_EXCLUDE_FIELDS), same set for all cases. This
    is exhaustive but blunt: structural calibration constants it includes
    (v_ref_kn, gross_slots, p_ref_kw, deadweight_t) tend to dominate via the
    cube power law and per-TEU scaling, swamping the economic uncertainties —
    which is the very reason the curated sets exist. Only the top SOBOL_ALL_TOP
    factors are tabled/plotted (the ~80-factor tail is ~0). Runs in ~1 min at
    N=256; no vectorization needed — the scalar cost path is cheap per eval.
"""

import os
import sys
from dataclasses import replace, fields

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

# ---- "vary all inputs" mode (--all) ----------------------------------------
# Instead of the curated per-case sets, sweep EVERY Params field over ±frac of
# its base value. The point is exhaustiveness: confirm which of all inputs move
# LCOT and catch any driver the curated lists missed (most land at ~0).
SOBOL_ALL_FRAC = 0.30      # ± this fraction of each base value
SOBOL_ALL_TOP = 22         # all-mode: show only the top-N factors by ST (the
                           #   full ~80-factor table/plot is unreadable; the tail is ~0)
# Solver / scenario knobs, not uncertainties — varying them changes the analysis
# setup, not an input (v_min/v_max bound the speed search; v_design_max is the
# subject of the dedicated design-speed sweep). Excluded from the all-inputs set.
SOBOL_EXCLUDE_FIELDS = {"v_min_kn", "v_max_kn", "v_design_max_kn"}
# Fields capped at 1.0 (efficiencies, availabilities, depth-of-discharge, the
# electric propulsion factors, load/usable fractions): ±frac must not exceed 1.
SOBOL_FRACTIONAL_FIELDS = {
    "load_factor", "availability", "availability_elec",
    "eta_fossil", "eta_elec", "eta_nuclear", "eta_hotel", "eta_aux_gen",
    "battery_dod", "battery_eta_charge", "battery_eta_discharge",
    "ironair_dod", "ironair_eta_charge", "ironair_eta_discharge",
    "cable_efficiency", "nucc_pool_availability", "mob_tender_availability",
    "mob_tender_eta_nuclear", "fossil_propulsion_factor", "elec_hull_form_factor",
    "elec_coating_factor", "elec_propeller_factor", "elec_wider_eff_factor",
    "elec_routing_factor", "batt_empty_usable_frac",
}


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


def all_input_factors(p: Params, frac: float = SOBOL_ALL_FRAC):
    """A factor set spanning EVERY Params field (less the solver/scenario knobs),
    each over [base·(1-frac), base·(1+frac)]. Fractional fields are clamped to
    (0, 1]; the one zero-base field (battery_min_discharge_h) has no ±% range and
    is skipped. Label = field name. Used by --all mode for all cases."""
    sens, skipped = [], []
    for f in fields(Params):
        name = f.name
        if name in SOBOL_EXCLUDE_FIELDS:
            continue
        base = getattr(p, name)
        if base == 0.0:
            skipped.append(name)
            continue
        lo, hi = sorted((base * (1 - frac), base * (1 + frac)))
        if name in SOBOL_FRACTIONAL_FIELDS:
            lo, hi = max(lo, 1e-6), min(hi, 0.999)
        sens.append((name, name, lo, hi))
    return sens, skipped


# ---- Grouping by economic lever (--groups) ---------------------------------
# Aggregate the itemized factors into the higher-level lever each one pulls, so
# Sobol reports variance share per lever (incl. within-lever interactions)
# instead of per itemized input. The model stays fully itemized; this only
# changes how indices are aggregated. SALib does it natively via problem
# ["groups"]; sampling then costs N·(num_groups+2), so it is also cheaper than
# the per-factor --all run. NOTE: without the scale reparameterization (separate
# branch) the size-coupled extensives still vary independently, so the "scale"
# and "technical" levers here are inflated by unphysical combinations.
_SCALE_FIELDS = {"gross_slots", "deadweight_t"}  # size proxies (until reparam'd)
_CAPEX_PER_KWH = {"battery_usd_per_kwh", "ironair_usd_per_kwh"}  # storage CAPEX, not energy

def _group_of(name: str) -> str:
    """Map a Params field to its economic lever (the --groups aggregation)."""
    if name in _SCALE_FIELDS:
        return "scale"
    if name in _CAPEX_PER_KWH or name.endswith("_usd_per_kw") \
            or name in ("hull_capex_usd", "mob_tender_capex_hull_usd"):
        return "capex"
    if name.endswith("_usd_per_kwh_th") \
            or name in ("fuel_usd_per_t", "elec_usd_per_kwh", "efuel_usd_per_kwh"):
        return "energy"
    if name.startswith(("om_", "crew_", "tug_")) or name.endswith("_om_other_usd_yr"):
        return "opex"
    if name == "discount_rate" or name.endswith("_life_yr") or name.endswith("cycle_life"):
        return "finance"
    if name.startswith("eta_") or name.endswith(("_eta_charge", "_eta_discharge",
            "_eta_nuclear", "_factor", "_dod")) \
            or name in ("cable_efficiency", "fuel_lhv_kwh_per_kg"):
        return "efficiency"
    if name.startswith(("availability", "port_hours")) \
            or name.endswith(("_idle_h", "_availability")) \
            or name in ("mob_port_hours_per_call", "load_factor", "load_factor_imbalance",
                        "weather_reserve", "storm_survival_duration_h",
                        "coastal_untethered_distance_nm", "mob_cable_v_cap_kn"):
        return "utilization"
    return "technical"  # power curve, hotel load, storage/reactor sizing, ISO limits, cargo mass


def _ordered_groups(groups) -> list:
    """Unique group labels in first-appearance order — the order SALib returns
    grouped indices in (so they line up with Si['S1']/['ST'])."""
    seen = []
    for g in groups:
        if g not in seen:
            seen.append(g)
    return seen


# ---- Sampling + model evaluation -------------------------------------------

def _problem(sens, groups=None) -> dict:
    """SALib problem dict for a factor set; `groups` (one label per factor, in
    the sens order) switches the analysis to per-group (lever) indices."""
    problem = {
        "num_vars": len(sens),
        "names": [field for _, field, _, _ in sens],
        "bounds": [[lo, hi] for _, _, lo, hi in sens],
    }
    if groups is not None:
        problem["groups"] = list(groups)
    return problem


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

def _print_table(case_label: str, labels, Si: dict, N: int, n_evals: int, infeas: float,
                 top: int = None, grouped: bool = False):
    """Per-case console table: entities (factors, or levers if grouped) sorted by
    ST, with S1/ST ± conf and the ST-S1 interaction term. `top` shows only the N
    most influential (the rest ~0)."""
    s1, s1c, st, stc = Si["S1"], Si["S1_conf"], Si["ST"], Si["ST_conf"]
    order = np.argsort(st)[::-1]  # most influential (highest ST) first
    shown = order if top is None else order[:top]
    head = "lever" if grouped else "factor"

    print(f"\n=== Sobol sensitivity — {case_label} ===")
    print(f"N={N}, {n_evals} model evals, D_max {SOBOL_DMAX_KM:.0f} km, "
          f"infeasible {infeas * 100:.1f}%")
    if infeas > INFEASIBLE_WARN_FRAC:
        print(f"  WARNING: {infeas * 100:.0f}% of draws infeasible — indices are "
              f"distorted by the feasibility boundary; tighten the ranges.")
    if top is not None and len(order) > top:
        print(f"  (top {top} of {len(order)} factors by ST; the rest are ~0)")
    print(f"  {head:<30}{'S1':>8}{'±':>8}{'ST':>8}{'±':>8}{'ST-S1':>9}")
    for i in shown:
        print(f"  {labels[i]:<30}{s1[i]:>8.3f}{s1c[i]:>8.3f}"
              f"{st[i]:>8.3f}{stc[i]:>8.3f}{st[i] - s1[i]:>9.3f}")


def _plot(case_label: str, stem: str, labels, Si: dict, infeas: float, out_dir: str,
          top: int = None, grouped: bool = False) -> list:
    """Grouped horizontal bars: ST (total) and S1 (first-order) per entity (factor,
    or lever if grouped), sorted by ST, with bootstrap-CI error bars. `top` keeps
    only the N largest."""
    try:
        import plotly.graph_objects as go
    except Exception as e:
        print("plot skipped:", e)
        return []

    s1, s1c, st, stc = Si["S1"], Si["S1_conf"], Si["ST"], Si["ST_conf"]
    order = np.argsort(st)  # ascending -> largest ST at the top of a horizontal bar
    if top is not None:
        order = order[-top:]  # keep the top-N largest (tail end of the ascending sort)
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

    title_suffix = " by lever" if grouped else ""
    fig.update_layout(
        template=fca_template,
        title=dict(text=f"Sobol sensitivity — {case_label}{title_suffix}", x=geom["title_x"]),
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

    subtitle = ("first-order (S1) vs total-order (ST), by economic lever" if grouped
                else "first-order (S1) vs total-order (ST)")
    fig.add_annotation(
        text=subtitle, xref="paper", yref="paper",
        x=0, xanchor="left", xshift=geom["header_x_shift"],
        y=1, yanchor="bottom", yshift=12, showarrow=False,
        font=dict(family="Titillium Web", size=16, color=blue_black),
    )
    top_note = f"top {len(y)} factors by ST — " if top is not None else ""
    fig.add_annotation(
        text=(f"{case_label}, base case at D_max {SOBOL_DMAX_KM:.0f} km — {top_note}"
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
             N: int, out_dir: str, top: int = None, groups=None) -> list:
    """Sample, evaluate, analyze, print, and plot one case. With `groups` (one
    lever label per factor) indices are aggregated per lever. Returns saved paths."""
    problem = _problem(sens, groups=groups)
    X = sobol_sample.sample(problem, N, calc_second_order=False, seed=SOBOL_SEED)
    Y = _evaluate(case_name, sens, X, p, SOBOL_DMAX_KM)
    Yi, infeas = _impute(Y)
    if not np.isfinite(Yi).any():
        print(f"\n=== Sobol sensitivity — {case_label} ===")
        print(f"  SKIPPED: all {X.shape[0]} draws infeasible at "
              f"{SOBOL_DMAX_KM:.0f} km — nothing to analyze.")
        return []
    Si = sobol_analyze.analyze(problem, Yi, calc_second_order=False, seed=SOBOL_SEED)
    grouped = groups is not None
    labels = _ordered_groups(groups) if grouped else [lbl for lbl, _, _, _ in sens]
    _print_table(case_label, labels, Si, N, X.shape[0], infeas, top=top, grouped=grouped)
    return _plot(case_label, stem, labels, Si, infeas, out_dir, top=top, grouped=grouped)


def main():
    args = sys.argv[1:]
    groups_mode = "--groups" in args
    all_mode = "--all" in args
    nums = [a for a in args if a.lstrip("-").isdigit()]
    N = int(nums[0]) if nums else SOBOL_N
    p = load_params(CONFIG_PATH)

    groups = None
    if groups_mode:
        # Broad input set, aggregated by economic lever. Same set for all cases.
        factors, skipped = all_input_factors(p)
        groups = [_group_of(field) for _, field, _, _ in factors]
        run = [(name, factors, label, f"{stem}_groups") for name, _, label, stem in CASES]
        top = None  # few levers — show them all
        print(f"Global Sobol sensitivity — BY LEVER (±{SOBOL_ALL_FRAC * 100:.0f}%), "
              f"{len(factors)} factors in {len(set(groups))} levers, Saltelli (SALib), "
              f"N={N}, D_max {SOBOL_DMAX_KM:.0f} km, speed grid {SOBOL_SPEED_N} pts")
        print(f"  levers: {', '.join(_ordered_groups(groups))}")
        print(f"  NOTE: size-coupled extensives still vary independently here "
              f"(scale reparameterization is a separate branch) — the scale/technical "
              f"levers are inflated by unphysical combinations.")
    elif all_mode:
        factors, skipped = all_input_factors(p)
        # Every case sweeps the same full input space; distinct stems so the
        # all-inputs plots sit beside (don't overwrite) the curated ones.
        run = [(name, factors, label, f"{stem}_all") for name, _, label, stem in CASES]
        top = SOBOL_ALL_TOP
        print(f"Global Sobol sensitivity — ALL INPUTS (±{SOBOL_ALL_FRAC * 100:.0f}%), "
              f"{len(factors)} factors, Saltelli (SALib), N={N}, "
              f"D_max {SOBOL_DMAX_KM:.0f} km, speed grid {SOBOL_SPEED_N} pts")
        if skipped:
            print(f"  skipped (zero base, no ±% range): {', '.join(skipped)}")
        print(f"  excluded (solver/scenario knobs): {', '.join(sorted(SOBOL_EXCLUDE_FIELDS))}")
    else:
        run = [(name, sens, label, stem) for name, sens, label, stem in CASES]
        top = None
        print(f"Global Sobol sensitivity — Saltelli sampling (SALib), N={N}, "
              f"D_max {SOBOL_DMAX_KM:.0f} km, speed grid {SOBOL_SPEED_N} pts")

    saved = []
    for case_name, sens, case_label, stem in run:
        saved += run_case(p, case_name, sens, case_label, stem, N, RESULTS_DIR,
                          top=top, groups=groups)
    for path in saved:
        print(f"Saved plot: {os.path.relpath(path, REPO_ROOT)}")


if __name__ == "__main__":
    main()
