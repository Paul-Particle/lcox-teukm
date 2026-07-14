# Sobol sensitivity & vectorization — implementation plan

Status: **proposal** (2026-07-14). Covers the four gaps in TODO's "Sobol sensitivity —
readiness" section — override channel, sampler + ranges spec, analysis layer, Stage-2
vectorization — plus the driver and artifact design around them. Each decision lists the
alternatives considered, so the choices can be challenged point by point. Sensitivity *viz*
(tornado / index bars) stays deferred to the `plots.py` rebuild.

## Measured baseline

Numbers from this machine (Apple-silicon VM, CPython 3.11), because the vectorization
decision should rest on measurement, not the intuition that "thousands of samples" implies
"slow":

| quantity | measured |
|---|---|
| full `run.py` (8 cases × 36 sweep × 18 speeds ≈ 5.2k strategy evals) | ~0.5 s |
| one strategy eval | 2.6–5.4 µs |
| one inner `optimize()` (18-point speed grid) | 0.05–0.10 ms |
| prototype dotted-path override, 6 paths (see Decision 1) | 28 µs |

One Sobol sample = one override + one inner optimization ≈ **0.13 ms**. Saltelli design
sizes that follow from it:

| design | samples | est. runtime / case |
|---|---|---|
| d=10, N=1024, first-order+total: N(d+2) | 12 288 | ~1.6 s |
| d=10, N=1024, with second-order: N(2d+2) | 22 528 | ~2.9 s |
| d=20, N=4096, with second-order | 172 032 | ~22 s |

Headline consequence: **Sobol never makes the scalar engine the bottleneck.** The sampler
and the vectorization question decouple; Stage-2 is re-scoped in Decision 7.

## Decision 1 — how per-sample values reach the model

The blocker from TODO: only `d_km` / `op_v_kn` / `design_v_kn` / `detach_frac` flow through
`point.get`; the high-leverage library params (battery/reactor/tether cost + efficiency
blocks) are read straight off the frozen dataclasses.

Alternatives considered:

- **(a) Widen the `Point` channel** — read *every* parameter as
  `point.get("battery.capex.usd_per_kwh", battery.capex.usd_per_kwh)`. Rejected: `point`
  never reaches the source cost methods (`BatterySource.size` reads `self.energy.dod`
  internally), so it would have to be threaded through every `size`/`levelize`/`life_yr`
  signature; every read site spells the path twice; strategy readability — the model's main
  asset — degrades everywhere to serve a feature used only by the sampler.
- **(b) Override the parsed YAML, rebuild through the loader** — set dotted paths in the
  `config.yaml` dict, rebuild Cases per sample. Workable, and the path namespace is exactly
  the YAML's. Rejected on three counts: `load_config` must be split into parse-once /
  build-many (loader churn); a per-sample deepcopy + full library rebuild costs on the order
  of the evaluation itself; and route params come from `cases.csv`, so a second namespace is
  needed anyway.
- **(c) Recursive `dataclasses.replace` on the built `Case`** — a pure function
  `apply_overrides(case, {path: value}) -> Case` that rebuilds only the frozen spine along
  each path. **Chosen.** Prototype validated: 28 µs for 6 paths; untouched subtrees stay
  shared by reference; a misspelled path raises `AttributeError` naming the exact bad field
  (validation against the schema itself, stronger than a dict-key check); no change to
  loader, schema, strategies, or optimizer.
- **(d) Mutable copies (`deepcopy` + `object.__setattr__`)** — rejected: breaks the frozen
  invariant and risks leaking mutations through the loader's shared-by-reference
  economics/margins/library objects.
- **(e) omegaconf / pydantic** — dotted overrides come built in, but it's a dependency plus
  a loader rewrite to solve a ~30-line problem.

Paths are rooted at the Case and mirror the schema (which mirrors `config.yaml`
one-to-one), with one convention: inside the `sources` tuple the next segment selects by
source *name*:

```
sources.lfp.capex.usd_per_kwh
sources.tender-reactor.tether.cable_efficiency
params.route.standoff_nm
platform.capacity.deadweight_t
drivetrain.operations.crew_count
```

Validated prototype (lands as `scripts/override.py`, plus docstrings):

```python
def apply_overrides(case: schema.Case, overrides: dict[str, float]) -> schema.Case:
    for path, value in overrides.items():
        case = _set(case, path.split("."), value)
    return case

def _set(node, keys: list[str], value):
    """Rebuild the frozen spine along `keys`, setting the leaf to `value`."""
    key, *rest = keys
    child = getattr(node, key)                      # AttributeError names a bad segment
    if isinstance(child, tuple):                    # sources tuple: next segment is a name
        name, *rest = rest
        index = next(i for i, el in enumerate(child) if el.name == name)
        elements = list(child)
        elements[index] = _set(elements[index], rest, value) if rest else value
        return dataclasses.replace(node, **{key: tuple(elements)})
    if not rest:
        return dataclasses.replace(node, **{key: value})
    return dataclasses.replace(node, **{key: _set(child, rest, value)})
```

### Decision 1a — one mechanism or two?

Overrides could in principle retire the `Point` channel entirely (the optimizer would apply
`params.route.op_v_kn` per grid point; strategies would read `route.op_v_kn` directly).
Tempting — one mechanism — but rejected:

- The **axis-consumed guard would be lost**. Overrides validate "path exists in the
  schema", not "this strategy actually reads it" — `reactor_direct` ignores
  `design_v_kn`, and today the read-tracking guard catches an axis that would silently vary
  nothing. Path validation cannot.
- The layering is principled, not accidental: `Point` carries *search/decision coordinates*
  explored within one case evaluation (hot loop, few params); overrides produce a *new
  scenario Case* once per sample. A sample's route override composes cleanly with the point
  channel — `point.get("d_km", route.d_km)` falls back to the overridden route default when
  no axis sets it.

Known residual limit (either design has it): a *valid but unread* override — say
`params.route.detach_frac` on the fossil case — is silently flat and comes back as an
exactly-zero index. That zero is in fact the correct answer (fossil LCOT is independent of
detach weather); it only misleads if the study *intended* the param to matter. Study specs
are short and reviewed; if this ever bites, a cheap probe pass (perturb each param once,
warn if the output is bit-identical) can be added to the driver.

## Decision 2 — where samples live

- **(a) Machine-generate `cases.csv`** (the original comment in `config.yaml`). A column
  per parameter is already rejected in TODO; a JSON-overrides column keeps the schema but
  round-trips thousands of rows through CSV, where Saltelli's strict row ordering — which
  the analyzer depends on — is one accidental re-sort away from silent corruption, and the
  hand-written seed table drowns.
- **(b) A separate driver, `scripts/sobol.py`** — **chosen**. Samples exist only as arrays
  in the driver and as an output artifact; `cases.csv` stays the human-written seed table.
  The stale "cases.csv will be machine-generated (Sobol…)" comment in `config.yaml` gets
  corrected as part of this work.

## Decision 3 — parameter-space spec

- **(a) Extend `Axis` with distribution fields** — rejected; TODO already flags this:
  `Axis` is a grid descriptor for search/sweep, and conflating it with sampling priors
  muddies both.
- **(b) Priors inline in `config.yaml`** (`usd_per_kwh: {value: 250, dist: triang, …}`) —
  the eventual endgame once the tech-data library (TODO "Data & itemization") tags every
  value with source + uncertainty, but today it churns the loader and conflates *data*
  (priors) with *study design* (which params a given study varies).
- **(c) A separate `uncertainty.yaml`** — **chosen**. Studies are named, list member cases
  and parameters, and map 1:1 onto SALib's problem dict. When the tech-data library
  arrives, studies can reference its priors instead of carrying bounds.

```yaml
# uncertainty.yaml — Sobol study definitions (paths per override.py; dists per SALib)
studies:
  tender-screening:
    cases: [tender, fossil]          # one shared sample matrix across member cases
    n: 1024                          # base sample count (power of 2)
    second_order: false
    params:
      - {path: sources.tender-reactor.capex.usd_per_kw,          dist: unif,   bounds: [3500, 12000]}
      - {path: sources.tender-reactor.parasitic_kw,              dist: unif,   bounds: [1000, 5000]}
      - {path: sources.tender-reactor.tether.cable_efficiency,   dist: unif,   bounds: [0.90, 0.98]}
      - {path: params.route.standoff_nm,                         dist: unif,   bounds: [100, 400]}
      - {path: params.route.detach_frac,                         dist: unif,   bounds: [0.0, 0.15]}
      - {path: sources.lfp.capex.usd_per_kwh,                    dist: unif,   bounds: [80, 300]}
```

## Decision 4 — sampler + analyzer

- **(a) SALib** — **chosen**. The Saltelli/Sobol′ sampling and the index estimators with
  bootstrap confidence intervals come as a validated pair (getting the estimators right by
  hand is notoriously subtle); distributions (`unif`/`triang`/`norm`/`lognorm`) supported in
  the problem dict. One new dependency (`salib`), pure Python over numpy/scipy — and scipy's
  arrival is a bonus: the "swap in a real 1-D minimizer" TODO item gets its solver for free.
- **(b) `scipy.stats.qmc.Sobol` + hand-rolled Saltelli/Jansen estimators** — scipy would be
  a new dependency too, and we'd own the estimator subtleties for no gain.
- **(c) Fully hand-rolled** — rejected outright; ~100 lines of easy-to-get-quietly-wrong.

Default `second_order: false` (cost N(d+2) instead of N(2d+2)); first-order + total indices
are the standard screening pair, and S2 is a flag away when interactions matter.

## Decision 5 — study semantics (the driver's contract)

- **One shared sample matrix per study, applied to every member case.** This is what makes
  cross-case questions ("does tender beat fossil under joint uncertainty?") answerable
  post-hoc from the artifact — any derived Y (LCOT gap, rank indicator) can be analyzed
  against the same matrix without re-evaluating.
- **Resolvability rule:** a path whose *source name* is absent from a member case's
  `sources` tuple is skipped for that case (correct semantics — that case genuinely doesn't
  depend on the param; e.g. `sources.lfp.*` on `fossil`). Any other unresolvable segment is
  an error (it's a typo — schema fields exist on every instance). Sanity floor: every path
  must resolve in at least one member case.
- **Y = min-LCOT from the case's own `optimize` axes.** Operating speed is a decision
  variable, not an uncertainty — each sample re-optimizes it. The known staircase from the
  18-point grid (optimal speeds land on knots) adds a little quantization noise to the
  indices; acceptable for screening, and the existing grid-refinement TODO item (now with
  scipy available) is the fix if it ever shows in the confidence intervals.
- **`sweep` axes are ignored by the driver.** A study evaluates at the route's (possibly
  overridden) nominal `d_km`; distance enters either as a fixed override or as a sampled
  param — not as a swept trace.
- **Infeasible samples fail the study.** Saltelli's paired design cannot tolerate dropped
  or `inf` rows, so the driver counts `lcot = inf` rows, archives them
  (`infeasible.parquet`) for inspection, and errors with the count — the remedy is
  tightening bounds, which is study design, not code. (The alternative — substituting a
  large penalty LCOT — deliberately distorts the indices toward the feasibility boundary;
  documented here so it can be chosen consciously later if a study *wants* feasibility
  sensitivity.)

Driver shape: `uv run scripts/sobol.py [study ...]` → for each study, build the SALib
problem, sample once, then per member case loop samples → `apply_overrides` →
`optimizer.optimize(case_i, {})` → assemble the per-sample frame → `sobol.analyze` per
case → write artifacts. ~120 lines, no changes to the eval engine.

## Decision 6 — artifacts

Everything under `results/sobol/<study>/`, never touching `results/lcot.*`:

- `samples.parquet` — one row per (sample × case): the sampled inputs plus the *full*
  strategy row (all the extra columns), not just LCOT. Costs nothing and keeps every
  downstream reuse open — input/output scatters, derived-Y analyses, regression-based
  importance — without re-running.
- `indices.parquet` + `indices.csv` — long form: `case, param, S1, S1_conf, ST, ST_conf`
  (+ S2 pairs when computed). CSV for eyeballing, matching the `lcot.*` convention.
- `study.yaml` — a snapshot of the study spec that produced the run, so an artifact is
  self-describing after `uncertainty.yaml` moves on.

## Decision 7 — vectorization, re-scoped by measurement

TODO Stage-2 assumed the Sobol generator makes the grid "big enough to feel". The measured
numbers say otherwise: the worst realistic screening study runs in seconds per case. The
honest ladder, cheapest lever first:

- **(a) Do nothing** — **chosen for now.** Nothing on the near horizon crosses a minute.
- **(b) Process pool over samples** (`ProcessPoolExecutor`, chunked, in the driver only) —
  the designated first lever if a study ever crosses ~minutes: ~10 lines, ×n_cores, zero
  model-code impact. Samples are embarrassingly parallel.
- **(c) Stage-2 numpy broadcast** (TODO's plan: `Point` carries whole-grid arrays,
  feasibility early-returns become masks, `argmin` picks winners) — ~100× on top, but
  priced in the readability of all six strategies (masks, `np.where`, `np.errstate` over
  masked garbage). Its real trigger is not Sobol but the voyage-weather Monte Carlo
  (hour-by-hour SoC over hundreds of journeys × routes) or designs orders of magnitude
  larger. The TODO plan itself is sound — kernel inventory, GRID-vs-CONFIG branch rule,
  byte-identical diff acceptance — keep it verbatim, unscheduled.
- **(d) numba / jax** — rejected: heavy deps, and jitting dataclass-orchestration code
  means rewriting it into arrays anyway, i.e. (c) with extra steps.

Nothing in this plan is thrown away by (c) later: `apply_overrides` is dimension-agnostic —
a numpy array set into a field broadcasts through the pure-arithmetic kernel exactly like
the Point-carried arrays, so Stage-2 could vectorize across samples as well as grid points.

## Implementation order

1. **`scripts/override.py`** (+ correct the stale machine-generated-cases comment in
   `config.yaml`). Pure addition; `run.py` output stays byte-identical — verified by
   diffing `results/lcot.csv` before/after.
2. **`uncertainty.yaml`** seed study + `salib` dependency.
3. **`scripts/sobol.py`** driver + artifacts.
4. **TODO.md** — collapse the readiness section to what remains (viz; Stage-2 pointer here).

Verification beyond the golden diff: a single-param study must return S1 ≈ ST ≈ 1; a
two-param study on params with known relative leverage must rank them; and one real study
run at N and 2N must agree within the bootstrap confidence intervals.
