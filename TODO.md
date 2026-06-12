# TODO / known limitations

## New cases — speculative params & follow-ups
- **Mobile nuclear tender** and **nuclear-electric (containerized)** params have
  little/no commercial precedent — all engineering estimates. Highest-leverage,
  most uncertain: `mob_tender_*` (reactor/hull/O&M cost, parasitic), `mob_cable_v_cap_kn`,
  `coastal_untethered_distance_nm`, `storm_survival_duration_h`, `cable_efficiency`,
  `nucc_unit_kw`, `nucc_usd_per_kw`, `nucc_pool_idle_h`, `nucc_pool_availability`.
  Now swept in per-case tornados (`plot_tornados`: LFP, iron-air, mobile,
  nuclear-electric containerized + leased), incl. `ironair_pack_wh_per_kg`.
- **Mobile tender escort refactor — DONE.** `lcot_mobile` now models the Dedicated
  Escort concept: battery sized for `max(coastal transit, storm survival)`, tender cost
  amortized over the bus energy pushed across the cable per ocean crossing, with a
  reactor-net-power feasibility check. Future optimizations still open: ride out storms
  at reduced/zero speed to shrink the pack; jointly optimize tethered cruise speed vs
  battery size vs ships-per-tender (currently a fixed cable cap + ~1:1 escort ratio).
- `lcot_mobile` duplicates the battery hull/motor/CAPEX/`carried` boilerplate; the
  3-axis refactor collapses it to "battery energy-source + at-sea-charge sizing
  strategy + tender price fn" — good motivation to do the refactor.
- Nuclear-electric: `elec_propulsion_factor` (pod gains) applied to both; the
  single-shaft integrated case may not earn the full pod benefit — consider a
  separate factor.
- **Reactor-as-a-service lease — DONE.** `lcot_nuclear_elec_leased` recovers the
  containerized reactor's CAPEX via a per-kWh rate levelized over the reactor's POOL
  utilization (`nucc_pool_idle_h`, `nucc_pool_availability`), decoupled from one
  ship's port time — mirrors the mobile-tender service pricing. Caveat: recovers
  reactor CAPEX + fuel only; the model has no separate reactor-O&M line (it sits in
  the ship's non-crew residual, kept ship-side), and the doc's option to also bundle
  nuclear-specialist crew into the lease isn't done (our `crew_count_nuclear` is the
  ship's whole complement, not splittable). The 3-axis refactor folds lease-vs-owned
  into an `EnergySource` pricing strategy.
- Containerized reactor power density: revisit `nucc_overhead_slots_per_unit` (45
  slots / 15 MWe module incl. shielding) against AMPERA-class footprints — may be
  pessimistic (per `swappable_reactor_concept.md` §3).
- **Battery mix for short journeys:** iron-air is power-limited (C/50) up to
  ~1500 km — its pack is sized by *sustained* cruise power, 2-6x the energy it
  uses, and a finite LFP/supercap buffer can't relieve that (cruise power is
  sustained, not peaky). So a **battery mix may be optimal by route**: LFP
  (high power, modest energy) short-haul, iron-air (cheap bulk energy) long-haul
  — explore choosing chemistry per route, or a physical LFP + iron-air split
  (LFP carries cruise power, iron-air adds range only where energy binds).

## Data & itemization
- **Tech-data library:** integrate a techno-economic reference dataset (published
  reactor / battery / hull cost, efficiency, density figures) so param values are
  sourced robustly *alongside* our own engineering estimates — tag each param with
  source + uncertainty rather than relying on hand-curated comments.
- **Full O&M & availability itemization:** O&M is only partly itemized — crew
  (`crew_count_* x crew_cost_usd_yr`) + tugs + a single lumped non-crew residual
  (`om_*_usd_yr` = insurance/repairs/lube/stores). Break that residual into its
  components, per powertrain. Availability is per-powertrain (`availability` vs
  `availability_elec`) but not decomposed; itemize planned maintenance /
  refueling-or-bunkering / weather downtime so each driver is explicit.

## Design speed & reactor sizing
- **Design speed is fixed high, cruise is swept low — wrong paradigm for nuclear.**
  Installed power (engine/motor/reactor) is sized at `v_design_max_kn` (22 kn)
  while the optimizer picks a slower cruise. That's the fossil slow-steaming
  paradigm (cheap to slow down, save fuel) — but nuclear has ~free fuel and a
  reactor sized for 22 kn is wasted CAPEX if it ever cruises slower. Real
  practice sizes installed power from a *service speed* + ~15% sea/weather power
  margin, not from the optimizer's cruise. At minimum **sweep `v_design_max`**
  (it strongly drives reactor CAPEX); better, model a service-speed + margin
  basis and let nuclear's own speed/sizing optimum emerge rather than inheriting
  the fossil one.
- **Marginal reactor CAPEX vs size.** `nuclear_usd_per_kw` / `nucc_*` / `nuci_*`
  / `mob_tender_usd_per_kw` are flat $/kW. At the absolute sizes here (tens of
  MW) the marginal $/kW likely varies a lot with size (economies of scale, or
  step changes at module boundaries / large single units). Model reactor CAPEX
  as a size-dependent curve, not a constant $/kW — probably material to the
  nuclear and tender results.

## Deferred — 3-axis platform refactor
The big refactor (Platform × Drivetrain × Energy-source; bulk/chemical tonne·km
platforms; one `levelized_cost(case,v,d)`) is documented in the plan file but NOT
done. The mass + asymmetry work here on the flat model carries concepts the refactor
will formalize on the platform axis.


## Powertrain-specific efficiency (P-v curve)
- **Done — itemized electric factor:** `elec_hull_form/coating/propeller/
  wider_eff/routing` factors compound (~0.73 via `_elec_propulsion_factor`), replacing
  the 0.90 lump. Values at the conservative end of the literature ranges.
- **Done — fossil factor:** `fossil_propulsion_factor` (0.95) — the smaller
  hull/coating/routing gain fossil can adopt once the design barrier is overcome.
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
- **Done — accommodation/crew:** per-powertrain hotel delta
  (`hotel_delta_elec_kw` -150, `hotel_delta_nuclear_kw` +250) — electric sheds a
  few engine-room engineers, nuclear adds crew + security.
- **Ship systems** (pumps, ventilation, nav) — roughly constant, no powertrain
  dependence.

Already correct: hotel is time-based (`x sail_hours`), so slow steaming raises
hotel energy per leg and partially offsets the cube-law savings.

- **Hotel efficiency split (battery cases):** `pack_draw_leg = E_use / eta_elec`
  applies the traction-motor efficiency (0.88) to the hotel load, but hotel
  power never passes through the drive motor — it draws directly from the pack
  via the ship-service bus (efficiency ~0.97). The correct treatment is
  `pack_draw_leg = prop_E/eta_elec + hotel_E/eta_hotel`. At 14 kn the error is
  ~1.5%, growing at lower speeds as hotel becomes a larger fraction of total
  draw. For fossil, `E_use/eta_fossil` applies main-engine efficiency to the
  hotel load too; aux-gen efficiency (~0.42) is close enough to `eta_fossil`
  (~0.50) to be order-of-magnitude correct, but should eventually be modelled
  explicitly with a separate `eta_aux_gen` parameter.

## Maneuverability credit (electric / podded ships) — DONE
- **Port time:** per-powertrain `port_hours_elec` (16 h vs 18) — pods/azimuth
  give electric-drive ships faster berthing.
- **Tugs:** `tug_usd_per_call` (5000, conventional) vs `tug_usd_per_call_elec`
  (2000), charged per cycle; electric needs far less tug assistance.
- **Crew salary:** O&M itemized — `om_*_usd_yr` is now the non-crew residual and
  crew is `crew_count_* x crew_cost_usd_yr` explicitly (fossil 22 / elec 20 /
  nuclear 30), so the "fewer engineers" credit is visible and tunable.
Remaining: the swap-vs-empty-slot port-time nuance is still ignored (the elec
port time assumes swap ~neutral) — see Cargo demand / empty-slot usability.

## Parameter checks
- **Done — availability:** `availability_elec` (0.97) for battery/electric-drive
  ships vs 0.95 (fossil/nuclear), reflecting lower drivetrain maintenance.
- **Done — min speed:** `v_min_kn` lowered to 5 kn (lets the power-bound iron-air
  and battery ships slow-steam further — notably makes long-haul iron-air feasible).

## Cargo demand & load factor
Carried cargo (`carried_teu` in `scripts/lcot.py`) is the round-trip average of
per-direction `min(volume-limited, mass-limited)`, where volume demand =
`load_factor x (gross_slots - overhead)` and batteries displace cargo only after
the usable empty slack.

**Done — asymmetric legs (now default 0.2):** `load_factor_imbalance` splits
the mean LF into headhaul `LF·(1+imb)` and backhaul `LF·(1-imb)`; a fixed battery
footprint eats the fuller leg first. (`mean(min(demand_dir, capacity))`.) Future:
a richer fill distribution instead of a two-point head/back split.

**Done — mass/deadweight constraint:** carried also limited by
`(deadweight_cargo_t + fuel_credit - battery_tonnes)/cargo_t_per_teu`; battery
mass = `installed_kwh / pack_wh_per_kg`. Battery/nuclear ships recover the fossil
bunker mass (`bunker_mass_t`) they don't carry (`fuel_credit`); fossil gets none
(its fuel is already netted out of `deadweight_cargo_t`). This makes iron-air's
weight bite (mass-limited at all ranges, infeasible long-haul). Uncertain inputs
to sweep: `ironair_pack_wh_per_kg` (30 Wh/kg), `bunker_mass_t`. Power constraint
already exists (`*_min_discharge_h`); volume = slots (`*_kwh_per_teu`).
Refinement: `bunker_mass_t` is a fixed constant — really it scales with range/
speed (longer voyages carry more fuel, tightening fossil's own mass budget too);
fossil's mass constraint is currently never binding.

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
  - `Drivetrain`: `eta`, `propulsion_factor`, prop capex/life, om, overhead.
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
