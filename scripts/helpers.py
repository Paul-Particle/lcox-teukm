"""
helpers.py — shared, stateless computation drawn on in more than one place.

Only genuinely shared functions live here: the finance math several EnergySources
need (`crf`), and the ship physics that strategies — and sources that size to shaft
or bus power, like the tender reactor — both use (the admiralty cube-law propulsion
power and the propulsion-factor product). `crf` is not physics, which is exactly why
the module is named helpers, not physics.

Route-execution arithmetic that ONLY a strategy needs (legs/year, revenue cargo) does
NOT belong here — it lives with the strategies in `strategies.py`.

Units (see units.py): power kW, speed kn, money US$.
"""

from __future__ import annotations

import data_classes as dc


# ---- finance ----
def crf(rate: float, years: float) -> float:
    """Capital recovery factor (annuity): the annual payment that amortizes one unit of
    CAPEX over `years` at discount `rate`."""
    years = max(years, 1e-6)
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


# ---- ship physics ----
def propulsion_factor(pf: dc.PropulsionFactor) -> float:
    """The itemized hull/propeller efficiency stack compounded into the one factor that
    scales propulsion power (1.0 = baseline; electric-only items are 1.0 on mechanicals)."""
    return pf.hull_form * pf.coating * pf.propeller * pf.wider_eff * pf.routing


def prop_power_kw(resistance: dc.Resistance, v_kn: float, factor: float = 1.0) -> float:
    """Propulsion shaft power at speed `v_kn` (admiralty cube law P ~ v^3), scaled by the
    compounded propulsion `factor`. Propulsion only — hotel load is handled separately."""
    return resistance.p_ref_kw * (v_kn / resistance.v_ref_kn) ** 3 * factor
