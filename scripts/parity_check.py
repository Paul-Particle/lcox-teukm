"""
parity_check.py — migration gate for the 3-axis refactor.

Asserts that `cost.levelized_cost(case, p, v, d)` reproduces the legacy
`lcot_*(p, v, d)` functions to ~1e-9 (relative) across a v×d grid, for every
case and every numeric field in the result dict. Run after each refactor step;
must stay green until the legacy functions are deleted.
"""

import numpy as np

from params import load_params
from cases import build_cases
from cost import levelized_cost
from lcot import (lcot_fossil, lcot_lfp, lcot_ironair, lcot_nuclear,
                  lcot_nuclear_elec_containerized, lcot_nuclear_elec_leased,
                  lcot_nuclear_elec_integrated, lcot_mobile)

LEGACY = {
    "fossil": lcot_fossil, "lfp": lcot_lfp, "iron-air": lcot_ironair,
    "nuclear": lcot_nuclear, "nuc-ec": lcot_nuclear_elec_containerized,
    "nuc-el": lcot_nuclear_elec_leased, "nuc-ei": lcot_nuclear_elec_integrated,
    "mobile": lcot_mobile,
}

TOL = 1e-9


def _match(av, bv) -> float:
    """Return relative diff for finite floats; 0 if both nan/inf-equal; raise on mismatch."""
    if isinstance(bv, float) and (np.isnan(bv) or np.isinf(bv)):
        ok = (np.isnan(av) and np.isnan(bv)) or (av == bv)
        if not ok:
            raise AssertionError(f"{av} vs {bv}")
        return 0.0
    return abs(av - bv) / (abs(bv) + 1e-30)


def main():
    p = load_params("config.yaml")
    cases = build_cases(p)
    vs = np.linspace(5, 22, 18)
    ds = np.linspace(100, 6000, 25)
    worst, worst_where = 0.0, None
    for case in cases:
        fn = LEGACY[case.name]
        for v in vs:
            for d in ds:
                a = levelized_cost(case, p, float(v), float(d))
                b = fn(p, float(v), float(d))
                for k, bv in b.items():
                    if k not in a:
                        raise AssertionError(f"{case.name}: missing key {k!r} in new result")
                    rel = _match(a[k], bv)
                    if rel > worst:
                        worst, worst_where = rel, f"{case.name}.{k} v={v:.1f} d={d:.0f}"
                    if rel >= TOL:
                        raise AssertionError(
                            f"PARITY FAIL {case.name}.{k} v={v:.2f} d={d:.0f}: "
                            f"new={a[k]!r} old={bv!r} rel={rel:.2e}")
    print(f"PARITY OK — 8 cases x {len(vs)}x{len(ds)} grid; "
          f"worst relative diff {worst:.2e} at {worst_where}")


if __name__ == "__main__":
    main()
