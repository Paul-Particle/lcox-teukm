"""
smoke_check.py — lightweight sanity check.

Replaces the byte-exact golden-output regression: with the model and config in
active flux, a golden test fights every intended change (re-bless on each tweak),
so it stops protecting anything. This instead asserts the model still RUNS and
produces SANE numbers — which stays valid as the numbers move:

  1. config loads and the full console report (`report.print_report`) renders
     without raising — catches crashes, bad imports, formatting/field errors;
  2. every case yields a finite, positive LCOT, an in-range optimal speed, and
     positive cargo at a couple of safely-feasible hop lengths.

It deliberately does NOT check long-haul hops, where a battery ship going
infeasible is legitimate, not a bug.

    uv run scripts/smoke_check.py     # exit 1 on any problem
"""

import contextlib
import io
import os
import sys

import numpy as np

from params import load_params, scaled_params
from cases import build_cases
from cost import cost_fn
from analysis import optimize_speed
from report import print_report

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.yaml")

# Hop lengths where every case must be feasible (short/mid; not long-haul).
SANE_DMAX_KM = [200, 1000]


def main():
    p = scaled_params(load_params(CONFIG_PATH))

    # 1. The console report renders without raising.
    with contextlib.redirect_stdout(io.StringIO()):
        print_report(p)

    # 2. Every case is sane at the safely-feasible hop lengths.
    cases = build_cases(p)
    problems = []
    for case in cases:
        for d in SANE_DMAX_KM:
            r = optimize_speed(cost_fn(case), p, d)
            if not np.isfinite(r["lcot"]) or r["lcot"] <= 0:
                problems.append(f"{case.name} @ {d:.0f} km: lcot={r['lcot']}")
            elif not (p.v_min_kn - 1e-9 <= r["v"] <= p.v_max_kn + 1e-9):
                problems.append(f"{case.name} @ {d:.0f} km: v_opt={r['v']} out of [{p.v_min_kn}, {p.v_max_kn}]")
            elif r["cargo_cap"] <= 0:
                problems.append(f"{case.name} @ {d:.0f} km: cargo_cap={r['cargo_cap']}")

    if problems:
        print("SMOKE FAIL:")
        for x in problems:
            print("  " + x)
        sys.exit(1)
    print(f"SMOKE OK — report renders; {len(cases)} cases finite & positive "
          f"at {SANE_DMAX_KM} km")


if __name__ == "__main__":
    main()
