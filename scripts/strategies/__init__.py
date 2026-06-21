"""
strategies — the per-case strategy functions, one module each.

A strategy is a plain function `(case, point) -> dict`: reads the case's fixed setup + one
`point` (parameter-space coordinates the optimizer passes in, e.g. `{"d_km", "op_v_kn"}`),
segments the route, decides which source supplies what, sizes the stores, and returns a row
dict — `lcot` (all the optimizer reads) plus extra numbers for the artifact. Config is frozen
dataclasses; the point in and row out are plain dicts (rows go straight to the artifact).

The optimizer does `getattr(strategies, case.strategy)`, so each strategy is re-exported here
by name. One strategy per structurally-distinct case-type; cases differing only in parameters
share one (fossil/e-methanol; LFP/iron-air). Each orchestrates the source cost methods on its
EnergySource (`size` / `life_yr` / `usd_per_kwh` / `levelize`, defined in sources.py):
  - fuel_burn                   — fossil / e-methanol: mechanical drivetrain, thin commodity fuel.
  - port_swap_battery           — LFP / iron-air: electric, pack carries a whole leg, swapped at port.
  - tether_charge               — nuclear tender: battery ship, crossing carried by an at-sea reactor.
  - reactor_direct              — integrated reactor, direct mechanical drive.
  - reactor_electric_integrated — integrated reactor + generator + motor, electric drive.
  - reactor_electric            — bare motor + separable CONTAINERIZED reactor source.

Expensive reactors are sized to the OPERATING speed (no free oversizing); cheap engines/motors
to the FIXED design speed. The scaffolding common to all six (demand resolution, fixed-cost
assembly, the row/lcot skeleton, route arithmetic `legs_per_year`/`carried`) lives in `_shared`.
"""

from .tether_charge import tether_charge
from .port_swap_battery import port_swap_battery
from .fuel_burn import fuel_burn
from .reactor_direct import reactor_direct
from .reactor_electric_integrated import reactor_electric_integrated
from .reactor_electric import reactor_electric

__all__ = [
    "tether_charge",
    "port_swap_battery",
    "fuel_burn",
    "reactor_direct",
    "reactor_electric_integrated",
    "reactor_electric",
]
