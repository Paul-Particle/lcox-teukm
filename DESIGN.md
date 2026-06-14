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
  EnergySources** + a named **Strategy** + operating/design parameters + flags for
  which inputs are optimizable. The unit we evaluate. A Case can be multi-source:
  the nuclear-tender case is *also* a battery case (onboard buffer + at-sea
  charger), so the architecture supports N sources from the start.

### Behavior

- **Strategy** — named and deliberately bespoke per case-type (e.g. `fuel-burn`,
  `battery-swap`, `reactor-direct`, `reactor-electric`, `tether-charge`). Defines
  the **journey structure** and how the bundled sources are orchestrated (which
  source serves what). A source often implicitly *suggests* a strategy but does not
  lock the Case into one.
- **Journey** — the **resolved dispatch plan** for one hop: route segments (e.g.
  coastal/untethered vs. tethered open-ocean), storm exposure, speed policy.
  Produced by the Strategy. **Not** a configuration axis — there is no `Scenario`
  config section; route-reshaping is the Case's job, expressed as a Journey.
- **Optimizer** — evaluates a Case at a given `D_max`: sizing and/or dispatch
  driven by cost. It explores the flagged free inputs, asks the Strategy to build
  candidate Journeys, hands each EnergySource the Journey/parameters, collects the
  cost data they return, computes `carried` and `legs_per_year`, assembles LCOT,
  and returns the cost-optimal Result.

### Primitives

- **physics.py** — pure ship physics & logistics arithmetic: propulsion cube law,
  per-leg energy, `legs_per_year`, and the `carried` cargo accounting. Stateless;
  called by the Optimizer during evaluation. (Renamed from `energy.py` to stop
  colliding with the EnergySource axis wording.)
- **helpers** (`helpers.py`) — shared cost/finance math (`crf` and friends) needed
  by *both* the Optimizer's cost determination and the EnergySources' cost models;
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
run.py
  └─ load config (YAML ─> frozen dataclasses)
       └─ build Cases (Platform × Drivetrain × [EnergySource…] × Strategy)
            └─ for each Case × D_max in the grid:        (always sweep D_max; the grid is
                 Optimizer.evaluate(case, d_max)          structured to allow extra swept axes
                   ├─ pick trial inputs (flagged)         later — eases the planned Sobol work,
                   ├─ Strategy ─> Journey(s)              not a near-term priority)
                   ├─ EnergySources.cost(journey, …) ─> cost data
                   ├─ physics (carried, legs/yr) + helpers ─> assemble LCOT
                   └─ argmin LCOT ─> Result row
            └─ write artifact  (Parquet, CSV option)
```

## Optimization

- **Objective:** minimize LCOT.
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

Computed by the **Optimizer during cost evaluation** — not a standalone module. The
arithmetic is a `physics.py` primitive that draws capacity and deadweight from the
**Platform** and the slot/mass footprint from the **EnergySources**, then takes the
volume-bound vs. mass-bound minimum over asymmetric (head/back-haul) legs. May go
≤ 0 (the store swamps the ship) → infeasible. `legs_per_year` lives the same way: a
`physics.py` primitive the Optimizer calls, since it's a necessary cost-evaluation
input.

## Configuration layout

Hierarchical YAML, populated into frozen schema dataclasses (`params.py` becomes
the schema — the old flat inventory regrouped by axis):

```yaml
shared:        # route + economics that aren't axis-specific (discount rate, crew cost, margins…)
platforms:     # container (TEU)  [bulk (tonne) when it earns its keep]
drivetrains:   # mechanical-fossil | mechanical-nuclear | electric
sources:       # VLSFO | LFP | iron-air | SMR-* | mobile-tender-reactor | …
cases:         # named: a platform + drivetrain + [sources] + strategy + params + opt flags
```

## Output artifact

A tidy table, one row per (case, `D_max`, and any other swept input): LCOT, optimal
speed, reactor size (where applicable), the energy / capital / O&M breakdown,
energy-store size (battery slots & kWh), and a feasibility flag. **Parquet**
primary, **CSV** optional. Designed for large sweeps — potentially thousands of
rows — and for **incremental generation/update** (append / partitioned writes),
not full regeneration on every run.

## Terminology hygiene

- The noun is **EnergySource**; its computation is its **energy cost model**. We do
  **not** use the word "supply".
- **physics.py** = ship physics & logistics arithmetic. **Journey** = resolved
  dispatch plan.
- **Strategy** = the named, case-specific behavior. **Optimizer** = the cost-driven
  search over inputs.

## Module map — target vs. current debris

| Target            | Role                                              | Current status |
|-------------------|---------------------------------------------------|----------------|
| `units.py`        | unit conversions                                  | keep as-is |
| `physics.py`      | ship physics & logistics (`legs_per_year`, `carried`) | exists; stale docstring; `legs_per_year` + `carried` still in `_orphans` |
| `helpers.py`      | shared cost/finance math (`crf`, …)               | to create; `crf` currently in `_orphans` |
| `params.py`       | per-axis frozen schema dataclasses                | still the flat `Params`; to rewrite |
| (config) `*.yaml` | hierarchical input                                | deleted; to author |
| `data_classes.py` | Platform / Drivetrain / EnergySource / Case       | axes present; `Case` still a passive bag; source cost models to move onto EnergySource |
| `determine_cost.py` | the Optimizer + evaluation                      | holds old archetype fns calling deleted helpers; to redesign |
| `run.py`          | entry point → config → optimize → artifact        | fully stale |
| `plots.py`, `style.py` | presentation                                 | deferred until an artifact exists |
| `supply.py`       | —                                                 | to be **dissolved**; contents become EnergySource cost models |
| `_orphans.py`     | holding pen                                       | temporary; `legs_per_year` + `carried` → `physics.py`, `crf` → `helpers.py` |

## Open / deferred decisions

- **Source roles** in multi-source cases: a plain list for now; natural roles
  (buffer / charger / …) may emerge as we write the cases.
- **Strategy ↔ Optimizer boundary:** leaning Optimizer-owned for dispatch (Strategy
  supplies the structure); sharpen when we write the first multi-source (tender) case.
- **EnergySource cost-model interface:** the exact signature and the shape of the
  data a source returns to the Optimizer.
- **Journey representation:** the concrete shape of the resolved segment plan.
- **Extra swept axes** beyond `D_max` (to ease later Sobol exploration): structure
  for it, but low priority.
