# TODO / known limitations

Open items only — completed work is in the git history.

**Current focus — finish the rebuild on `main`.** The model is being rebuilt onto a clean
3-axis schema (`Platform × Drivetrain × EnergySource`, composed into `Case`s, costed by
per-case `strategy` functions). The schema, loader, shared physics, all six strategies, the
optimizer, and the entry point are done — the model now runs end-to-end and writes the artifact.
What's left is presentation. The earlier feature branches (scale factors, Sobol sensitivity, MRV
fleet data) are set aside, to be redone from the refactored base.

## Rebuild — what's left to build

- **Presentation** — rebuild `plots.py`/`style.py` against the artifact (`plots.py` is stale,
  importing pre-rebuild modules).
- **Incremental artifact** — `run.py` currently rebuilds `results/lcot.{parquet,csv}` whole each
  run. Add append / partitioned writes once the case/sweep grid is big enough to want it.
- **Config placeholders** — some crew/O&M values in `config.yaml`; some route/axis values in
  `cases.csv` are placeholders pending real data.

## Open design decisions

- **Strategy ↔ Optimizer boundary** — resolved: the strategy owns the whole per-point cost
  (segments the route, sizes stores, computes `carried`/`legs`, assembles LCOT) and returns a row
  dict; `optimize` only *searches* free inputs and compares `lcot`. Revisit if a case needs the
  optimizer to see partial structure.
- **Grid search** — `optimizer.py` searches free axes by exhaustive cartesian grid (each Axis ->
  `n` linearly-spaced points). Fine for the current low-dimensional axes (just `op_v_kn`), but the
  optimal speeds land on grid points (integer knots at the seed `n`); refine the grid or swap in a
  real 1-D minimizer if the speed resolution matters.
- **Containerized-reactor pool utilization** — `ContainerizedReactor.size` levelizes over a
  route-independent fleet utilization (`pool.availability`), per the owned==leased collapse. So
  `pool.idle_h` is currently **unused**; wiring it would mean a route-coupled pool model (passing
  the duty cycle into `size`), which the interface deliberately doesn't do yet. Decide whether the
  fleet-constant is good enough or the route coupling is worth the extra signature.
- **Tender CAPEX on one life** — `TenderReactor.levelize` amortizes hull + reactor CAPEX over a
  single `capex.life_yr`; split if the hull and reactor lives should differ.
- **Source roles in multi-source cases** — a plain list for now; natural roles (buffer / charger)
  may emerge as more cases are written.
- **Extra swept axes beyond `D_max`** — structure for it (eases later Sobol exploration), low priority.

## Speculative parameters

The newer cases (tender, containerized reactor, e-methanol) have little commercial precedent —
engineering estimates. Highest-leverage and most uncertain: the tender block (reactor/hull/O&M
cost, `parasitic_kw`, `tether.cable_*`), `route.standoff_nm`, `route.storm_duration_h`, and the
containerized-reactor block (`capex.usd_per_kw`, `overhead.teu_per_mwe`, `pool.*`). Treat the new
cases' absolute LCOTs as order-of-magnitude until grounded in real data.

## Reactor sizing & speed

- **Marginal reactor CAPEX vs size.** Reactor `capex.usd_per_kw` is flat. At these sizes (tens of
  MW) the marginal $/kW likely varies with size (scale economies; step changes at module
  boundaries — e.g. an AMPERA module granularity where a small power drop crosses a module
  boundary roughly halves containerized CAPEX + overhead). Model it as a size-dependent curve.
  (The rebuild already sizes the *expensive* reactors to the operating speed, not a fixed design
  speed, so the old fossil slow-steaming paradigm no longer mis-sizes them.)

## Case-specific follow-ups

- **Mobile tender:** optionally ride out storms at reduced/zero speed to shrink the pack; jointly
  optimize tethered cruise speed vs battery size vs ships-per-tender (currently a fixed cable cap
  + ~1:1 escort ratio; `ships_per_tender` is a diagnostic only).
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
