# TODO / known limitations

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

- **Strategy ↔ Optimizer boundary** — Revisit if a case needs the optimizer to see partial structure.
- **Grid search** — `optimizer.py` searches free axes by exhaustive cartesian grid (each Axis ->
  `n` linearly-spaced points). Fine for the current low-dimensional axes (just `op_v_kn`), but the
  optimal speeds land on grid points (integer knots at the seed `n`); refine the grid or swap in a
  real 1-D minimizer if the speed resolution matters.
- **Containerized-reactor pool utilization** — `ContainerizedReactor.size` levelizes over a
  route-independent fleet utilization (`pool.availability`). So `pool.idle_h` is currently **unused**;
  it urgently needs an estimated turnaround time just like the tender.
- **Design speed as a live variable** — `design_v_kn` now flows through the `point` resolver, so
  it can be put on a sweep/optimize axis like `d_km`/`op_v_kn`. But optimizing it only bites once
  the model has a counterforce — a peak-power / brief-sprint constraint (weather evasion, schedule
  recovery) that rewards a larger converter. Without one, minimizing converter CAPEX drives the
  design speed to the floor. Add such a constraint before treating it as a meaningful free variable.
- **Tender CAPEX on one life** — `TenderReactor.levelize` amortizes hull + reactor CAPEX over a
  single `capex.life_yr`; split if the hull and reactor lives should differ.
- **Source roles in multi-source cases** — a plain list for now; natural roles (buffer / charger)
  may emerge as more cases are written.

## Sobol sensitivity — readiness (before machine-generating `cases.csv`)

*Implementation plan (alternatives weighed at each decision point, measured baselines):
`docs/sobol_sensitivity_plan.md`.*

The eval engine (`run → optimize → strategy`, the `Point` resolver, the axis-consumed guard) is
ready to evaluate a machine-generated batch; the input and analysis ends are not. Gaps, in
dependency order:

- **Override reach is the blocker.** Only `d_km`, `op_v_kn`, `design_v_kn`, `detach_frac` are read
  through `point.get`; everything else is read straight off the frozen dataclasses. So
  **route/condition params are Sobol-ready today** — `standoff_nm`, `idle_h`, `detach_duration_h`,
  `load_factor`, `load_factor_imbalance` are per-row CSV scalars that land in each case's `Route`
  and are read directly, no axis needed — but the **component-library params are not reachable
  per-sample**: the battery/reactor/tether cost + efficiency blocks live in `config.yaml`,
  referenced by name, so a case row selects *which* component, not its internals. The
  highest-leverage uncertain params (see "Speculative parameters" below) are almost all library
  params. Fix at altitude: generalize the `point.get` idea from route scalars to any config leaf —
  a per-sample dotted-path override (`battery.capex.usd_per_kwh=95`) applied via
  `dataclasses.replace` before the strategy runs — rather than adding a CSV column per parameter.
  (This is the real state of the Stage-1 "any parameter is sweepable" note below: the *mechanism*
  generalizes, but only the route reads are wired today.)
- **No sampler / no ranges spec.** No `scipy`/`SALib` dependency and nothing in `scripts/` samples;
  the parameter-space priors (distribution + bounds per uncertain param) exist only as prose here.
  `Axis.lo/hi/n` is a grid descriptor, not a sampling prior — a Saltelli design needs its own spec.
- **No analysis layer.** Running samples yields a table of LCOTs; computing first-order/total Sobol
  indices from it is unwritten (SALib's Saltelli sampler + analyzer is the usual pairing, and it
  fixes the sample count/layout).
- **Viz doesn't serve it.** `plots.py` traces LCOT-vs-`d_km` lines; sensitivity wants
  tornado / index-bar / scatter. Lowest priority (presentation).
- **Performance / output become the binding case for the deferred items below:** Stage-2
  vectorization (a Saltelli design over ~10 params is `N·(2d+2)` ≈ thousands of samples, each still
  running the inner optimize grid) and incremental artifact writes (`run.py` rebuilds
  `results/lcot.*` whole). Neither blocks a first run.

Suggested order: (1) generalize the override channel to any config leaf; (2) parameter-space spec +
Saltelli/Sobol generator; (3) Sobol-index analysis, then viz; (4) Stage-2 vectorization when run
time hurts.

## Vectorization (deferred until the grid is large)

**Stage 1 (done).** Any config parameter is sweepable/optimizable, not just `d_km`/`op_v_kn`.
Strategies read varying inputs through the `Point` resolver — `point.get(name, <config default>)` —
so a parameter becomes an axis simply by being read that way; `Route.d_km`/`op_v_kn` hold the
nominal fallback used when that axis isn't swept. `Point` records its reads, and `optimizer.run`
rejects an axis whose `param` no strategy consumes (instead of silently varying nothing).

**Stage 2 (deferred).** Replace the per-point Python grid loop with one vectorized strategy call
per case: `Point` carries whole-grid numpy arrays, the strategy broadcasts, `argmin` over the
optimize dims picks the winner, gather its full row. Cases stay a Python loop (they differ
structurally — source types, the `next(...)` source selection). Work involved:
- numpy-ify the kernel: `max`/`min` → `np.maximum`/`np.minimum` (`carried`, `BatterySource.size`,
  `crf`), `math.ceil` → `np.ceil` (`ContainerizedReactor.size`), `life_yr`'s `legs > 0` branch →
  `np.where`. The pure-arithmetic functions broadcast unchanged.
- the structural part: the six strategies' `if cargo <= 0: return _infeasible(...)` early-returns
  (and `tether_charge`'s `tethered_km <= 0` / speed-cap guard) become element-wise masks writing
  `lcot = inf`, with `np.errstate` over the masked-out garbage regions.
- branches on CONFIG scalars stay (`min_discharge_h > 0`, `fuel is not None`); only branches on
  GRID quantities become masks.
- output stays byte-identical: infeasible ⇒ `lcot = inf` AND extra fields → `NaN` (matches today's
  short `_infeasible` row after the column union). Verify by diffing against the scalar baseline.

Deferred on engineering grounds: at the current 8 cases / ~5k evals the scalar version is instant,
and the masks/`np.where` cost strategy readability. Worth it once the Sobol generator makes the
grid big enough to feel; nothing in stage 1 is thrown away by waiting.

## Speculative parameters

The newer cases (tender, containerized reactor, e-methanol) have little commercial precedent —
engineering estimates. Highest-leverage and most uncertain: the tender block (reactor/hull/O&M
cost, `parasitic_kw`, `tether.cable_*`), `route.standoff_nm`, `route.detach_duration_h` and
`route.detach_frac`, and the containerized-reactor block (`capex.usd_per_kw`,
`overhead.teu_per_mwe`, `pool.*`). Treat the new
cases' absolute LCOTs as order-of-magnitude until grounded in real data.

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
