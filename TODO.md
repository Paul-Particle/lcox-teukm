# TODO / known limitations

The model runs end-to-end on a vectorized kernel. Two inputs feed it: **`assumptions.yaml`** (the
component library + `cases:` compositions + `shared` voyage scalars, with sampling ranges declared
on the values) and **`studies.yaml`** (role assignment — which config leaves are sampled / fixed /
swept / optimized). A study becomes array-valued config leaves (`design`), one broadcast kernel
call per case with the lever argmin-collapsed (`evaluate`), a per-slice variance decomposition
(`analyze`), and a persisted store (`store`). The single entry point `lcot.py` drives all of it:
`lcot run` renders the baseline `fleet` study to `results/lcot.{parquet,csv}`, `lcot study` runs
the sensitivity studies into `results/sobol/`, and `lcot plot` draws the fleet and sensitivity
figures (`lcot all` = run + plot). The two evaluation renders behind the CLI live in `pipeline.py`. `docs/sobol_sensitivity_plan.md` records the
design and the alternatives weighed at each decision point.

The rebuild, the "any parameter can play any role" generalization, the vectorized kernel, and the
Sobol machinery are all done. What remains is model fidelity plus a few open modelling decisions —
not plumbing.

## Open design decisions

- **Strategy ↔ evaluate boundary** — revisit if a case needs the collapse to see partial
  structure (today `evaluate` argmin-collapses the whole lever block by the objective).
- **Grid resolution on the lever** — an optimize axis is an exhaustive grid (`n` linearly-spaced
  points), so the optimum lands on a grid knot. Fine at the current resolution; refine the grid or
  drop a 1-D minimizer onto the lever axis if speed precision starts to matter (the plan flags this
  as the fallback if it shows up in the confidence intervals).
- **Containerized-reactor pool utilization** — `ContainerizedReactor.size` levelizes over a
  route-independent fleet utilization (`pool.availability`), so `pool.idle_h` is still **unused**;
  it needs an estimated turnaround time like the tender's.
- **Design speed as a live variable** — `design_v_kn` is an ordinary config leaf
  (`shared.design_v_kn`), so it can be sampled/swept/optimized like any other. But optimizing it
  only bites once the model has a counterforce — a peak-power / brief-sprint constraint (weather
  evasion, schedule recovery) that rewards a larger converter. Without one, minimizing converter
  CAPEX drives it to the floor. Add such a constraint before treating it as a meaningful free
  variable.
- **Tender CAPEX on one life** — `TenderReactor.levelize` amortizes hull + reactor CAPEX over a
  single `capex.life_yr`; split if the hull and reactor lives should differ.
- **Source roles in multi-source cases** — a plain list for now; natural roles (buffer / charger)
  may emerge as more cases are written.

## Deferred / future plumbing

- **Incremental artifact** — `lcot run` rebuilds `results/lcot.{parquet,csv}` whole each run; add
  append / partitioned writes once the case × sweep grid is big enough to want it.
- **Config placeholders** — some crew/O&M values, the tender `idle_h` / `standoff_nm` / `detach_*`
  fields, and `design_v_kn` are placeholders pending real data (flagged in `assumptions.yaml`).

## Speculative parameters

The newer cases (tender, containerized reactor, e-methanol) have little commercial precedent —
engineering estimates. Highest-leverage and most uncertain: the tender block (reactor/hull/O&M
cost, `parasitic_kw`, `tether.cable_*`, `tether.standoff_nm`, `tether.detach_duration_h`,
`tether.detach_frac`) and the containerized-reactor block (`capex.usd_per_kw`,
`overhead.teu_per_mwe`, `pool.*`). Treat the new cases' absolute LCOTs as order-of-magnitude until
grounded in real data.

## Reactor sizing & speed

- **Marginal reactor CAPEX vs size.** Reactor `capex.usd_per_kw` is flat. At these sizes (tens of
  MW) the marginal $/kW likely varies with size (scale economies; step changes at module
  boundaries — e.g. an AMPERA module granularity where a small power drop crosses a module
  boundary roughly halves containerized CAPEX + overhead). Model it as a size-dependent curve.
  (The rebuild already sizes the *expensive* reactors to the operating speed, not a fixed design
  speed, so the old fossil slow-steaming paradigm no longer mis-sizes them.)

## Case-specific follow-ups

- **Mobile tender:** detached sailing is billed at the full cruise bus power; slowing down while
  detached would shrink both the expected drain and the `detach_duration_h`-sized pack, at the
  cost of voyage time (a genuine trade — the tender won't slow its schedule to wait). Jointly
  optimize tethered cruise speed vs battery size vs ships-per-tender (currently a fixed cable cap
  + ~1:1 escort ratio; `ships_per_tender` is a diagnostic only). Pack cycle counting:
  `BatterySource.life_yr` counts one full cycle per leg, but the tender pack actually runs two
  coastal sub-leg cycles (plus expected detach drains) per leg — count full-cycle equivalents
  from energy throughput instead.
- **Containerized/pooled reactor:** no separate reactor-O&M line (it sits in the ship's non-crew
  residual, kept ship-side); nuclear-specialist crew isn't split out (`crew_count` is the whole
  complement).
- **Nuclear-electric (integrated):** the electric-drive propulsion-factor gains (pods) are applied
  to both nuclear-electric cases; the single-shaft *integrated* plant may not earn the full pod
  benefit — consider a separate factor.
- **Battery mix for short journeys:** iron-air is power-limited (C/50) — its pack is sized by
  sustained cruise power, which a finite LFP/supercap buffer can't relieve. A battery mix may be
  optimal by route (LFP short-haul, iron-air long-haul; or a physical LFP + iron-air split).

## Efficiency & load modeling

- **Sea-state / weather time series:** the propulsion factors and the constant drive/hotel
  efficiencies are single voyage-average constants (e.g. `drive` is estimated from one
  marine-engine figure; `propulsion_factor.wider_eff` is a flat credit for an electric motor
  staying near optimum). A fuller model would resolve weather and sea state as a function of
  position and time along the route, so required power and the near-optimum efficiency gain emerge
  from the conditions actually encountered rather than from fixed multipliers.
- **Voyage Monte Carlo to calibrate the expected-value weather parameters:** the analytic model
  bills expected per-leg time and energy, with sizing margins kept out of throughput
  (`margins.energy_reserve` and the `detach_duration_h` pack are capex/mass only; billed energy
  is nominal consumption plus, for the tender, the drain of the `detach_frac` expected
  cable-dropped hours). Those expected values are placeholders. Calibrate them by simulating
  hundreds of journeys per route against historical weather (hindcast time series) with
  hour-by-hour pack SoC: outputs are per-route `detach_frac` (sea state above the floating
  tether's limit), an expected weather energy uplift on consumed energy (all cases — fuel burn
  included — currently bill calm-water consumption), the hove-to/survival-weather share of
  `availability`, and the longest detached stretch `detach_duration_h` should represent. The
  same runs validate the SoC tails the analytic model waves through: arriving at the tender
  near-empty into weather that delays connecting, detach hitting before the pack has been
  refilled, and end-of-crossing SoC when detach clusters late.
- **Slow-steaming asymmetry:** drive/hotel efficiencies are constant in speed, so both ships get
  the ideal cube-law energy-vs-speed and fossil slow-steaming is over-credited. Real engines droop
  at part-load while motors stay flat — model the fossil drive efficiency as load/speed-dependent
  so slowing favours the electric ship.
- **Reefer load coupling:** `hotel_base_kw` is constant and identical across powertrains. Reefer
  load really scales with reefers carried and is far costlier on a battery ship (it comes from the
  slot-displacing pack). A faithful model couples reefer load to carried cargo and credits reefer
  revenue; both out of scope.

## Cargo demand & load factor

- **Empty-slot usability — linear ramp:** `slot_limits.batt_empty_usable_frac` (θ) is a hard cap
  (stores fill θ·slack free, then displace cargo 1:1). Replace with a *marginal* cargo cost ramping
  linearly 0→1 over `[θ·slack, slack]` so the constraint bites progressively.
- **Couple battery-in-slot count to swap time:** the batteries-in-empty-slots quantity is exactly
  what adds port/swap time — feed it into the port-time term (currently swap ≈ time-neutral).
- **`FuelSource.energy_mass_t` is a fixed constant:** really it scales with range/speed (longer
  voyages carry more fuel), tightening fossil's own mass budget; fossil's mass constraint is
  currently never binding.

## Data & itemization

- **Tech-data library:** integrate a techno-economic reference dataset (published reactor / battery
  / hull cost, efficiency, density figures), tagging each value with source + uncertainty rather
  than hand-curated comments.
- **Full O&M & availability itemization:** O&M is partly itemized (crew + tug + a lumped non-crew
  residual, `om_other_usd_yr`). Break the residual into components per powertrain. Availability is
  per-drivetrain (`operations.availability`) but not decomposed — itemize planned maintenance /
  refueling-or-bunkering / weather downtime.

## Bulk/chemical platforms (when a second platform earns it)

The `carried`/`Platform` machinery supports `cargo_unit = "tonne"`, but no bulk/chemical case
exists yet. Adding one needs: real economics (DWT, hull capex, load factors, port/cargo-value —
left as placeholders, research when populated); plot/crossover **faceting by platform** (can't
share a TEU·km / tonne·km axis); a per-(platform, drivetrain) O&M/overhead override map; and a
**cargo-as-fuel** coupling for chemical/e-fuel tankers (consumed mass netted out of deliverable
cargo, fuel priced at the cargo commodity price — needs the energy chain to feed into `carried`).
