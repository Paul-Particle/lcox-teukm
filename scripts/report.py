"""
report.py — console-table output for the model results.

All number formatting and unit-for-display conversions live here so the model
modules stay free of presentation concerns. The set of powertrains shown comes
from `cases.build_cases`; cost is computed through `cost.levelized_cost` (bound
per case with `cost.cost_fn`); plotting lives in plots.py.
"""

from dataclasses import replace

import numpy as np

from params import Params
from cases import build_cases, cases_by_name
from cost import cost_fn
from analysis import optimize_speed, crossover_dmax
from units import CENTS_PER_USD, PERCENT_PER_FRACTION, KWH_PER_MWH, KG_PER_TONNE

# Sample hop lengths (km) shown in the per-ship breakdown table.
SAMPLE_HOPS_KM = [200, 500, 1000, 2000, 4000]

# Sensitivity sweep axes (LFP battery cost x electricity price).
SENS_BATTERY_USD_PER_KWH = [250, 150, 80]
SENS_ELEC_USD_PER_KWH = [0.09, 0.06, 0.03]

# Hotel/reefer load scenarios (kW) and the D_max at which they are evaluated.
# Reefer power is the big variable part of hotel load; reefer-heavy ~ hundreds
# of reefer plugs at a few kW each, reefer-light ~ base ship systems only.
SENS_HOTEL_KW = [("reefer-light", 1000), ("base", 1500), ("reefer-heavy", 3000)]
SENS_HOTEL_DMAX_KM = 1000

# Design/service-speed sweep for the reactor cases (knots) and its D_max.
SENS_DESIGN_SPEED_KN = [8, 10, 12, 14, 16, 18, 20, 22]
SENS_DESIGN_DMAX_KM = 2000


def print_base_header(p: Params) -> None:
    print("=" * 72)
    print("BASE CASE")
    print(f"  fuel ${p.fuel_usd_per_t}/t  |  elec ${p.elec_usd_per_kwh}/kWh  "
          f"|  LFP ${p.battery_usd_per_kwh}/kWh  |  hull {p.gross_slots:.0f} TEU")
    print(f"  iron-air ${p.ironair_usd_per_kwh}/kWh @ "
          f"{p.ironair_eta_charge*p.ironair_eta_discharge*PERCENT_PER_FRACTION:.0f}% RTE  "
          f"|  SMR ${p.nuclear_usd_per_kw:.0f}/kW")
    print("=" * 72)


def print_energy_cost(p: Params) -> None:
    """Useful-energy cost per kWh, all powertrains head to head."""
    costs = {
        "fossil": (p.fuel_usd_per_t / KG_PER_TONNE / p.fuel_lhv_kwh_per_kg)
                  / p.eta_fossil,
        "e-methanol": p.efuel_usd_per_kwh / p.eta_fossil,
        "lfp": p.elec_usd_per_kwh / (p.battery_eta_charge * p.battery_eta_discharge * p.eta_elec),
        "iron-air": p.elec_usd_per_kwh / (p.ironair_eta_charge * p.ironair_eta_discharge * p.eta_elec),
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
        for case in build_cases(p):
            name = case.name
            r = optimize_speed(cost_fn(case), p, d)
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
    fossil = cases_by_name(p)["fossil"]
    for case in build_cases(p):
        if not case.clip:  # the battery cases are the ones with a D_max crossover
            continue
        co = crossover_dmax(p, d_grid, cost_fn(case), cost_fn(fossil))
        msg = ("never cheaper in base case" if co is None
               else f"cheaper than fossil below {co:.0f} km" if np.isfinite(co)
               else "always cheaper than fossil")
        print(f"Crossover D_max: {case.name} {msg}")


def print_sensitivity(p: Params, d_grid) -> None:
    """LFP-vs-fossil crossover D_max vs battery cost and electricity price.
    (Iron-air and nuclear axes are out of scope for this table.)"""
    print("\n" + "=" * 72)
    print("SENSITIVITY: LFP crossover D_max (km) vs battery cost & elec price")
    print("=" * 72)
    print(f"{'':>14}" + "".join(f"  elec ${e:>4.2f}" for e in SENS_ELEC_USD_PER_KWH))
    for bc in SENS_BATTERY_USD_PER_KWH:
        row = f"batt ${bc:>3}/kWh "
        for ep in SENS_ELEC_USD_PER_KWH:
            pp = replace(p, battery_usd_per_kwh=bc, elec_usd_per_kwh=ep)
            cm = cases_by_name(pp)
            c = crossover_dmax(pp, d_grid, cost_fn(cm["lfp"]), cost_fn(cm["fossil"]))
            cell = "none" if c is None else (">6000" if np.isinf(c) else f"{c:.0f}")
            row += f"  {cell:>9}"
        print(row)


def print_hotel_sensitivity(p: Params, d_grid) -> None:
    """Hotel/reefer load sensitivity. Reefer power is the large, variable part
    of hotel load, and on a battery ship it is drawn from the (slot-displacing)
    battery, so reefer-heavy routes penalize the battery ships far more than
    fossil. Shows LCOT at a representative D_max plus the LFP crossover.
    A faithful model would couple reefer load to carried cargo and credit
    reefer revenue (reefers are high-value) — out of scope here (see TODO.md)."""
    d = SENS_HOTEL_DMAX_KM
    print("\n" + "=" * 72)
    print(f"SENSITIVITY: hotel/reefer load — LCOT (c/TEU·km) at D_max {d:.0f} km")
    print("=" * 72)
    print(f"{'':>20}{'fossil':>9}{'lfp':>9}{'iron-air':>9}{'lfp x-over':>15}")
    for label, h in SENS_HOTEL_KW:
        pp = replace(p, p_hotel_kw=h)
        cm = cases_by_name(pp)
        lf = optimize_speed(cost_fn(cm["fossil"]),   pp, d)["lcot"] * CENTS_PER_USD
        le = optimize_speed(cost_fn(cm["lfp"]),      pp, d)["lcot"] * CENTS_PER_USD
        li = optimize_speed(cost_fn(cm["iron-air"]), pp, d)["lcot"] * CENTS_PER_USD
        co = crossover_dmax(pp, d_grid, cost_fn(cm["lfp"]), cost_fn(cm["fossil"]))
        cox = "none" if co is None else (">6000" if np.isinf(co) else f"{co:.0f} km")
        tag = f"{label} {h:>4} kW"
        print(f"{tag:>20}{lf:>8.3f}c{le:>8.3f}c{li:>8.3f}c{cox:>15}")


def print_mobile_fleet(p: Params) -> None:
    """Mobile nuclear tender fleet economics, surfaced (not buried in LCOT):
    the service $/kWh that prices each leg, at sample hop lengths. `ships/tender`
    is a face-validity diagnostic (>=1 means one dedicated tender keeps pace); it
    does not feed back into LCOT — energy is priced as a per-kWh service."""
    print("\n" + "=" * 72)
    print("MOBILE TENDER FLEET (at-sea charging economics)")
    print("=" * 72)
    print(f"{'D_max':>7} {'v_opt':>6} {'batt_MWh':>9} {'$/kWh deliv':>12} "
          f"{'ships/tender*':>13} {'LCOT':>9}")
    mobile = cases_by_name(p)["mobile"]
    for d in SAMPLE_HOPS_KM:
        r = optimize_speed(cost_fn(mobile), p, d)
        if not np.isfinite(r["lcot"]):
            print(f"{d:>7.0f} {'—':>6} {'—':>9} {'—':>12} {'—':>13} {'infeasible':>9}")
            continue
        print(f"{d:>7.0f} {r['v']:>6.1f} {r['battery_kwh']/KWH_PER_MWH:>9.0f} "
              f"{'$'+format(r['tender_usd_per_kwh'],'.3f'):>12} {r['ships_per_tender']:>13.1f} "
              f"{r['lcot']*CENTS_PER_USD:>8.3f}c")
    print("  * diagnostic only: tender priced as a per-kWh service, not a fleet ratio")


def print_reactor_lease(p: Params) -> None:
    """Leased containerized nuclear-electric: the reactor-as-a-service $/kWh that
    prices each leg, at sample hop lengths. `ships/reactor` is a face-validity
    diagnostic (>1 means one pooled reactor powers several ships, the pooling
    leverage); it does not feed back into LCOT — the reactor is priced per kWh."""
    print("\n" + "=" * 72)
    print("REACTOR LEASE POOL (containerized nuclear-electric, as-a-service)")
    print("=" * 72)
    print(f"{'D_max':>7} {'v_opt':>6} {'$/kWh lease':>12} {'ships/reactor*':>15} {'LCOT':>9}")
    leased = cases_by_name(p)["nuc-el"]
    for d in SAMPLE_HOPS_KM:
        r = optimize_speed(cost_fn(leased), p, d)
        if not np.isfinite(r["lcot"]):
            print(f"{d:>7.0f} {'—':>6} {'—':>12} {'—':>15} {'infeasible':>9}")
            continue
        print(f"{d:>7.0f} {r['v']:>6.1f} "
              f"{'$'+format(r['lease_usd_per_kwh'],'.3f'):>12} {r['ships_per_reactor']:>15.1f} "
              f"{r['lcot']*CENTS_PER_USD:>8.3f}c")
    print("  * diagnostic only: reactor priced as a per-kWh service, not a fleet ratio")


def print_design_speed_sweep(p: Params) -> None:
    """Reactor-case LCOT vs *service* speed. The model sizes installed power at
    v_design_max but optimizes cruise separately — the fossil slow-steaming
    paradigm, which wastes reactor CAPEX (∝ power ∝ v³) when a ~free-fuel ship
    cruises slower than its plant. Here design speed and the cruise cap are coupled
    (size for the service speed, then sail it), exposing each reactor case's own
    size/speed optimum that the fixed 22 kn design hides. (Tier-1: the ~15%
    sea/weather power margin on top of service speed is not modeled.)"""
    d = SENS_DESIGN_DMAX_KM
    targets = ["nuclear", "nuc-ec", "nuc-ei"]
    print("\n" + "=" * 72)
    print(f"DESIGN-SPEED SWEEP: reactor LCOT (c/TEU·km) vs service speed, D_max {d:.0f} km")
    print("=" * 72)
    print(f"{'v_service':>10}" + "".join(f"{t:>12}" for t in targets))
    for vd in SENS_DESIGN_SPEED_KN:
        pp = replace(p, v_design_max_kn=vd, v_max_kn=vd)
        cm = cases_by_name(pp)
        cells = ""
        for t in targets:
            r = optimize_speed(cost_fn(cm[t]), pp, d)
            cells += (f"{r['lcot']*CENTS_PER_USD:>11.3f}c" if np.isfinite(r["lcot"])
                      else f"{'—':>12}")
        print(f"{vd:>8.0f}kn{cells}")


def print_report(p: Params) -> None:
    """The full console report, in order. Single source of the print sequence so
    run.py (live) and regression_check.py (golden) can't drift apart."""
    d_grid = np.linspace(100, 6000, 80)
    print_base_header(p)
    print_energy_cost(p)
    print_breakdown(p)
    print_crossover(p, d_grid)
    print_sensitivity(p, d_grid)
    print_hotel_sensitivity(p, d_grid)
    print_mobile_fleet(p)
    print_reactor_lease(p)
    print_design_speed_sweep(p)
