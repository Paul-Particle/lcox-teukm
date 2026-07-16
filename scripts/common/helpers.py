"""helpers.py — genuinely shared, stateless computation.

Only what is used in more than one place: the finance math several EnergySources need
(`crf`), and the ship physics that strategies — and any source sizing to shaft/bus power,
like the tender reactor — both use. `crf` isn't physics, which is why this is `helpers`,
not `physics`. Strategy-only route arithmetic (legs/year, carried) lives in strategies/_shared.py.
"""

from __future__ import annotations

import numpy as np

from common import schema


def crf(rate: float, years: float) -> float:
    """Capital recovery factor: annual payment amortizing one unit of CAPEX over `years`."""
    years = np.maximum(years, 1e-6)
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def propulsion_factor(pf: schema.PropulsionFactor) -> float:
    """Itemized hull/propeller efficiency stack compounded into one propulsion-power factor
    (1.0 = baseline; electric-only items are 1.0 on mechanicals)."""
    return pf.hull_form * pf.coating * pf.propeller * pf.wider_eff * pf.routing


def prop_power_kw(resistance: schema.Resistance, v_kn: float, factor: float = 1.0) -> float:
    """Propulsion shaft power at `v_kn` (admiralty cube law P ~ v^3), scaled by `factor`.
    Propulsion only — hotel load is separate."""
    return resistance.p_ref_kw * (v_kn / resistance.v_ref_kn) ** 3 * factor
