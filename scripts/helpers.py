"""
helpers.py — small stateless functions shared across the cost calc.

Pure arithmetic, no model state and no imports from the rest of the model (so anything
may import it): the propulsion cube law, the propulsion-factor product, a half-TEU
rounding, and the capital recovery factor. Named `helpers`, not `physics`, because
`crf` isn't physics. Journey-specific accounting (legs/year, cargo carried) lives with
the strategies in determine_journey_cost.py.

Inputs are duck-typed (a Resistance, a PropulsionFactor) to avoid an import cycle with
data_classes.
"""

from __future__ import annotations

import math


def crf(rate: float, years: float) -> float:
    """Capital recovery factor: the annual payment that amortizes one unit of CAPEX
    over `years` at discount `rate`."""
    years = max(years, 1e-6)
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def propulsion_factor(pf) -> float:
    """Product of the itemized hull/propeller efficiency factors (a PropulsionFactor)."""
    return pf.hull_form * pf.coating * pf.propeller * pf.wider_eff * pf.routing


def prop_power_kw(resistance, v_kn: float, factor: float = 1.0) -> float:
    """Propulsion power demand at speed v (admiralty cube law P ~ v^3), scaled by the
    drivetrain's propulsion factor. `resistance` is a Resistance (p_ref_kw, v_ref_kn)."""
    return resistance.p_ref_kw * (v_kn / resistance.v_ref_kn) ** 3 * factor


def ceil_half_teu(teu: float) -> float:
    """Round a slot footprint up to the nearest half-TEU (a reactor + shielding package
    still lands on a coarse container-slot grid)."""
    return math.ceil(teu * 2.0) / 2.0
