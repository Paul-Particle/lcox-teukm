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
3. **EnergySources own their cost models.** Given a Journey (or other parameters)
   a source returns what's needed to compute LCOT — a flat price or a full
   levelization (tender, lease) — with no special-casing leaking elsewhere.
4. **Config is immutable.** The Optimizer explores by constructing trial design +
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

- **Case** — a frozen composition: one Platform + one Drivetrain + **zero or more
  EnergySources** + a named **Strategy** + fixed parameters + a declaration of which
  inputs are **free** (the optimizer searches them to argmin LCOT, with bounds) and
  which are **swept** (an outer runner iterates them, with their ranges, to trace LCOT
  vs. X — `D_max` by default). A Case is therefore a **complete, self-contained
  evaluation spec**: evaluating it yields a whole results table (sweep × per-point
  optimum), not a single number. It stays **pure frozen data** — a generic runner reads
  the declaration and drives sweep → optimize → strategy; the Case has no behavior of its
  own. A Case can be multi-source: the nuclear-tender case is *also* a battery case
  (onboard buffer + at-sea charger), so the architecture supports N sources from the start.

### Behavior

- **Strategy** — a plain **function** `strategy(case, point) -> Result`, named and
  deliberately bespoke per case-type (e.g. `fuel-burn`, `battery-swap`, `reactor-direct`,
  `reactor-electric`, `tether-charge`); registered by name, and the Case names the one it
  uses. It designs the **Journey** for the point and orchestrates the bundled sources
  (which source serves what), computing `carried` / `legs_per_year` and assembling the
  cost. A source often implicitly *suggests* a strategy but does not lock the Case in.
- **Journey** — the **resolved dispatch plan** for one hop (a frozen dataclass the
  Strategy produces at runtime): route segments (e.g. coastal/untethered vs. tethered
  open-ocean), storm exposure, speed policy. **Not** a configuration axis — there is no
  `Scenario` config section, and the Case's fixed route/condition params live in its
  `route` block (a different thing); route-reshaping is the Strategy's job, expressed as a
  Journey.
- **Optimizer** — a generic **function** `optimize(case, swept_point) -> Result`: at one
  fixed swept point it searches the Case's flagged free inputs (sizing and/or dispatch),
  calling the strategy for each trial point, and returns the cost-optimal Result. The
  outer **`run(case)`** function iterates the Case's swept points and collects the table.

### Primitives

- **physics.py** — pure ship physics & logistics arithmetic: propulsion cube law,
  per-leg energy, `legs_per_year`, and the `carried` cargo accounting. Stateless;
  called by the strategy as it assembles cost. (Renamed from `energy.py` to stop
  colliding with the EnergySource axis wording.)
- **helpers** (`helpers.py`) — shared cost/finance math (`crf` and friends) needed
  by *both* the strategies' cost assembly and the EnergySources' cost models;
  likely to accrete other common functions.
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
                        strategy(case, point) ─> Result        near-term priority)
                          ├─ design the Journey (route segments)
                          ├─ EnergySources own their cost models
                          ├─ physics (carried, legs/yr) + helpers ─> assemble LCOT
                          └─ argmin LCOT ─> the point's Result
            └─ write artifact  (Parquet, CSV option)
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

Computed by the **strategy as it assembles cost** — not a standalone module. The
arithmetic is a `physics.py` primitive that draws capacity and deadweight from the
**Platform** and the slot/mass footprint from the **EnergySources**, then takes the
volume-bound vs. mass-bound minimum over asymmetric (head/back-haul) legs. May go
≤ 0 (the store swamps the ship) → infeasible. `legs_per_year` lives the same way: a
`physics.py` primitive the strategy calls, since it's a necessary cost input.

## Configuration layout

Hierarchical YAML, populated into frozen schema dataclasses (`params.py` becomes
the schema — the old flat inventory regrouped by axis):

```yaml
shared:        # route + economics that aren't axis-specific (discount rate, crew cost, margins…)
platforms:     # container (TEU)  [bulk (tonne) when it earns its keep]
drivetrains:   # mechanical-fossil | mechanical-nuclear | electric
sources:       # VLSFO | LFP | iron-air | SMR-* | mobile-tender-reactor | …
cases:         # named: platform + drivetrain + [sources] + strategy + fixed params
               #        + free-param decl (optimized) + swept-param decl (D_max range, …)
```

## Output artifact

A tidy table, one row per (case, `D_max`, and any other swept input): LCOT, optimal
speed, reactor size (where applicable), the energy / capital / O&M breakdown,
energy-store size (battery slots & kWh), and a feasibility flag. **Parquet**
primary, **CSV** optional. Designed for large sweeps — potentially thousands of
rows — and for **incremental generation/update** (append / partitioned writes),
not full regeneration on every run.

## Terminology hygiene

- **Objects vs. functions:** nouns are **frozen dataclasses** (Platform, Drivetrain,
  Shared, Case, Route, Point, Journey, Result); verbs are **plain functions** (the
  strategies, `optimize`, `run`). The *one* exception is **EnergySource**, which carries
  its own cost methods (`size` / `levelize` / …) because that cost model is intrinsically
  polymorphic by source type. No `.evaluate()` / `.execute()` elsewhere.
- The noun is **EnergySource**; its computation is its **energy cost model**. We do
  **not** use the word "supply".
- **Journey** = the resolved dispatch plan the strategy *produces*. **`route`** = the
  Case's fixed route/condition params in config. Never reuse "journey" for the config bundle.
- **Strategy** = a named function `(case, point) -> Result`. **Optimizer** = the function
  `optimize(case, swept_point)` searching free inputs. **`run`** = the outer sweep.
- **physics.py** = ship physics & logistics arithmetic.

## Module map — target vs. current debris

| Target            | Role                                              | Current status |
|-------------------|---------------------------------------------------|----------------|
| `units.py`        | unit conversions                                  | keep as-is |
| `physics.py`      | ship physics & logistics (`legs_per_year`, `carried`) | exists; stale docstring; `legs_per_year` + `carried` still in `_orphans` |
| `helpers.py`      | shared cost/finance math (`crf`, …)               | to create; `crf` currently in `_orphans` |
| `data_classes.py` | config schema: Shared / Platform / Drivetrain / EnergySource / Case / Route | nouns present; `Case` + `Route` to add; source cost models to move onto EnergySource |
| `load_config.py`  | thin YAML → schema loader                         | exists |
| `config.yaml`     | hierarchical input                                | draft; `cases:` deferred |
| `strategies.py`   | the per-case strategy functions `(case, point) -> Result` | renamed from `determine_journey_cost.py`; `tether_charge` drafted, defines interfaces |
| `optimizer.py`    | the `optimize` (free-param search) + `run` (sweep) functions, + the `Point` / `Result` types | to create; `determine_cost.py` holds old archetype fns to delete |
| `run.py`          | entry point → load config → `run(case)` → artifact | fully stale |
| `plots.py`, `style.py` | presentation                                 | deferred until an artifact exists |
| `supply.py`       | —                                                 | to be **dissolved**; contents become EnergySource cost models |
| `_orphans.py`     | holding pen                                       | temporary; `legs_per_year` + `carried` → `physics.py`, `crf` → `helpers.py` |

## Open / deferred decisions

- **Source roles** in multi-source cases: a plain list for now; natural roles
  (buffer / charger / …) may emerge as we write the cases.
- **Strategy ↔ Optimizer boundary:** resolved for now — the strategy owns the whole
  per-point cost (designs the Journey, sizes the stores, computes `carried`/`legs`,
  assembles LCOT) and returns a Result; `optimize` only *searches* the free inputs over
  points. Revisit if a case needs the optimizer to see partial structure.
- **EnergySource cost-model interface:** the exact signature and the shape of the
  data a source returns to the strategy (the `# NEEDS` lines in `strategies.py` are the
  current working spec).
- **Journey representation:** the concrete shape of the resolved segment plan.
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
