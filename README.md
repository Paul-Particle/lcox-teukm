# lcox-teukm

A Tier-1 techno-economic model computing the **levelized cost of transport** (**LCOT**, US$ per
cargo-unit·km — US¢/TEU·km for the container platform) of ship-technology **cases** as a function
of **`D_max`**, the longest port-to-port hop on a route, so powertrains and energy strategies
compare on an absolute basis, not just as ratios.

The pipeline runs end-to-end: a hierarchical config of composable components → per-case cost
strategies → a cost-optimal speed search over a `D_max` sweep → a tidy results artifact →
figures. A standalone EU MRV fleet-data toolchain grounds the config in real ships.

## Quick start

Uses [uv](https://docs.astral.sh/uv/) (provisions Python 3.11+ automatically):

```sh
uv sync                        # install deps (pinned in uv.lock)
uv run scripts/run.py          # load config -> optimize the 8 cases over the D_max sweep -> results/lcot.{parquet,csv}
uv run scripts/plots.py        # LCOT- and speed-vs-D_max figures from the artifact -> results/*.{html,png}
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
  containerized reactor, tender — CAPEX, sizing, levelization). Holds its tech spec **and its
  energy cost model**. A Case bundles **zero or more** (zero = fueled-for-life converter).

**Case** (the verb) — a frozen composition + everything that isn't one of the three: a `params`
block (cross-case `economics` + `margins`; per-case `route`), a named **strategy**, and
`optimize`/`sweep` axis lists. Self-contained evaluation spec with no behavior of its own — a
the optimizer reads its declarations and drives sweep → optimize → strategy. A Case can be
multi-source (the tender case is also a battery case).

**Strategy** — a plain function `strategy(case, point) -> dict`, bespoke per case-type, named by
the Case. Segments the route, orchestrates the sources, sizes the stores, computes
`carried`/`legs_per_year`, returns the levelized cost (`lcot`) plus artifact fields. They live in
the `strategies/` package, one module per strategy.

**Optimizer** — `optimize(case, swept_point) -> dict` searches the Case's **free** axes
(sizing/dispatch) at one fixed swept point, keeping the min-`lcot` row. **`run(case)`** iterates
the **swept** axes (`D_max` by default), collecting one optimal row per point. The search is an
exhaustive grid (each `Axis` → `n` linearly-spaced points); swap in a real solver later without
touching the strategies.

### Module map

The model is flat modules under `scripts/`; nothing cross-imports beyond what is shown.

| Module | Role |
|---|---|
| `units.py` | unit conversions, single source of truth |
| `helpers.py` | shared only: `crf` + ship physics (`prop_power_kw`, `propulsion_factor`) |
| `schema.py` | frozen config schema (Platform / Drivetrain / `EnergySource` base / Case / Params / Axis) — passive structure mirroring `config.yaml` |
| `sources.py` | concrete `EnergySource` family (fuel / battery / reactor) + their cost methods (`size` / `levelize` / `usd_per_kwh` / `life_yr`) |
| `load_config.py` | YAML library + pandas CSV cases → built Cases (`dict[name → Case]`) |
| `strategies/` | package: one module per strategy (6) + `_shared.py` (scaffolding + route math `legs_per_year`/`carried`) |
| `optimizer.py` | `optimize` (free-param grid search) + `run` (sweep) |
| `run.py` | entry point → load → run → artifact |
| `plots.py` | LCOT- and speed-vs-`D_max` line figures + per-case cost-breakdown bars from the artifact |
| `style.py` | FCA house plotting style (template, palette, brand chrome) |
| `sync_excel.py` | bidirectional sync between `config.xlsx` and `config.yaml` + `cases.csv` |
| `mrv/` | standalone EU MRV fleet tooling (`mrv_unify`, `mrv_fleet`, `run_mrv`) — runs on its own, imports nothing from the model |

The two inputs into the schema — **`config.yaml`** (component library) and **`cases.csv`** (the
case table) — still carry some placeholder values (crew/O&M, a few route/axis parameters) pending
grounding; see [`docs/mrv_grounding.md`](docs/mrv_grounding.md) and **TODO.md**.

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
  carries no EnergySource; there is then no slow-steaming incentive (the optimizer pushes to the
  max feasible speed, traded only against sizing CAPEX).
- **Speed caps come from either axis** — the Drivetrain (an integrated reactor's power) or the
  EnergySource (iron-air's C/50 power limit; the tender's cable speed cap).

## Control flow

```
run.py ─ run(case) for each built Case
  └─ load_config (config.yaml + cases.csv ─> frozen dataclasses)
       └─ for each point in case.sweep (D_max by default):
            optimize(case, swept_point)
              └─ search the Case's free params
                   strategy(case, point) ─> row dict
                     ├─ segment the route; sources own their cost models
                     ├─ route math (carried, legs/yr) + helpers (crf, physics) ─> LCOT
                     └─ keep min-lcot ─> the point's row
            └─ write artifact (rows ─> Parquet + CSV)
```

## Cargo accounting (`carried`)

Computed by the strategy (arithmetic in `strategies/_shared.py`): draws capacity/deadweight from
the Platform and the slot/mass footprint from the EnergySources, then takes the volume-bound vs.
mass-bound minimum over asymmetric (head/back-haul) legs. May go ≤ 0 (store swamps the ship) →
infeasible.

## Configuration

Two inputs into the frozen schema:

- **`config.yaml`** — the reusable **component library** (hierarchical): `shared` (cross-case
  economics + margins), `platforms`, `drivetrains`, `sources`. `type:` is the loader's
  cost-model discriminator.
- **`cases.csv`** — the flat **case table** (tidy, read with pandas), one case per *group* of
  rows. Case-level scalars repeat on every row; `source` and the optimize/sweep axes are
  enumerated one per row (an extra source/axis is a continuation row). A blank `source` =
  fueled-for-life. Machine-generated later by a Sobol sweep; seeds hand-written for now.

Both inputs are also available as **`config.xlsx`** — a five-sheet workbook (shared /
platforms / drivetrains / sources / cases) that stays in sync with the text files via
`sync_excel.py`. Run `uv run scripts/sync_excel.py --help` to see the sync options.

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

`plots.py` adds two cost-breakdown figures (`results/cost_stack_{medium,ocean}.{html,png}`):
stacked bars of absolute LCOT by case at a fixed hop distance (a medium 2,000 km hop and a
14,000 km ocean crossing), each bar colored by case and each cost component read off its fill
pattern. The two share a y-axis so they compare directly; a case whose LCOT exceeds the cap
(long-haul LFP) overflows the frame and is labelled with its off-scale total.

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
open ocean tethered (the tender drives propulsion *and* recharges the coastal drain). In severe
seas the cable disconnects and the ship rides out the storm on battery (`storm_duration_h`).
The pack is sized only for the worst untethered stretch — `max(coastal transit, storm)` — so it
is far smaller than a port-swap pack; energy is priced at the tender's levelized $/kWh (its
annualized hull+reactor+O&M+fuel over the bus energy it pushes across the cable, including a
tethered/idle duty cycle); tethered speed is capped by the cable. `standoff_nm` defaults to the
12 nm UNCLOS territorial-sea minimum; ~200 nm tests a full-EEZ standoff.

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
