# Exploration architecture — Sobol, sweeps, and the evaluation store

Status: **proposal**, revised 2026-07-15. This iteration reorganizes the plan around the
kernel / designs / store / views frame below; the override mechanism, sampler choice,
measured baselines, and vectorization stance carry over from earlier iterations (git
history has the previous structures). Sensitivity *viz* stays deferred to the `plots.py`
rebuild.

## The frame

The model is one pure function — the **kernel**: a fully-specified point in parameter
space, plus a **composition** (platform × drivetrain × sources × strategy — the discrete,
structural coordinates), maps to one output row (`lcot` + its itemization). Everything
around it divides into three separable jobs:

1. **Designs** — decide *which points to evaluate*: a dense grid over two params, a
   Saltelli matrix over forty, a hand-picked nominal. Parameters have no intrinsic roles —
   *constant / swept / optimized / sampled* are per-run assignments a design makes, cheap
   to change between runs.
2. **Execution** — evaluate the kernel at those points (a Python loop today; a process
   pool or numpy broadcast if it ever hurts).
3. **Views** — every question is a query over the evaluated rows: an LCOT-vs-`d_km` trace
   (filter + plot), an optimum (group by everything-but-the-lever, argmin), Sobol indices
   (an estimator over a Saltelli-shaped run), a named scenario ("tender, transpacific,
   pessimistic batteries" — a filter), a feasibility map.

This is a known shape, not an invention. In the exploratory-modeling / Robust Decision
Making literature (Bankes 1993, "Exploratory Modeling for Policy Analysis"; Lempert et
al., RAND) this is the standard loop, and the **XLRM** vocabulary maps one-to-one onto
what we've been calling axes and cases:

| XLRM | here |
|---|---|
| X — exogenous uncertainties | sampled / swept params (the ranges) |
| L — levers (decisions) | optimized params (`op_v_kn`; later `design_v_kn`) |
| R — relationships (model structure) | the composition, incl. strategy — the "integer param" |
| M — measures | the output row |

Two more borrowed names: **scenario discovery** (PRIM / CART: find the input-space box
where the output is interesting) is "cases as names for regions in the result space", and
**factor mapping / Monte-Carlo filtering** (Saltelli) is "sensitivity analysis and
choosing what to plot are the same activity". `ema_workbench` (TU Delft) is an existing
implementation of the whole loop — see "Adopt vs build" below.

Consequences the frame forces:

- **Optimize-by-grid is a view, not a phase.** The optimizer already evaluates its whole
  grid and throws away everything but the argmin; store all rows instead, and the argmin
  becomes a groupby-min query — as does the trace, as do the indices. One store, many
  views. (A smarter-than-grid optimizer is *adaptive* — it chooses points from results, so
  it belongs on the design side, ask/tell style; the kernel's purity is what keeps that
  door open.)
- **Roles move to run-time; they don't disappear.** Not knowing in advance which params
  matter is real, but combinatorics still rules: dense designs must stay low-dimensional
  (grids), global designs must be sparse (Sobol/LHS). The workflow is: blast globally by
  sampling → read the indices → assign x/y roles for the dense local views the indices
  point at. Assignment becomes a per-study line instead of a schema commitment.
- **`Case` currently does three jobs**: composition (structural input — keeps the name),
  nominal per-case values (input — stays), and "this subspace is meaningful" (a view —
  moves out to the analysis side).
- **Inputs invert: ranges first, narrowing second.** The config declares values *with*
  their ranges (a scalar is a degenerate range); studies narrow — select, assign roles,
  fix. Previously the YAML pinned scalars and the CSV re-opened them; that is backwards
  relative to how the space is actually explored.

## Design stance — guards inform, they don't gate

The model's correctness rests on judgment the code can't check (is 0.02 a sane
`detach_frac`?); the code's job is to make what *is* checkable loud and everything else
frictionless. Two kinds of "safety" get two different treatments:

- **Structural validation stays loud and blocking.** A misspelled override path raises
  naming the exact bad field; an unknown config key `TypeError`s in the loader. These are
  typos, never intent — in a hundred-parameter blast a silent one costs a day — and they
  cost one schema line per parameter while shaping nothing architecturally.
- **Intent-level checks report, they never block.** Whether an inert axis, a flat
  parameter, or an infeasible corner of the sampled space is a mistake or the
  uninteresting part of a deliberately wide blast is a judgment call. The machinery
  surfaces these (warnings, feasibility columns, run summaries) and leaves the call to
  the person reading the run.

Target workflow: add a parameter's value and range to `config.yaml` (plus its one-line
schema field), and every subsequent sweep or sensitivity blast picks it up across every
case it resolves in — no per-study, per-case, per-param wiring.

## Measured baseline

Numbers from this machine (Apple-silicon VM, CPython 3.11):

| quantity | measured |
|---|---|
| full `run.py` (8 cases × 36 sweep × 18 speeds ≈ 5.2k kernel evals) | ~0.5 s |
| one kernel eval | 2.6–5.4 µs |
| one inner `optimize()` (18-point speed grid) | 0.05–0.10 ms |
| prototype dotted-path override, 6 paths | 28 µs |

One Sobol sample = one override + one inner optimization ≈ **0.13 ms**:

| design | samples | est. runtime / case |
|---|---|---|
| d=10, N=1024, first-order+total: N(d+2) | 12 288 | ~1.6 s |
| d=10, N=1024, with second-order: N(2d+2) | 22 528 | ~2.9 s |
| d=20, N=4096, with second-order | 172 032 | ~22 s |
| d=100 ("every param"), N=1024, first-order | 104 448 | ~14 s |

Headline: **nothing on the horizon makes the scalar kernel the bottleneck.** Store sizes
are equally comfortable — even a 10⁶-row ensemble with ~30 columns is tens of MB of
Parquet.

## The pieces

### 1. Values + ranges (the input end)

**Decision (revised this iteration): ranges live with values in `config.yaml`.** A leaf
is either a scalar or `{value: 250, range: [80, 300], dist: unif}`; the loader unwraps
`value` for the schema and harvests the ranges into a path-keyed library. A parameter's
plausible range is *data about the parameter* — it belongs next to the value, and it is
exactly what the future tech-data library will tag with sources — while *which params
vary in a given run* is study design and stays in the study file.

Alternatives considered: a separate ranges file keyed by dotted path (the previous
iteration — kept to avoid loader churn, an objection the frame dissolves: the unwrap walk
is ~15 lines, and value+range adjacency is the reading you actually want); ranges on
`Axis` (rejected: conflates grid descriptors with priors); ranges in the Excel mirror
(no).

Wrinkles: per-case route values live in `cases.csv` cells, so their per-case ranges go in
the study file as case-rooted paths (`params.route.standoff_nm`) until case definitions
move to YAML (flagged below). `sync_excel.py` reads `config.yaml`; the scalar-or-dict
leaf shape must round-trip through it — check during implementation. A param with a value
but no range can still be varied: a study may apply a default perturbation (e.g. ±20%)
for screening, so *entering* a param never requires deciding its range.

### 2. Compositions (the structural dimension)

`cases.csv` stays the hand-written composition table: platform × drivetrain × sources ×
strategy + nominal route values + the seed axes behind `run.py`'s default artifact. What
it stops being is the roster of every subspace worth looking at — named scenarios are
filters on the store (later PRIM boxes), not input rows, and a view may span the
composition dimension ("cheapest nuclear option") just as well as slice it.

Flagged, not acted on: at a genuinely large composition count the CSV's multi-row
grouping and wide columns will strain; the escape hatch is YAML case definitions (anchors
give shared defaults + per-case deltas), which would also give per-case route ranges a
natural home.

### 3. The kernel and the override channel

How per-sample / per-point values reach the model. Alternatives considered:

- **(a) Widen the `Point` channel** (read every param via `point.get`) — rejected:
  `point` never reaches the source cost methods (`BatterySource.size` reads
  `self.energy.dod` internally), every read site spells the path twice, and strategy
  readability — the model's main asset — degrades everywhere.
- **(b) Override the parsed YAML, rebuild through the loader** — rejected: needs a
  parse-once/build-many loader split, costs a deepcopy + full rebuild per sample (order
  of the evaluation itself), and route params need a second namespace anyway.
- **(c) Recursive `dataclasses.replace` on the built `Case`** — **chosen**, prototype
  validated: 28 µs for 6 paths, untouched subtrees stay shared, a misspelled path raises
  `AttributeError` naming the exact bad field. No loader or schema change.
- **(d) Mutable copies** — rejected: breaks the frozen invariant, mutation-leak risk
  through shared library objects. **(e) omegaconf / pydantic** — a dependency plus a
  loader rewrite to solve a ~30-line problem.

Paths are rooted at the Case and mirror the schema; inside the `sources` tuple the next
segment selects by source name (`sources.lfp.capex.usd_per_kwh`,
`params.route.standoff_nm`, `drivetrain.operations.crew_count`).

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

**Axes unify onto this channel** (one mechanism, not two): `Axis.param` takes the same
dotted path (bare names alias to `params.route.<name>`, keeping `cases.csv` and columns
as they are); the optimizer builds each grid point's Case with `apply_overrides`; the
`Point` class and the strategies' `point` parameter retire — every
`point.get(name, route.X)` becomes a plain read of the (possibly overridden) config.
Costs, and why they don't bite:

- **Read-tracking dies with `Point`** → replaced by a stronger *effect* check: rows
  identical along an axis (comparing strategy-produced columns, before coordinates are
  merged in) trigger a **warning** naming both readings — unconsumed param vs genuinely
  flat range. A report, not a gate: under blast-everything exploration an axis inert on
  some cases is routine. Reading isn't affecting, so this also catches read-but-inert
  params the old guard passed (an optimize axis on `design_v_kn` for `reactor_direct`
  yields a plausible-looking "optimum" at the grid floor only because nothing varies).
  Axes with `n = 1` or `lo == hi` skip the check.
- **Hot-loop overhead** ~5 µs/path/point vs 4–5 µs per eval: full `run.py` ~0.5 s →
  ~1.5 s. Irrelevant, and it vanishes under array execution (leaf values set once per
  case).
- **A new axis param needs a schema field** — discipline, not loss: every variable param
  is schema-documented, and all four previously point-wired params were already `Route`
  fields, which is what makes the migration mechanical.
- **Churn**: six strategies lose their `point` plumbing (`strategy(case) -> row`).
  Acceptance: `results/lcot.csv` byte-identical before/after.

### 4. Designs (which points to evaluate)

- **Grid** — the existing `Axis` mechanics, unchanged: seed axes in `cases.csv` drive the
  default artifact; studies can declare denser or different grids.
- **Saltelli / Sobol via SALib** — **chosen** over `scipy.stats.qmc.Sobol` + hand-rolled
  estimators (we'd own notoriously subtle estimator math for no gain) and over fully
  hand-rolled (~100 lines of easy-to-get-quietly-wrong). SALib brings the
  sampler/estimator pairing, bootstrap confidence intervals, and distributions
  (`unif`/`triang`/`norm`/`lognorm`); its scipy dependency also unlocks the 1-D
  grid-refinement TODO item. Default `second_order: false` (N(d+2) vs N(2d+2)); if d ever
  grows into the many hundreds, SALib's Morris screening is a cheaper first pass with the
  same driver shape — noted, not planned.
- **Adaptive designs later** (real optimizers, adaptive sampling) plug in ask/tell style
  against the pure kernel; nothing here forecloses them.

Studies select and assign — cases and params default to *everything*, so the
blast-everything study is an empty spec:

```yaml
# studies.yaml — role assignment + narrowing over the ranges declared in config.yaml
studies:
  blast:                        # defaults: every case, every param that has a range
    mode: sobol                 # sobol -> Saltelli + indices | sweep -> per-param 1-D traces
    n: 1024                     # base sample count (power of 2)
  tender-screening:
    mode: sobol
    cases: [tender, fossil]     # one shared sample matrix across member cases
    params: [sources.tender-reactor.*, params.route.detach_frac]   # paths or globs
    fix: {params.route.d_km: 8000}      # constants for this run (override the nominal)
    n: 1024
    second_order: false
```

Semantics:

- **One shared sample matrix per study across member cases** — what makes cross-case
  questions (LCOT gaps, rankings) answerable post-hoc from the store without re-eval.
- **Resolvability:** a path whose source name is absent from a member case is skipped for
  that case (that case genuinely doesn't depend on it — its index is exactly zero by
  construction); any other unresolvable segment is a typo and errors. An explicitly
  listed param resolving in *no* member case errors (you named it); under default-all it
  is simply not selected, noted in the run summary.
- **`mode: sweep` shares everything but the math** — per-param 1-D traces (each param
  stepped across its range, others at nominal): the mass-produced version of a
  hand-written sweep axis. `mode: sobol` adds the Saltelli matrix and indices.
- **Levers stay levers:** each sample re-optimizes the case's `optimize` axes (speed is a
  decision, not an uncertainty); the Sobol Y is the argmin-view LCOT. Grid quantization
  (optima land on speed knots) adds staircase noise — acceptable for screening, scipy 1-D
  refinement is the fix if it shows in the confidence intervals.
- **Infeasible samples are signal, not failure.** Wide ranges *will* cross feasibility
  edges — partly the point. Saltelli's pairing can't drop rows, so the driver always
  reports the infeasible fraction per case; with zero it computes LCOT indices normally,
  otherwise it also computes indices on the *feasibility indicator* (which params push a
  case off the cliff — often the more interesting result) and, for LCOT, substitutes a
  study-declared penalty (`infeasible_lcot:`) or skips that case's LCOT indices with a
  note. Nothing errors; all rows land in the store.

### 5. Execution

The ladder, cheapest lever first — re-scoped by the measured baseline (the old TODO
assumed Sobol would force vectorization; it doesn't):

- **(a) Scalar loop — chosen for now.** Worst realistic study: seconds per case.
- **(b) Process pool over samples** (driver-only, ~10 lines, ×n_cores) — first lever if a
  study crosses ~minutes; samples are embarrassingly parallel.
- **(c) Numpy broadcast** (whole-grid arrays through the kernel, feasibility
  early-returns become masks) — ~100× more, priced in the readability of all six
  strategies. Its real trigger is the voyage-weather Monte Carlo or designs orders of
  magnitude larger. The old TODO plan (kernel inventory, GRID-vs-CONFIG branch rule,
  byte-identical acceptance) stays valid, unscheduled. With axes unified onto overrides
  there is exactly one door for values into the model — config leaves — so array
  execution later needs no second channel: an array set into a field broadcasts through
  the pure-arithmetic kernel, grid dims and sample dims alike.
- **(d) numba / jax** — rejected: heavy deps, and jitting dataclass-orchestration code
  means rewriting it into arrays anyway, i.e. (c) with extra steps.

### 6. The store

One rows artifact per run — the *full* evaluated grid, not just winners: sampled/swept
inputs + the complete kernel row (all itemization columns) + `feasible`. `run.py` keeps
writing `results/lcot.{parquet,csv}`, reframed as the argmin *view* over its stored grid;
studies write under `results/sobol/<study>/`:

- `samples.parquet` — one row per (sample × case); sweep-mode runs write the same shape
  (grid point × case).
- `indices.parquet` + `indices.csv` — long form: `case, param, S1, S1_conf, ST, ST_conf`
  (+ S2 pairs when computed).
- `study.yaml` — snapshot of the study spec that produced the run, so the artifact stays
  self-describing after `studies.yaml` moves on.

### 7. Views (the analysis end)

All queries over the store: argmin tables (the current artifact), LCOT-vs-X traces, Sobol
indices, feasibility maps, and named scenarios (filters; later PRIM boxes when scenario
discovery earns a dependency). Sensitivity analysis and subspace selection are the same
activity here — the indices are hints for which views to look at next. Viz (tornado /
index bars / scatter) joins the `plots.py` rebuild.

## Adopt vs build: `ema_workbench`

The TU Delft EMA Workbench implements this exact loop: parameters declared with ranges
(constants as degenerate cases), categorical parameters that could carry the composition
dimension, parallel ensemble execution, SALib integration for Sobol, PRIM/CART for
scenario discovery, results as tidy arrays. Since the kernel is a pure function of a flat
parameter dict post-unification, wrapping it is an afternoon. Costs: it owns the run loop
(inversion of control), drags matplotlib/seaborn/platypus along, and its API has real
friction. **Plan: a timeboxed spike before building our own driver** — wrap the kernel,
run one Sobol study and one PRIM box, then decide: adopt it, or keep the ~300-line
in-repo core (override + driver + store + queries) and consciously borrow its structure.
Either way the workflow stays portable to other projects — the four pieces are
model-agnostic.

## Implementation order

1. **`scripts/override.py` + axis unification** — optimizer applies axes via
   `apply_overrides` (bare names alias to `params.route.<name>`), strategies drop
   `point`, effect-based flat-axis warning replaces read-tracking; derive `load_config`'s
   `_ROUTE_FIELDS` from the `Route` dataclass fields; correct the stale
   machine-generated-cases comment in `config.yaml`. Acceptance: `results/lcot.csv`
   byte-identical.
2. **Timeboxed `ema_workbench` spike** (needs 1's kernel purity). Outcome decides step 4's
   shape.
3. **Ranges-with-values** — loader unwrap (+ `sync_excel.py` round-trip check) +
   `studies.yaml` seeds; `salib` dependency.
4. **Driver + store + index/trace views** — in-repo `scripts/sobol.py` or the EMA wrapper,
   per the spike.
5. **TODO.md** — collapse the readiness section to what remains (viz; execution-ladder
   pointer here).

Verification beyond the golden diff: a single-param study must return S1 ≈ ST ≈ 1; a
two-param study on params with known relative leverage must rank them; one real study at
N and 2N must agree within the bootstrap confidence intervals.
