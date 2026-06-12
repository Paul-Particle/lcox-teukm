# TODO / known limitations

## New cases — speculative params & follow-ups
- **Mobile nuclear tender** and **nuclear-electric (containerized)** params have
  little/no commercial precedent — all engineering estimates. Highest-leverage,
  most uncertain: `mob_tender_*` (reactor/hull/O&M cost, parasitic), `mob_cable_v_cap_kn`,
  `mob_charge_power_kw`, `mob_rendezvous_spacing_h`, `nucc_unit_kw`, `nucc_usd_per_kw`.
  Add them (and `ironair_pack_wh_per_kg`) to the tornado sensitivity — it currently
  sweeps only Li-ion params.
- Mobile tender: rendezvous spacing is a fixed param; jointly optimizing it trades
  ship battery size vs tender count. Battery cycle-life accounting under frequent
  shallow top-ups is approximate. Single capped cruise speed (no separate free-speed
  deadhead leg).
- Nuclear-electric: `elec_prop_power_factor` (pod gains) applied to both; the
  single-shaft integrated case may not earn the full pod benefit — consider a
  separate factor. Containerized priced as owned (shorter life); a lease option
  (`nucc_lease_usd_yr`) is an alternative.

## Deferred — 3-axis platform refactor
The big refactor (Platform × Drivetrain × Energy-source; bulk/chemical tonne·km
platforms; one `levelized_cost(case,v,d)`) is documented in the plan file but NOT
done. The mass + asymmetry work here on the flat model carries concepts the refactor
will formalize on the platform axis. Plot also needs work: 7 trace labels crowd the
right edge of `lcot_vs_dmax` — consider faceting or a real legend.


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

## Hotel / reefer load
`p_hotel_kw` (1500) is a constant, identical across powertrains and speeds. It
bundles three components with different behaviour:
- **Reefer load** — the large, variable part (hundreds of plugs x a few kW).
  Not constant in reality: it scales with reefers carried, bounded by cargo
  slots. The substantive coupling is that on a battery ship reefer energy comes
  from the (slot-displacing, expensive) battery, whereas fossil feeds reefers
  from cheap aux gensets — so reefer-heavy routes penalize battery ships far
  more than fossil. A faithful model couples reefer load to carried cargo AND
  credits reefer revenue (reefers are high-value, which the all-TEU-equal cost
  model ignores); both are out of scope for now. A hotel-load sensitivity
  (reefer-light / base / reefer-heavy) is in `print_hotel_sensitivity` to
  surface the effect without overfitting.
- **Accommodation / crew** — small; electric may shed a few engine-room
  engineers (slightly lower), nuclear likely needs more crew + security
  (slightly higher). Consider a small per-powertrain hotel delta later.
- **Ship systems** (pumps, ventilation, nav) — roughly constant, no powertrain
  dependence.

Already correct: hotel is time-based (`x sail_hours`), so slow steaming raises
hotel energy per leg and partially offsets the cube-law savings.

## Maneuverability credit (electric / podded ships)
Option to credit the superior low-speed maneuverability of electric ships
(azimuth pods/thrusters) — faster, easier berthing and less tug assistance.
Two hooks:
- **Port time**: make `port_hours_per_call` per-powertrain (lower for electric)
  for the berthing/maneuverability saving. NOTE — battery swap itself adds ~no
  time when a battery *replaces cargo* (offload-depleted + onload-charged = the
  2 crane moves a cargo slot already needs; uniform battery boxes may crane
  faster). Added swap time scales only with batteries in *empty* slots
  (`B_empty` below), minus any charged onboard via a modest plug. Shorter port
  time raises cycles/year, most impactful short-haul where port time dominates
  the cycle, i.e. exactly where battery ships compete.
- **O&M / tugs**: reduced tugboat fees. Tug/port fees are **not currently
  modeled** (om_* is crew/insurance/repairs/lube only). Add an explicit
  tug-cost-per-call parameter (lower for electric) rather than folding it into
  `om_elec`, so the credit is visible.

Related: crew salaries are bundled in `om_*_usd_yr`, not itemized or scaled by
headcount. Itemizing O&M (crew / insurance / repairs / tugs / ...) would let
the "fewer engineers" and tug credits be modeled explicitly instead of via the
single 14% electric-vs-fossil O&M gap.

## Parameter checks
- `availability` (0.95) is shared; consider raising it for electric/iron-air —
  lower drivetrain maintenance than combustion, à la EV vs ICE.
- `v_min_kn` (9 kn): check the minimum sailing speed is justified (probably fine).

## Cargo demand & load factor
Carried cargo (`carried_teu` in `scripts/lcot.py`) is the round-trip average of
per-direction `min(volume-limited, mass-limited)`, where volume demand =
`load_factor x (gross_slots - overhead)` and batteries displace cargo only after
the usable empty slack.

**Done — asymmetric legs:** `load_factor_imbalance` (default 0 = symmetric) splits
the mean LF into headhaul `LF·(1+imb)` and backhaul `LF·(1-imb)`; a fixed battery
footprint eats the fuller leg first. (`mean(min(demand_dir, capacity))`.) Future:
a richer fill distribution instead of a two-point head/back split.

**Done — mass/deadweight constraint:** carried also limited by
`(deadweight_cargo_t - battery_tonnes)/cargo_t_per_teu`; battery mass =
`installed_kwh / pack_wh_per_kg`. This makes iron-air's weight bite (it's
mass-limited at all ranges and infeasible long-haul). `ironair_pack_wh_per_kg`
(30 Wh/kg) is a key uncertain input — sweep it. Power constraint already exists
(`*_min_discharge_h`); volume = slots (`*_kwh_per_teu`).

**Decided (per-ship demand):** demand is `load_factor x (gross - that ship's
overhead)`, not a single freight task shared across powertrains. Load factor is
a scale-invariant fraction of each ship's own cargo-capable slots, so the
electric ship's lighter overhead (30 vs 120 slots) is credited in full — 90
extra slots = 72 revenue TEU at L=0.8, ~0.27 c/TEU.km. This is a genuine
advantage of electric/iron-air and is modeled deliberately.

**Empty-slot usability.** Not every empty slot is battery-usable (dangerous-
goods segregation, stability/lashing, crane access, reefer positions), so
batteries should not get the full `(1-L)·cargo_slots` slack for free.
- **Done (hard cap):** `batt_empty_usable_frac` (θ = 0.40) — batteries fill
  θ·slack for free, then displace cargo 1:1 (`carried_teu`). θ = 1.0 recovers
  the old `min(demand, capacity)`. Raises battery-ship LCOT, iron-air most.
- **Future (linear ramp):** replace the hard cap with a *marginal* cargo cost
  ramping linearly 0→1 over `[θ·slack, slack]`, so constraints bite
  progressively rather than at a single threshold.
- **Couple to swap time:** the batteries-in-empty-slots count
  (`B_empty = max(0, B - θ·slack)`-ish) is exactly what adds port/swap time —
  feed the same quantity into the maneuverability port-time term above.

---

# Appendix — deferred 3-axis refactor design

Preserved from the planning pass (the plan file lives outside the repo). The
goal: support bulk carriers & chemical tankers (tonne·km, not just TEU·km) and
let every powertrain compose cleanly. Most cases are recombinations along three
orthogonal axes: **Platform** (cargo/route) × **Drivetrain** (energy→shaft) ×
**Energy-source/logistics**. Today all three are baked into one flat `Params` +
one `lcot_*(p,v,d)->dict` per case.

**Target:** a cost model = `compose(Platform, Drivetrain, EnergySource)`, with a
single shared `levelized_cost(case, v, d)` instead of N `lcot_*` functions.

- **Three frozen dataclasses** —
  - `Platform`: `name`, `cargo_unit` ("TEU"/"tonne"), `gross_capacity`,
    `load_factor`, hull capex/life, port hours, availability, `batt_usable_frac`,
    + a `displace(overhead, storage)` strategy fn.
  - `Drivetrain`: `eta`, `prop_power_factor`, prop capex/life, om, overhead.
  - `EnergySource`: `kind` ∈ fuel/battery/reactor, price, `BatterySpec|None`,
    reactor capex/life, + a `size_storage` strategy fn.
  Composed by a `Case` namedtuple registry in a new `cases.py` (one row per case).

- **`carried_teu` generalizes** to `carried(demand, capacity)` — *identical
  arithmetic*, but the kWh→capacity-unit mapping is platform-specific: container
  displaces **volume** (TEU via `kwh_per_teu`); bulk/chemical displace
  **deadweight** (tonnes via `pack_wh_per_kg`). The mass constraint added now on
  the flat model is the same idea — the refactor just makes it the primary
  binding metric on tonne·km platforms.

- **Config → nested YAML** (`platforms:` / `drivetrains:` / `sources:`), with a
  section-validating loader preserving the strict reject-unknown-keys behaviour
  per section (string allow-list for `name`/`kind`/`cargo_unit`, numeric coercion
  otherwise).

- **Overhead & O&M are per-(platform,drivetrain) cells** — keep drivetrain
  defaults + a per-case override map in `cases.py` (a nuclear bulk carrier's O&M
  ≠ a nuclear container ship's).

- **Mixed cargo units** → plots/crossover **facet by platform** (can't share a
  TEU·km / tonne·km axis); `crossover_dmax` asserts same `cargo_unit`. Dict keys
  generalize `teukm→unitkm`, `battery_*→storage_*`, add `cargo_unit`.

- **Migration order (runnable + parity-gated each step):** snapshot baseline
  stdout → add axis dataclasses + nested loader *alongside* the flat `Params`
  (adapter) → write `levelized_cost` + `cases.py` for the container cases →
  parity-test vs old `lcot_*` on a v×d grid (match to ~1e-9; **no formula tweaks
  during the refactor**) → switch `analysis.py`/`report.py` to consume `Case` →
  delete the flat-`Params` shim → restructure config → then add bulk/chemical
  platforms (data only).

**Bulk/chemical economics** (DWT, hull capex, load factors, port economics,
cargo-value notes) were deliberately left as placeholders — research them when
the platforms are actually populated.
