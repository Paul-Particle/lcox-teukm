# lcox-teukm

A Tier-1 techno-economic model computing the **levelized cost of transport** (**LCOT**, US$ per
cargo-unit·km — US¢/TEU·km for the container platform) of ship-technology **cases** as a function
of **`D_max`**, the longest port-to-port hop on a route, so powertrains and energy strategies
compare on an absolute basis, not just as ratios.

The pipeline runs end-to-end: a component library + case compositions in `assumptions.yaml`, a study
in `studies.yaml` naming which parameters vary and how → a vectorized kernel that evaluates a
whole block per case in one broadcast call and collapses the cost-optimal speed lever → a tidy
results artifact / Sobol indices → figures. A standalone EU MRV fleet-data toolchain grounds the
config in real ships.

## Quick start

Uses [uv](https://docs.astral.sh/uv/) (provisions Python 3.11+ automatically):

```sh
uv sync                        # install deps (pinned in uv.lock) + the project itself (editable)
uv run scripts/run.py          # render the `fleet` study: 8 cases x speed lever x D_max sweep -> results/lcot.{parquet,csv}
uv run scripts/study.py        # run the sensitivity studies in studies.yaml -> results/sobol/<study>/
uv run scripts/viz/plots.py    # LCOT/speed-vs-D_max + Sobol/lever figures from the artifacts -> results/*.{html,png}
uv run scripts/mrv/run_mrv.py  # (optional) ground config anchors in EU MRV fleet data
```

`run.py` reports e.g. `288 rows across 8 cases (261 feasible)`. The MRV step needs the public
data files in `data/` — see [Grounding in real data](#grounding-in-real-data-eu-mrv).

## Architecture — three axes, a verb, a behavior

Every powertrain is a composition of three independently-configured, frozen dataclasses, loaded
1:1 from a hierarchical YAML (no hand-wired factory):

- **Platform** — hull + cargo capacity. Carries the cargo-capacity dimension (`gross` in
  `cargo_unit`, `deadweight_t`, load factors) and hull CAPEX/life. Makes the binding cargo
  metric platform-specific (container = volume-bound TEU slots; bulk = mass-bound tonnes).
- **Drivetrain** — energy → shaft, **including the integral powerplant's CAPEX**: the engine
  (fossil) or an *integrated* reactor (reactor+steam+shaft direct-drive; reactor+gen+motor
  integrated-electric). Propulsion factor, drive/hotel efficiencies, converter CAPEX, tug cost.
- **EnergySource** — *one* energy-supplying technology. **Thin** for a commodity (fossil/fission
  fuel — just a price, folded in), **full** for separable hardware (swappable battery,
  containerized reactor, tender — CAPEX, sizing, levelization). Holds its tech spec as **pure
  data**; the cost/sizing that reads it lives in `model/costing.py`. A Case bundles **zero or
  more** (zero = fueled-for-life converter).

**Case** (the verb) — a frozen composition + the cross-case `params` block (`economics`,
`margins`, load factors, and the voyage scalars `d_km` / `op_v_kn` / `design_v_kn`) and a named
**strategy**. Self-contained evaluation spec with no behavior of its own; it declares no axes —
what varies is a study's concern, not the case's. A Case can be multi-source (the tender case is
also a battery case).

**Strategy** — a plain function `strategy(case) -> dict`, bespoke per case-type, named by the
Case. Pure arithmetic that **broadcasts over whichever config leaves are scalars or arrays**:
segments the route, orchestrates the sources, sizes the stores, computes `carried`/`legs_per_year`,
returns the levelized cost (`lcot`) plus artifact fields. They live in the `strategies/` package,
one module per strategy.

**Study** — the driver, in `studies.yaml`. A study assigns each parameter a **role** over any
config leaf: `fix` (a scalar), `sample` (a Saltelli column on the shared sample dim), `sweep` (a
retained condition grid), `optimize` (an argmin-collapsed lever grid). `ingest` places those as
array leaves on the config, `evaluate` runs one broadcast kernel call per case and collapses the
lever, `analyze` variance-decomposes per swept slice. No parameter is privileged: `op_v_kn` and
`d_km` are ordinary leaves that the `fleet` study happens to optimize and sweep.

### Module map

`scripts/` is grouped into packages by role, with dependencies pointing downward
(`common` ← `assumptions`/`model` ← `kernel` ← `viz`). `scripts/` is the source root: `uv sync`
editable-installs the packages (see `pyproject.toml`), so `from common.paths import ...` resolves
at runtime from any directory and for static tooling — no `sys.path` manipulation. The flat
entry points (`run.py`, `study.py`) and the by-path scripts (`viz/plots.py`, `mrv/`) are invoked
with `uv run`; everything else is imported.

| Module | Role |
|---|---|
| **`common/`** | shared vocabulary + math — the foundation everything imports |
| `common/schema.py` | frozen config schema (Platform / Drivetrain / the `EnergySource` family: fuel / battery / reactor / Case / Params / Axis) — passive structure mirroring `assumptions.yaml` |
| `common/units.py` | unit conversions, single source of truth |
| `common/helpers.py` | shared only: `crf` + ship physics (`prop_power_kw`, `propulsion_factor`) |
| `common/paths.py` | canonical repo/input/output paths, derived once so no module counts `parents[...]` levels |
| **`assumptions/`** | the two YAML inputs → typed objects |
| `assumptions/load_assumptions.py` | YAML library + `cases:` → built Cases (`dict[name → Case]`); harvests `{value, range}` sampling priors |
| `assumptions/studies.py` | parse `studies.yaml` into `Study` role assignments; resolve each sampled leaf's range from config |
| **`model/`** | the cost / sizing model |
| `model/costing.py` | per-source cost/sizing **functions** (`battery_size` / `battery_life_yr` / `fuel_usd_per_kwh` / `containerized_reactor_size` / `tender_levelize`) over the schema source records |
| `model/strategies/` | package: one module per strategy (6) + `_shared.py` (scaffolding + route math `legs_per_year`/`carried`) |
| **`kernel/`** | the vectorized study → results pipeline |
| `kernel/ingest.py` | place a study's roles as array-valued config leaves → a `Design` (member cases + block layout + SALib problem) |
| `kernel/evaluate.py` | one broadcast kernel call per case; argmin-collapse the lever dims → one xarray `Dataset` per case |
| `kernel/analyze.py` | Sobol first-order/total indices (SALib) per swept slice + feasibility reporting |
| `kernel/store.py` | persist block + samples + indices + feasibility + a spec snapshot under `results/sobol/<study>/` |
| **`viz/`** | presentation |
| `viz/plots.py` | LCOT/speed-vs-`D_max` lines, per-case cost-breakdown bars, Sobol-index bars, lever-landscape curves |
| `viz/style.py` | FCA house plotting style (template, palette, brand chrome) |
| **entry points** (flat) | |
| `run.py` | render the `fleet` study → `results/lcot.{parquet,csv}` |
| `study.py` | run studies.yaml studies → Sobol indices under `results/sobol/` |
| `mrv/` | standalone EU MRV fleet tooling (`mrv_unify`, `mrv_fleet`, `run_mrv`) — runs on its own, imports only `common.units` (+ best-effort `viz.style`), nothing from the model |

The two inputs — **`assumptions.yaml`** (component library + cases + shared scalars) and
**`studies.yaml`** (role assignment) — still carry some placeholder values (crew/O&M, a few tender
parameters) pending grounding; see [`docs/mrv_grounding.md`](docs/mrv_grounding.md) and **TODO.md**.

## The cases & the integration rule

CAPEX follows integration: the EnergySource is **thin** for a commodity, **full** for separable
hardware.

| Case | Drivetrain (converter CAPEX) | EnergySource(s) |
|---|---|---|
| fossil | mech-fossil (engine) | VLSFO — thin |
| e-methanol | mech-fossil (engine) | e-methanol — thin (placeholder price) |
| nuclear-direct | mech-nuclear (reactor+steam+shaft) | fission fuel — thin (or none) |
| nuclear-int-el | electric-nuclear (reactor+gen+motor) | fission fuel — thin (or none) |
| nuclear-cont | electric (motor) | containerized reactor — full |
| tender | electric (motor) | battery + tender reactor — full |
| lfp / iron-air | electric (motor) | battery — full (grid charge folded in) |

- **Owned vs. leased reactors collapse** — at fleet-scale utilization the levelized cost is
  identical, so each reactor has a single cost model (the pooled-utilization $/kWh below).
- **No-energy-source cases.** A fueled-for-life reactor has no marginal energy cost, so the Case
  carries no EnergySource; there is then no slow-steaming incentive (the speed lever collapses to
  the max feasible speed, traded only against sizing CAPEX).
- **Speed caps come from either axis** — the Drivetrain (an integrated reactor's power) or the
  EnergySource (iron-air's C/50 power limit; the tender's cable speed cap).

## Control flow

```
run.py / study.py ─ pick a study from studies.yaml
  └─ load_assumptions (assumptions.yaml ─> frozen dataclasses + sampling ranges)
       └─ ingest.build_study(study, raw)
            ├─ draw the Saltelli sample matrix (if any leaf is sampled)
            └─ place every role as an array leaf on the config, rebuild once ─> Design
                 └─ evaluate.evaluate_design(design)      # one broadcast call per case
                      strategy(case) ─> block of measures over (sample x swept x lever)
                        ├─ segment the route; sources own their cost models
                        ├─ route math (carried, legs/yr) + helpers (crf, physics) ─> LCOT
                        └─ argmin the objective over the lever dims, carry every measure
                 ├─ run.py:   flatten the fleet datasets ─> results/lcot.{parquet,csv}
                 └─ study.py: analyze.sobol_indices per swept slice ─> store ─> results/sobol/
```

## Cargo accounting (`carried`)

Computed by the strategy (arithmetic in `strategies/_shared.py`): draws capacity/deadweight from
the Platform and the slot/mass footprint from the EnergySources, then takes the volume-bound vs.
mass-bound minimum over asymmetric (head/back-haul) legs. May go ≤ 0 (store swamps the ship) →
infeasible.

## Configuration

Two inputs, cleanly split between *what exists* and *what varies*:

- **`assumptions.yaml`** — the component library + the model's parameters. `shared` (cross-case
  economics, margins, load factors, and the voyage scalars `d_km` / `op_v_kn` / `design_v_kn`),
  `platforms`, `drivetrains`, `sources`, and `cases:` (pure compositions — platform + drivetrain +
  sources + strategy, no route). `type:` is the loader's cost-model discriminator. A value may be
  wrapped `{value:, range: [lo, hi], dist:}` to declare a sampling prior; the model reads only the
  `value`, and a study samples against the `range`.
- **`studies.yaml`** — role assignment over those parameters. Each study names which config leaves
  are `sample`d / `fix`ed / `sweep`t / `optimize`d (all addressing the same dotted paths, e.g.
  `shared.op_v_kn`, `sources.lfp.capex.usd_per_kwh`). The `fleet` study is the baseline sweep
  (all cases, speed lever, D_max condition, no sampling) that `run.py` renders to `lcot.csv`.

Units throughout: energy kWh, power kW, time h, distance km, speed kn, mass kg, money US$.

## The comparison axis (`D_max`) and speed

`D_max` is the longest hop between swap-capable ports (km). For battery ships it sets the required
pack size, driving CAPEX and the cargo slots displaced. Everything that scales the ships together
(load factor, port time, route geometry) is held at representative values so the model reads
**absolute LCOT**. Speed is optimized per ship: battery ships have an extra incentive to slow down
(less energy/km → smaller pack → fewer displaced slots + less CAPEX); iron-air's 100-h discharge
rating makes its pack power-bound, pinning it near minimum speed; the nuclear ships' cheap fuel +
expensive capital push them to maximum speed.

## Output artifact

`run.py` writes a tidy table (`results/lcot.parquet` + `results/lcot.csv`), one row per (case,
`D_max`, any other swept input): LCOT, optimal speed, reactor/store size, the
energy/capital/O&M breakdown, and a feasibility flag. The annualized cost is itemized into
`cost_hull` / `cost_powerplant` / `cost_store` / `cost_crew` / `cost_om` / `cost_energy`
(US$/yr; the first five sum to `annual_fixed`, the last equals `annual_energy`) — these drive
the cost-breakdown bars. The modular reactors (nuclear-cont, tender) levelize their reactor
CAPEX into a per-kWh rate, so that capital sits in `cost_energy`, not `cost_powerplant`. Columns
are unioned across the heterogeneous strategy rows (absent fields are NaN). It is regenerated
whole each run; incremental/partitioned writes for large sweeps are a TODO.

`plots.py` also draws cost-breakdown figures (`results/cost_stack_{medium,ocean}.{html,png}`):
stacked bars of absolute LCOT by case at a fixed hop distance (a medium 2,000 km hop and a
14,000 km ocean crossing), each bar colored by case (hue) and each cost component read off its
shade (lighter up the stack). The two share a y-axis so they compare directly; a case whose LCOT
exceeds the cap (long-haul LFP) overflows the frame and is labelled with its off-scale total.

From the sensitivity studies it draws two more (reading `results/sobol/<study>/`): **Sobol-index
bars** (`sobol_<study>.{html,png}`) — grouped horizontal S1/ST bars with bootstrap-CI whiskers,
small-multipled across the swept slices — and a **lever landscape** (`lever_landscape.{html,png}`)
— LCOT vs. the operating-speed lever per case at a fixed hop, with each case's optimum starred.

## Grounding in real data (EU MRV)

`scripts/mrv/` is a standalone toolchain (imported by nothing in the model) that turns the public
EU MRV (THETIS-MRV) fleet emissions reports into grounded anchors for the config:

- **`mrv_unify.py`** — concatenates the yearly workbooks into one lossless
  `data/mrv_unified.{parquet,csv}` (all ship types, every row and column, with provenance and a
  reversible header-normalization map carried in the file metadata).
- **`mrv_fleet.py`** — derives the container-fleet distributions (operating speed, useful power,
  energy intensity, ship size) and fits the size-scaling relations.
- **`run_mrv.py`** — runs both in order.

Findings and the proposed parameter grounding + ship-scale-factor design are written up in
[`docs/mrv_grounding.md`](docs/mrv_grounding.md). The data files are gitignored; download the
public reports from the [EU MRV portal](https://mrv.emsa.europa.eu/#public/emission-report)
into `data/`.

## Concept notes

Two cases rest on operational concepts that aren't yet commercial. The reactor in both is an
**AMPERA-class** micro-reactor (thorium TRISO, subcritical, sCO₂ cycle ~50% thermal→electric,
~30 MWe net per two-core module in ~36 TEU of footprint; refuels every few decades).

**Mobile nuclear tender (dedicated escort).** An uncrewed nuclear tender recharges a
battery-electric ship *at sea*: the ship runs untethered on battery through coastal/territorial
waters (`standoff_nm`), meets the tender at the regulatory border, then cables up and crosses the
open ocean tethered (the tender drives propulsion *and* recharges the coastal drain). The tether
is a floating cable that detaches in heavy seas; the ship then sails on battery for an expected
fraction `detach_frac` of the tethered hours (billed into energy as an expected-value drain), and
the pack is sized for the longest single detached stretch `detach_duration_h`. The pack covers the
worst untethered stretch — `max(coastal transit, detach_duration)` — so it is far smaller than a
port-swap pack; energy is priced at the tender's levelized $/kWh (its annualized
hull+reactor+O&M+fuel over the bus energy it pushes across the cable, including a tethered/idle
duty cycle); tethered speed is capped by the cable. `standoff_nm` defaults to the 12 nm UNCLOS
territorial-sea minimum; ~200 nm tests a full-EEZ standoff.

**Containerized (pooled) reactor.** A containerized nuclear-electric ship loads reactor modules
at port and returns them to a shared pool on arrival. The reactor's CAPEX + thermal fuel is
recovered through a per-kWh rate levelized over the reactor's **own** pool utilization (not one
ship's duty cycle), so a pooled reactor isn't charged for sitting idle during a ship's port
calls — a large win on short hops, negligible on long ones. (This is the model that collapses the
old owned-vs-leased distinction into one cost model.)

## Modular flexibility is out of scope (a known floor)

LCOT here is a deterministic, single-route, steady-state *floor*. It credits reactor **sharing**
(one tender/module amortized over many ship-hours, via the duty cycle) but **not** reactor
**flexibility** (reallocating a scarce reactor across heterogeneous, time-varying demand;
independent lifetimes; redundancy). That option value is real and systematically under-credits
the modular cases (tender, containerized) vs. integrated nuclear, but pricing it needs a
stochastic fleet-level simulation far beyond this model. Read the modular cases' LCOT as a floor
with an unpriced option premium on top.

## Glossary

- **LCOT** — levelized cost of transport: total annualized cost ÷ annual cargo·distance. The headline metric.
- **TEU** — twenty-foot equivalent unit; one standard container "slot." Hull capacity and battery containers are counted in TEU.
- **D_max** — the longest hop between swap-capable ports (km); the comparison axis. Sets battery size, hence CAPEX and displaced cargo.
- **Headhaul / backhaul** — the two directions of a round trip; trade is directionally imbalanced (`load_factor_imbalance`).
- **Load factor** — average fraction of cargo slots actually filled (≈0.8).
- **Deadweight (DWT)** — the mass a ship can carry; batteries/bunkers eat into it (the mass constraint).
- **Reefer / hotel load** — refrigerated containers / non-propulsion electrical load (reefers, accommodation, ship systems).
- **Slow steaming** — sailing below design speed to cut energy (power ∝ speed³); the basis for optimizing operating speed per ship.
- **Sea margin** — extra installed power reserve (~15%) for real-world weather/hull-fouling vs. calm-water trials.
- **Propulsion factor** — itemized fractional reduction in propulsion power (hull form, coatings, propeller/pods, motor efficiency, weather routing); the product scales propulsion power.
- **Admiralty (cube) law** — propulsion power scales as speed³ (`prop_power_kw`).
- **DoD / RTE / C-rate** — depth of discharge / round-trip efficiency / discharge rate relative to capacity (C/50 = full discharge over 50 h; iron-air is power-limited).
- **Tender** — a support vessel; here the mobile nuclear reactor recharging battery ships at sea.
- **EEZ** — Exclusive Economic Zone (~200 nm); a regulatory standoff the tender stays clear of.
- **nm / knot** — nautical mile (1.852 km) / one nautical mile per hour.
- **VLSFO / SMR / HALEU** — very low sulfur fuel oil / small modular reactor / high-assay low-enriched uranium.
- **EU MRV / THETIS-MRV** — the EU's Monitoring, Reporting & Verification scheme for ship CO₂; the public per-ship dataset used to ground the config.

## License

Released under the [MIT License](LICENSE).
