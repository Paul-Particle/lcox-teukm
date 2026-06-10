"""
lcox-teukm — levelized cost of transport (LCOT, US$/TEU·km) for a container ship:
fossil vs battery-electric with containerized battery swapping.

Comparison axis: D_max = the longest hop between swap-capable ports (km).
This sets the battery size (hence CAPEX + displaced cargo), independent of
total route length. Everything that scales fossil and electric together
(load factor, port time, route geometry beyond D_max) is fixed to
representative values so we can read ABSOLUTE LCOT, not just the ratio.

Structure of the route in this Tier-1 cut: the ship runs back-to-back legs of
length D_max, with one combined cargo+swap port call at each end. Cycles/year
(hence utilization and annual TEU-km) therefore fall out of D_max and speed.

Speed is optimized separately for each ship: the electric ship has an extra
incentive to slow down (slower -> less energy/km -> smaller battery -> fewer
displaced slots + less CAPEX), so its economic optimum speed is lower.

All energy in kWh, power in kW, time in hours, distance in km, speed in knots.
"""

import os
from dataclasses import dataclass, field
import numpy as np

KNOT_KMH = 1.852  # 1 knot = 1.852 km/h


def crf(rate: float, years: float) -> float:
    """Capital recovery factor (annuity)."""
    years = max(years, 1e-6)
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


@dataclass
class Params:
    # ---- shared hull / route (scale both ships, fixed to representative values)
    gross_slots: float = 3000.0        # nominal hull container capacity (TEU)
    load_factor: float = 0.80          # avg fraction of available slots filled
    hull_capex_usd: float = 45e6       # newbuild hull excl. propulsion
    discount_rate: float = 0.08
    hull_life_yr: float = 25.0
    port_hours_per_call: float = 18.0  # cargo + (for electric) battery swap; assumed equal
    availability: float = 0.95         # fraction of 8760 h the ship is in service

    # ---- powertrain sizing reference (admiralty-style P ~ v^3)
    p_ref_kw: float = 20000.0          # propulsion power at v_ref
    v_ref_kn: float = 18.0
    p_hotel_kw: float = 1500.0         # constant hotel/reefer load
    v_design_max_kn: float = 22.0      # sizes the installed motor/engine
    v_min_kn: float = 9.0
    v_max_kn: float = 22.0

    # ---- conversion efficiencies
    eta_fossil: float = 0.48           # fuel chemical -> useful (good 2-stroke)
    eta_elec: float = 0.88             # battery pack -> useful (drivetrain)
    eta_charge: float = 0.95           # grid -> battery pack

    # ---- energy prices (no carbon price in base case)
    fuel_usd_per_t: float = 550.0      # VLSFO
    fuel_lhv_kwh_per_kg: float = 11.1  # ~40 MJ/kg
    elec_usd_per_kwh: float = 0.09     # delivered industrial / shore power

    # ---- fossil powertrain
    engine_usd_per_kw: float = 400.0
    engine_life_yr: float = 25.0
    om_fossil_usd_yr: float = 3.5e6    # crew, insurance, repairs, lube (ex-fuel)
    fossil_overhead_slots: float = 120.0  # engine room + bunkers, in slot-equivalents

    # ---- electric powertrain
    motor_usd_per_kw: float = 120.0
    motor_life_yr: float = 25.0
    om_elec_usd_yr: float = 3.0e6      # fewer moving parts, no fuel system
    elec_fixed_overhead_slots: float = 30.0  # compact motors only (no big engine/tanks)
    battery_usd_per_kwh: float = 250.0     # installed, marinized system level
    battery_kwh_per_teu: float = 3000.0    # energy per battery container (3 MWh/TEU)
    battery_pack_wh_per_kg: float = 160.0  # for the deadweight sanity check
    battery_dod: float = 0.90              # usable depth of discharge
    battery_reserve: float = 0.20          # weather/safety margin on top of leg energy
    battery_cycle_life: float = 4000.0
    battery_calendar_life_yr: float = 12.0


def prop_power_kw(p: Params, v_kn: float) -> float:
    return p.p_ref_kw * (v_kn / p.v_ref_kn) ** 3


def leg_useful_energy_kwh(p: Params, v_kn: float, d_km: float) -> float:
    sail_h = d_km / (v_kn * KNOT_KMH)
    return (prop_power_kw(p, v_kn) + p.p_hotel_kw) * sail_h


def cycles_per_year(p: Params, v_kn: float, d_km: float) -> float:
    sail_h = d_km / (v_kn * KNOT_KMH)
    cycle_h = sail_h + p.port_hours_per_call
    return 8760.0 * p.availability / cycle_h


def lcot_fossil(p: Params, v_kn: float, d_km: float) -> dict:
    E_use = leg_useful_energy_kwh(p, v_kn, d_km)
    cyc = cycles_per_year(p, v_kn, d_km)

    fuel_chem_kwh = E_use / p.eta_fossil
    fuel_cost_per_kwh_chem = p.fuel_usd_per_t / 1000.0 / p.fuel_lhv_kwh_per_kg
    energy_cost_leg = fuel_chem_kwh * fuel_cost_per_kwh_chem

    engine_capex = p.engine_usd_per_kw * prop_power_kw(p, p.v_design_max_kn)
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + engine_capex * crf(p.discount_rate, p.engine_life_yr)
                    + p.om_fossil_usd_yr)

    cargo_cap = p.gross_slots - p.fossil_overhead_slots
    annual_teukm = cyc * d_km * cargo_cap * p.load_factor
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan}


def lcot_elec(p: Params, v_kn: float, d_km: float) -> dict:
    E_use = leg_useful_energy_kwh(p, v_kn, d_km)
    cyc = cycles_per_year(p, v_kn, d_km)

    pack_draw_leg = E_use / p.eta_elec
    installed_kwh = pack_draw_leg * (1 + p.battery_reserve) / p.battery_dod
    battery_slots = installed_kwh / p.battery_kwh_per_teu

    cargo_cap = p.gross_slots - p.elec_fixed_overhead_slots - battery_slots
    if cargo_cap <= 0:
        return {"lcot": np.inf, "v": v_kn, "cargo_cap": cargo_cap,
                "battery_slots": battery_slots, "battery_kwh": installed_kwh,
                "battery_life": np.nan, "annual_fixed": np.inf,
                "annual_energy": np.inf, "teukm": 0.0, "cyc": cyc}

    grid_kwh = pack_draw_leg / p.eta_charge
    energy_cost_leg = grid_kwh * p.elec_usd_per_kwh

    battery_life = min(p.battery_calendar_life_yr, p.battery_cycle_life / cyc)
    motor_capex = p.motor_usd_per_kw * prop_power_kw(p, p.v_design_max_kn)
    battery_capex = p.battery_usd_per_kwh * installed_kwh
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + motor_capex * crf(p.discount_rate, p.motor_life_yr)
                    + battery_capex * crf(p.discount_rate, battery_life)
                    + p.om_elec_usd_yr)

    annual_teukm = cyc * d_km * cargo_cap * p.load_factor
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": battery_slots,
            "battery_kwh": installed_kwh, "battery_life": battery_life}


def optimize_speed(fn, p: Params, d_km: float, n=141) -> dict:
    speeds = np.linspace(p.v_min_kn, p.v_max_kn, n)
    best = None
    for v in speeds:
        r = fn(p, v, d_km)
        if best is None or r["lcot"] < best["lcot"]:
            best = r
    return best


def crossover_dmax(p: Params, d_grid) -> float:
    """Smallest D_max where electric stops being cheaper. None if electric never wins
    (or 'always' if it wins across the whole grid)."""
    diff = []
    for d in d_grid:
        f = optimize_speed(lcot_fossil, p, d)["lcot"]
        e = optimize_speed(lcot_elec, p, d)["lcot"]
        diff.append(e - f)
    diff = np.array(diff)
    elec_wins = diff < 0
    if not elec_wins.any():
        return None
    if elec_wins.all():
        return float("inf")
    # first index where it flips from winning to losing
    idx = np.where(elec_wins)[0]
    last_win = idx.max()
    if last_win + 1 < len(d_grid):
        # linear interp of the crossover between last_win and last_win+1
        d0, d1 = d_grid[last_win], d_grid[last_win + 1]
        y0, y1 = diff[last_win], diff[last_win + 1]
        return float(d0 + (d1 - d0) * (0 - y0) / (y1 - y0))
    return float(d_grid[last_win])


def main():
    p = Params()
    d_grid = np.linspace(100, 6000, 80)

    print("=" * 72)
    print("BASE CASE  (no carbon price)")
    print(f"  fuel ${p.fuel_usd_per_t}/t  |  elec ${p.elec_usd_per_kwh}/kWh  "
          f"|  battery ${p.battery_usd_per_kwh}/kWh  |  hull {p.gross_slots:.0f} TEU")
    print("=" * 72)

    # useful-energy cost per kWh, head to head
    fuel_useful = (p.fuel_usd_per_t/1000/p.fuel_lhv_kwh_per_kg)/p.eta_fossil
    elec_useful = p.elec_usd_per_kwh/p.eta_charge/p.eta_elec
    print(f"\nEnergy cost per USEFUL kWh:  fossil ${fuel_useful:.3f}   "
          f"electric ${elec_useful:.3f}   "
          f"({'electric cheaper' if elec_useful < fuel_useful else 'fossil cheaper'})")

    print("\nBreakdown at sample hop lengths (speed optimized per ship):")
    hdr = (f"{'D_max':>7} {'ship':>8} {'v_opt':>6} {'LCOT':>9} "
           f"{'$fixed':>8} {'$energy':>8} {'cargo':>6} {'batt_TEU':>9} "
           f"{'batt_MWh':>9} {'batt_yr':>7}")
    print(hdr); print("-" * len(hdr))
    for d in [200, 500, 1000, 2000, 4000]:
        for name, fn in [("fossil", lcot_fossil), ("electric", lcot_elec)]:
            r = optimize_speed(fn, p, d)
            fixed_share = r["annual_fixed"] / (r["annual_fixed"] + r["annual_energy"]) if np.isfinite(r["lcot"]) else float('nan')
            energy_share = 1 - fixed_share if np.isfinite(r["lcot"]) else float('nan')
            print(f"{d:>7.0f} {name:>8} {r['v']:>6.1f} "
                  f"{r['lcot']*100:>8.3f}c "
                  f"{fixed_share*100:>7.0f}% {energy_share*100:>7.0f}% "
                  f"{r['cargo_cap']:>6.0f} {r['battery_slots']:>9.0f} "
                  f"{r['battery_kwh']/1000:>9.0f} {r['battery_life']:>7.1f}")

    co = crossover_dmax(p, d_grid)
    print("\nCrossover D_max (electric cheaper below this):",
          "electric never cheaper in base case" if co is None
          else f"{co:.0f} km" if np.isfinite(co) else "electric always cheaper")

    # ---- sensitivity: where does a crossover appear?
    print("\n" + "=" * 72)
    print("SENSITIVITY: crossover D_max (km) vs battery cost & electricity price")
    print("=" * 72)
    batt_costs = [250, 150, 80]
    elec_prices = [0.09, 0.06, 0.03]
    print(f"{'':>14}" + "".join(f"  elec ${e:>4.2f}" for e in elec_prices))
    for bc in batt_costs:
        row = f"batt ${bc:>3}/kWh "
        for ep in elec_prices:
            pp = Params(battery_usd_per_kwh=bc, elec_usd_per_kwh=ep)
            c = crossover_dmax(pp, d_grid)
            cell = "none" if c is None else (">6000" if np.isinf(c) else f"{c:.0f}")
            row += f"  {cell:>9}"
        print(row)

    # ---- plot base-case LCOT vs D_max
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        dd = np.linspace(100, 6000, 120)
        lf = [optimize_speed(lcot_fossil, p, d)["lcot"] * 100 for d in dd]
        le = [min(optimize_speed(lcot_elec, p, d)["lcot"] * 100, 50) for d in dd]
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(dd, lf, label="fossil", lw=2.2, color="#444")
        ax.plot(dd, le, label="battery-electric", lw=2.2, color="#1f77b4")
        ax.set_xlabel("D_max  —  longest hop between swap ports (km)")
        ax.set_ylabel("LCOT (US cents per TEU·km)")
        ax.set_title("Levelized cost of transport vs inter-swap distance\n"
                     f"(base case, no carbon price, battery ${p.battery_usd_per_kwh}/kWh, "
                     f"elec ${p.elec_usd_per_kwh}/kWh)")
        ax.set_ylim(0, max(max(lf), 8) * 1.3)
        ax.grid(alpha=0.3); ax.legend()
        fig.tight_layout()
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out_dir = os.path.join(repo_root, "results")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "lcot_vs_dmax.png")
        fig.savefig(out_path, dpi=130)
        print(f"\nSaved plot: {os.path.relpath(out_path, repo_root)}")
    except Exception as e:
        print("plot skipped:", e)


if __name__ == "__main__":
    main()
