"""
regression_check.py — frozen golden-output regression test.

Renders the full console report for the base-case config and compares it byte-for-
byte against `golden_output.txt`. This replaced the parity oracle (the legacy
`lcot_*` functions + `parity_check.py`) once the 3-axis refactor's unified path
became canonical: it pins the numeric results of all cases so later changes that
should be behaviour-preserving stay so, and intentional changes are reviewed by
regenerating the golden file.

    uv run scripts/regression_check.py            # check (exit 1 on mismatch)
    uv run scripts/regression_check.py --update    # regenerate golden_output.txt

The golden file is the console output of run.py minus the `Saved plot:` lines
(which are filesystem paths, not model results)."""

import contextlib
import io
import os
import sys

import numpy as np

from params import load_params
from report import (print_base_header, print_energy_cost, print_breakdown,
                    print_crossover, print_sensitivity, print_hotel_sensitivity,
                    print_mobile_fleet, print_reactor_lease)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.yaml")
GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_output.txt")


def render() -> str:
    """The console half of run.py — the same print sequence, captured to a string."""
    p = load_params(CONFIG_PATH)
    d_grid = np.linspace(100, 6000, 80)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_base_header(p)
        print_energy_cost(p)
        print_breakdown(p)
        print_crossover(p, d_grid)
        print_sensitivity(p, d_grid)
        print_hotel_sensitivity(p, d_grid)
        print_mobile_fleet(p)
        print_reactor_lease(p)
    return buf.getvalue()


def main():
    out = render()
    if "--update" in sys.argv:
        with open(GOLDEN_PATH, "w") as f:
            f.write(out)
        print(f"golden_output.txt updated ({out.count(chr(10))} lines)")
        return
    with open(GOLDEN_PATH) as f:
        golden = f.read()
    if out == golden:
        print(f"REGRESSION OK — console output matches golden_output.txt "
              f"({out.count(chr(10))} lines)")
        return
    # Show the first differing lines for a quick diagnosis.
    a, b = golden.splitlines(), out.splitlines()
    for i in range(max(len(a), len(b))):
        la = a[i] if i < len(a) else "<EOF>"
        lb = b[i] if i < len(b) else "<EOF>"
        if la != lb:
            print(f"REGRESSION FAIL at line {i + 1}:\n  golden: {la!r}\n  now:    {lb!r}")
            break
    sys.exit(1)


if __name__ == "__main__":
    main()
