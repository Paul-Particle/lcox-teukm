# TODO / known limitations

## Powertrain-specific efficiency (P-v curve)
- `elec_prop_power_factor` (0.90) is a **conservative single lump** for the
  hull-form, anti-fouling-coating, larger-low-RPM-propeller/pod, wider-motor-
  efficiency-curve, and weather/trim-routing gains the electric drivetrain
  enables. Replace with an itemized, sourced calculation (hull form ~−20%,
  coatings ~−3%, propeller/pods ~−15–20%, wider eff. ~−5–10%, ops ~−8%; these
  compound, so the realistic factor is well below 0.90 — current value is
  deliberately cautious).
- Fossil may warrant a **smaller** hull-design improvement of its own: the
  barrier cited for optimized hulls is extra design/shipyard coordination, and
  if that is overcome for electric ships it is no longer a blocker for fossil
  either. Add a (smaller) `fossil_prop_power_factor < 1.0` when itemizing.
- Slow-steaming asymmetry: `eta_fossil`/`eta_elec` are currently **constant in
  speed**, so both ships get the identical ideal cube-law energy-vs-speed and
  fossil slow-steaming is over-credited. Real engines droop at part-load while
  motors stay flat — model `eta_fossil` as load/speed-dependent so slowing down
  favours the electric ship.

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
