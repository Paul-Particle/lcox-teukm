"""
lcox-teukm — levelized cost of transport (LCOT, US$/TEU·km) for a container
ship across powertrains: fossil; battery-electric (Li-ion and iron-air, port
swap); onboard nuclear (direct-drive SMR); nuclear-electric (reactor -> motor,
containerized or integrated); and a battery ship recharged at sea by a mobile
nuclear tender.

Comparison axis: D_max = the longest hop between swap-capable ports (km).
This sets the battery size (hence CAPEX + displaced cargo), independent of
total route length. Everything that scales the ships together (load factor,
port time, route geometry beyond D_max) is fixed to representative values so
we can read ABSOLUTE LCOT, not just ratios.

Structure of the route in this Tier-1 cut: the ship runs back-to-back legs of
length D_max, with one combined cargo+swap port call at each end. Cycles/year
(hence utilization and annual TEU-km) therefore fall out of D_max and speed.

Speed is optimized separately for each ship. The battery ships have an extra
incentive to slow down (slower -> less energy/km -> smaller battery -> fewer
displaced slots + less CAPEX); iron-air's 100-h discharge rating makes its
pack power-bound (installed kWh >= peak kW x 100 h), pinning it near minimum
speed. The nuclear ship is the opposite: cheap fuel and expensive capital push
it to maximum speed, and its LCOT depends on D_max only through port-call
frequency.

This file is the entry point only. The model is split across sibling modules:
    units.py     unit conversions (single source of truth)
    params.py    Params schema + load_params(config.yaml)
    finance.py   capital recovery factor
    energy.py    ship physics (power, leg energy, cycles/year)
    lcot.py      the cost models: fossil, Li-ion & iron-air battery (port swap),
                 onboard nuclear (direct-drive), two nuclear-electric variants
                 (containerized / integrated), and a battery ship charged at sea
                 by a mobile nuclear tender. carried_teu now applies volume, mass
                 and asymmetric-leg constraints.
    analysis.py  speed optimization + crossover distance
    report.py    console tables + plotting

All energy in kWh, power in kW, time in hours, distance in km, speed in knots.
"""

import os

import numpy as np

from params import load_params
from report import (print_base_header, print_energy_cost, print_breakdown,
                    print_crossover, print_sensitivity, print_hotel_sensitivity,
                    print_mobile_fleet, plot_lcot_vs_dmax, plot_speed_vs_dmax,
                    plot_lcot_tornado, plot_teu_tech_tradeoff)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.yaml")
RESULTS_DIR = os.path.join(REPO_ROOT, "results")


def main():
    p = load_params(CONFIG_PATH)
    d_grid = np.linspace(100, 6000, 80)

    print_base_header(p)
    print_energy_cost(p)
    print_breakdown(p)
    print_crossover(p, d_grid)
    print_sensitivity(p, d_grid)
    print_hotel_sensitivity(p, d_grid)
    print_mobile_fleet(p)

    saved = plot_lcot_vs_dmax(p, RESULTS_DIR)
    saved += plot_speed_vs_dmax(p, RESULTS_DIR)
    saved += plot_lcot_tornado(p, 500, RESULTS_DIR)
    saved += plot_teu_tech_tradeoff(p, 500, RESULTS_DIR)
    for path in saved:
        print(f"Saved plot: {os.path.relpath(path, REPO_ROOT)}")


if __name__ == "__main__":
    main()
