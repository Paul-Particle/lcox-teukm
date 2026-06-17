# lcox-teukm — model architecture

The target architecture for the post-teardown rebuild. This is the contract; the
code is mid-reconstruction and does not yet match it. Working principle: keep the
design clean, do not splint the old code together with adapters — rewrite to fit.

## Purpose

Compute the levelized cost of transport (**LCOT**, US$ per cargo-unit·km) of ship
technology **cases** as a function of `D_max` — the longest port-to-port hop on a
route — so powertrains and energy strategies can be compared on an absolute basis,
not just as ratios. The output is a results artifact; plotting and sensitivity
analysis consume it downstream and are out of scope for the rebuild's early steps.

## Principles

1. **Single source of truth for inputs.** A hierarchical YAML maps 1:1 onto frozen
   schema dataclasses. No flat/structured duplication, no hand-wired factory.
2. **The three axes are real boundaries.** Platform, Drivetrain, EnergySource are
   configured independently and composed, not special-cased by a central switch.
3. **EnergySources own their cost models.** Given the power/energy a strategy asks for,
   a source returns what's needed to compute LCOT — a flat price or a full
   levelization (tender, lease) — with no special-casing leaking elsewhere.
4. **Configuration is immutable.** The Optimizer explores by constructing trial design +
   dispatch points (sizing and operating choices); it never mutates the configured
   Case.
5. **Domain computes, presentation consumes.** The model emits a results artifact;
   tables/plots read it. The domain never imports plotting.

## Three nouns, a verb, and the behavior around it

### Nouns — configuration, frozen dataclasses loaded from YAML

- **Platform** — the hull and its cargo capacity. Carries the cargo-capacity
  dimension (`gross_capacity` in `cargo_unit`, `deadweight_t`, load factors) and
  hull CAPEX/life. This is what makes the binding cargo metric platform-specific
  (container = volume-bound TEU slots; bulk = mass-bound tonnes).
- **Drivetrain** — energy → shaft, **including the integral powerplant's CAPEX**:
  the engine (fossil) or the *integrated* reactor (reactor+steam+shaft for direct
  drive; reactor+generator+motor for integrated-electric). Propulsion factor,
  drive/hotel efficiencies, converter CAPEX + life, tug cost. May impose a speed cap
  (an integrated reactor's power rating).
- **EnergySource** — *one* energy-supplying technology. **Thin** when the supply is a
  commodity (fossil fuel, fission fuel — just a price, folded in), **full** when the
  supply is separable hardware (swappable battery, containerized reactor, tender —
  carrying CAPEX, sizing, levelization). Holds its tech spec **and its energy cost
  model**, and may impose a speed cap (iron-air's power limit, the tender's cable
  cap). A Case bundles **zero or more** — zero when the converter is fueled-for-life
  (all cost in the drivetrain's CAPEX, no variable energy).

### Verb

- **Case** — a frozen composition and the **single place a strategy looks**: one Platform +
  one Drivetrain + **zero or more EnergySources**, plus **everything that isn't one of those
  three**. That "everything" is a `params` block — cross-case `economics` + `margins` (one
  of each, referenced by every case) and a per-case `route` (general — `load_factor`,
  `load_factor_imbalance`, `design_v_kn`; strategy-specific — `standoff_nm`,
  `storm_duration_h`, `idle_h`) — plus a named **strategy** and `optimize` / `sweep` axis
  lists (each an `Axis(param, lo, hi, n)`): **free** axes the optimizer searches to argmin
  LCOT (e.g. `op_v_kn`, whose bounds — the former `v_min/v_max` — live here), and **swept**
  axes the outer runner iterates to trace LCOT vs. X (`D_max` by default). `economics` /
  `margins` / `route` are just sub-dataclasses under `params` (hierarchy headings), not
  separate top-level nouns. So a
  Case is a **complete, self-contained evaluation spec** — evaluating it yields a whole
  results table (sweep × per-point optimum) — and it has no behavior of its own: a generic
  runner reads its declarations and drives sweep → optimize → strategy. A Case can be
  multi-source: the nuclear-tender case is *also* a battery case (onboard buffer + at-sea
  charger), so the architecture supports N sources from the start.

### Behavior

- **Strategy** — a plain **function** `strategy(case, point) -> dict`, named and deliberately
  bespoke per case-type (`fuel_burn`, `port_swap_battery`, `tether_charge`, `reactor_direct`,
  `reactor_electric_integrated`, `reactor_electric`); the Case names the one it uses. `point`
  is a small dict of the parameter-space coordinates the optimizer is at (e.g. `d_km`,
  `op_v_kn`). The strategy segments the route, orchestrates the bundled sources (which serves
  what), sizes the stores, computes `carried` / `legs_per_year`, and returns a dict: the
  levelized cost (`lcot`) plus extra numbers for the artifact. A source often implicitly
  *suggests* a strategy but does not lock the Case in.
- **Optimizer** — a generic **function** `optimize(case, swept_point) -> dict`: at one fixed
  swept point it searches the Case's free inputs (sizing and/or dispatch), calling the
  strategy for each trial and keeping the min-`lcot` row. The outer **`run(case)`** iterates
  the Case's swept points, collecting one optimal row each into the results table.

### Primitives

- **helpers.py** — only *genuinely shared* stateless computation, drawn on by **both**
  the strategies and the EnergySource cost models: cost/finance math (`crf` — every
  CAPEX-bearing source/strategy needs it) and the ship physics that sizing leans on (the
  admiralty cube-law `prop_power_kw` and the `propulsion_factor` product, which a source
  that sizes to shaft/bus power — the tender — may also need). `crf` is not physics, which
  is why the module is **helpers**, not physics. Route-execution arithmetic that *only* a
  strategy uses (`legs_per_year`, `carried`) does **not** live here — see `strategies.py`.
- **units.py** — every unit conversion, and only here.

## Where cost lives — the integration rule

CAPEX follows integration; the EnergySource is thin for a commodity and full for
separable hardware.

- **Integral converter** (engine, integrated reactor, built-in battery) → CAPEX on
  the **Drivetrain**; the **EnergySource is thin** (a commodity price, folded in) or
  **absent** (fueled-for-life → no variable energy cost).
- **Separable supply** (containerized reactor, tender vessel, swappable battery
  containers) → the **EnergySource carries the CAPEX + a full cost model** (sizing,
  levelization, logistics).

| Case | Drivetrain (converter CAPEX) | EnergySource(s) |
|---|---|---|
| fossil | mech-fossil (engine) | VLSFO — thin |
| nuclear, integrated direct | mech-nuclear (reactor+steam+shaft) | fission fuel — thin (or none) |
| nuclear, integrated electric | electric-nuclear (reactor+gen+motor) | fission fuel — thin (or none) |
| nuclear, containerized | electric (motor) | containerized-reactor — full |
| tender | electric (motor) | battery + tender-reactor — full |
| battery (port-swap) | electric (motor) | battery — full (grid charge price folded in) |

**Owned vs. leased reactors collapse** — under fleet-scale utilization the levelized
cost is identical, so each reactor has a single cost model.

**No-energy-source cases.** A fueled-for-life reactor (tender thorium; optionally an
integrated SMR) has no marginal energy cost, so the Case carries no EnergySource.
There is then no slow-steaming incentive: the Optimizer pushes to the maximum
feasible speed, traded only against sizing CAPEX.

**Speed caps come from either axis** — the Drivetrain (an integrated reactor's power)
or the EnergySource (iron-air's C/50 power limit; the tender's cable speed cap).

## Control flow

```
run.py ─ run(case) for each built Case
  └─ load config (YAML ─> frozen dataclasses)
       └─ build Cases (Platform × Drivetrain × [EnergySource…] × strategy name
            │            + fixed params + free-param & swept-param declarations)
            └─ for each point in case.sweep:                  (D_max by default; a Case may
                 optimize(case, swept_point)                   declare extra swept axes — eases
                   └─ search the Case's free params            the planned Sobol work, not a
                        strategy(case, point) ─> row (dict)     near-term priority)
                          ├─ segment the route
                          ├─ EnergySources own their cost models
                          ├─ route math (carried, legs/yr) + helpers (crf, physics) ─> LCOT
                          └─ keep min-lcot ─> the point's row
            └─ write artifact  (rows ─> Parquet, CSV option)
```

## Optimization

- **Objective:** minimize LCOT.
- **Free vs. swept — the Case declares both.** The Optimizer searches only the **free**
  params, at one fixed **swept** point handed to it. The **swept** params (`D_max` by
  default) are iterated by an outer runner, *not* the Optimizer. Because both live on the
  Case, it is self-describing: a runner needs nothing beyond the Case to produce its full
  results table.
- **Free variables (near-term):** operating/service speed (almost always) and
  reactor size (reactor-bearing cases). Installed power is *derived* from the
  chosen speed (+ a sea/weather margin) — this removes the old design-speed vs.
  cruise-speed split, where CAPEX and operating point were decoupled. `v_max` is the
  ceiling on the design speed the optimizer may choose (by sizing the reactor);
  drivetrains whose power we don't sweep get a sensible fixed default.
- **No energy cost ⇒ go fast.** With no variable energy cost (fueled-for-life), the
  speed search has no slow-steam incentive; the optimum is the fastest feasible speed
  the sizing CAPEX justifies.
- **Two regimes, set by the flags.** Optimizable inputs split into *investment*
  (e.g. reactor size) and *operating/dispatch* (e.g. service speed). With
  investment inputs flagged free the Optimizer runs **joint investment + dispatch**;
  with them fixed it runs **dispatch-only**. Both fall out of which inputs a Case
  flags free (one declared vocabulary; per-case flags + bounds).
- The design space is small enough for a simple grid / search for now.

## Cargo accounting (`carried`)

Computed by the **strategy as it assembles cost** — and the arithmetic *lives in*
`strategies.py` (strategy-only route math), not in `helpers.py`. It draws capacity and
deadweight from the **Platform** and the slot/mass footprint from the **EnergySources**,
then takes the volume-bound vs. mass-bound minimum over asymmetric (head/back-haul) legs.
May go ≤ 0 (the store swamps the ship) → infeasible. `legs_per_year` lives the same way:
a `strategies.py` function the strategy calls to annualize the route.

## Configuration layout

Hierarchical YAML, populated into frozen schema dataclasses (`data_classes.py` — the
old flat inventory regrouped by axis):

```yaml
shared:        # cross-case economics + margins (discount rate, crew cost, weather/sea margins)
platforms:     # container (TEU)  [bulk (tonne) when it earns its keep]
drivetrains:   # mechanical-fossil | mechanical-nuclear | electric
sources:       # VLSFO | LFP | iron-air | SMR-* | mobile-tender-reactor | …
cases:         # named: platform + drivetrain + [sources] + strategy + a `route` block
               #        + `optimize` axes (op_v_kn search) + `sweep` axes (D_max range, …)
```

## Output artifact

A tidy table, one row per (case, `D_max`, and any other swept input): LCOT, optimal
speed, reactor size (where applicable), the energy / capital / O&M breakdown,
energy-store size (battery slots & kWh), and a feasibility flag. **Parquet**
primary, **CSV** optional. Designed for large sweeps — potentially thousands of
rows — and for **incremental generation/update** (append / partitioned writes),
not full regeneration on every run.

## Terminology hygiene

- **Frozen dataclasses for config, plain dicts for runtime data.** The loaded config is
  frozen dataclasses (Platform, Drivetrain, EnergySource, Case, and its `params` sub-blocks
  economics/margins/route) — immutable, validated, safely shared across the sweep. Transient runtime data
  is plain dicts: the `point` the optimizer passes in and the cost `row` the strategy returns
  (rows go straight to the Parquet artifact, so a class would be ceremony). We don't name the
  strategy's internal route computation, and there is no `Journey` / `Result` / `Point` type.
- **Verbs are functions.** The strategies, `optimize`, `run` are plain functions. The *one*
  method-bearing exception is **EnergySource**, which carries its own cost methods (`size` /
  `levelize` / `usd_per_kwh`) because that cost model is polymorphic by source type. No
  `.evaluate()` / `.execute()` elsewhere.
- The noun is **EnergySource**; its computation is its **energy cost model**. We do
  **not** use the word "supply".
- **`params`** = the Case's non-component inputs, a sub-block of three: **`economics`** +
  **`margins`** (cross-case, by reference) and **`route`** (per-case fixed route/condition
  params). Reached via the case (`case.params.economics` / `.margins` / `.route`).
- **Strategy** = a named function `(case, point) -> dict`. **Optimizer** = the function
  `optimize(case, swept_point)` searching free inputs for min `lcot`. **`run`** = the outer sweep.
- **helpers.py** = only *shared* computation — `crf` + the ship physics sizing leans on
  (`prop_power_kw`, `propulsion_factor`); named helpers (not physics) since `crf` is not
  physics. Strategy-only route math (`legs_per_year`, `carried`) lives in `strategies.py`.

## Module map — target vs. current debris

| Target            | Role                                              | Current status |
|-------------------|---------------------------------------------------|----------------|
| `units.py`        | unit conversions                                  | keep as-is |
| `helpers.py`      | shared only: `crf` + ship physics (`prop_power_kw`, `propulsion_factor`) | done (rewritten against the new schema; renamed from `physics.py`/`energy.py`) |
| `data_classes.py` | config schema: Platform / Drivetrain / EnergySource / Case + its `Params` (Economics / Margins / Route) + `Axis` | nouns + Case + Params/Axis present (no top-level Config); source cost models to move onto EnergySource |
| `load_config.py`  | thin YAML → schema loader                         | exists |
| `config.yaml`     | hierarchical input                                | draft; 8 seed `cases:` (some placeholder values) |
| `strategies.py`   | the 6 per-case strategy functions `(case, point) -> dict`, + strategy-only route math (`legs_per_year`, `carried`) | all 6 drafted (`fuel_burn`, `port_swap_battery`, `tether_charge`, `reactor_direct`, `reactor_electric_integrated`, `reactor_electric`); they define the source interface via `# NEEDS` |
| `optimizer.py`    | the `optimize` (free-param search) + `run` (sweep) functions; both take/return plain dicts | to create from scratch (old `determine_cost.py` deleted) |
| `run.py`          | entry point → load config → `run(case)` → artifact | fully stale |
| `plots.py`, `style.py` | presentation                                 | deferred until an artifact exists |
| (energy sources)  | the EnergySource cost methods (`size`, `levelize`, `usd_per_kwh`, …) | to build from scratch against the strategies' `# NEEDS` (old `supply.py` deleted) |

## Open / deferred decisions

- **Source roles** in multi-source cases: a plain list for now; natural roles
  (buffer / charger / …) may emerge as we write the cases.
- **Strategy ↔ Optimizer boundary:** resolved for now — the strategy owns the whole
  per-point cost (segments the route, sizes the stores, computes `carried`/`legs`, assembles
  LCOT) and returns a row dict; `optimize` only *searches* the free inputs over points and
  compares `lcot`. Revisit if a case needs the optimizer to see partial structure.
- **EnergySource cost-model interface:** the exact signature and the shape of the
  data a source returns to the strategy (the `# NEEDS` lines in `strategies.py` are the
  current working spec). Settled so far: `BatterySource.size` + `life_yr` (shared by
  `port_swap_battery` and `tether_charge`); `FuelSource.usd_per_kwh` (shared by `fuel_burn`
  and the integrated-reactor strategies). The **integrated** reactors (`reactor_direct`,
  `reactor_electric_integrated`) carry the reactor as Drivetrain CAPEX — no source method.
- **Split ReactorSource → `TenderReactor` + `ContainerizedReactor` (APPLIED; cost methods pending).**
  A thin `ReactorSource` base holds the shared reactor block (capex, thermal fuel price,
  thermal→electric efficiency); the two subtypes add their integration-specific fields. The
  loader dispatches on `tether` present (= tender) and the strategies' `isinstance` checks
  match the subtype. Still TODO: the cost methods — `TenderReactor.levelize(bus_kw,
  tethered_h, idle_h, …)` (cable + reposition duty cycle) and `ContainerizedReactor.size(bus_kw,
  …) -> (usd_per_kwh, reactor_kw, slots)` (pool utilization + a `teu_per_mwe` slot footprint).
- **Extra swept axes** beyond `D_max` (to ease later Sobol exploration): structure
  for it, but low priority.
- **Modular flexibility / option value is out of scope (way down the line).** LCOT
  here is a deterministic, single-route, steady-state *floor*. It credits reactor
  **sharing** — one tender (or containerized module) amortized over many ship-hours —
  through the duty cycle in the levelized $/kWh. It does **not** credit reactor
  **flexibility**: reallocating a scarce reactor across heterogeneous, time-varying
  demand (long vs. short routes), independent reactor/hull/battery lifetimes, and
  graceful degradation/redundancy. That option value is real and systematically
  **under-credits the modular cases** (tender, containerized) relative to integrated
  nuclear, but pricing it needs a stochastic *fleet-level* simulation with a realistic
  route/demand mix — far beyond this model. Read the modular cases' LCOT as a floor
  with an unpriced option premium on top, not as the last word vs. integrated.
