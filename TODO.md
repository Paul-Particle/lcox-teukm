# TODO / known limitations

## Parameter checks
- `availability` (0.95) is shared; consider raising it for electric/iron-air —
  lower drivetrain maintenance than combustion, à la EV vs ICE.
- `v_min_kn` (9 kn): check the minimum sailing speed is justified (probably fine).

## Cargo demand & load factor (trade-imbalance asymmetry)
Carried cargo is `min(demand, capacity)` with
`demand = load_factor x (gross_slots - overhead_slots)` (see `carried_teu` in
`scripts/lcot.py`). Batteries displace paying cargo only after they use up the
empty `(1 - load_factor)` slack.

This assumes **symmetric leg fill** — every leg loaded to the same fraction.
Real liner trades are directionally imbalanced (full headhaul, light backhaul),
so a fixed battery footprint eats into cargo on the full leg before the
*average* slack is gone. Honest treatment: `mean(min(demand_i, capacity))` over
a headhaul/backhaul fill distribution — add a directional split parameter.

**Decided (per-ship demand):** demand is `load_factor x (gross - that ship's
overhead)`, not a single freight task shared across powertrains. Load factor is
a scale-invariant fraction of each ship's own cargo-capable slots, so the
electric ship's lighter overhead (30 vs 120 slots) is credited in full — 90
extra slots = 72 revenue TEU at L=0.8, ~0.27 c/TEU.km. This is a genuine
advantage of electric/iron-air and is modeled deliberately.
