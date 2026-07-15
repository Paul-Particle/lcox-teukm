# Exploration architecture — vectorize-first

Status: **proposal**, revised 2026-07-15 (v5). v4 promoted the numpy-safe kernel from
"deferred optimization" to the architectural basis. v5 folds in the taxonomy that fell out
of working the shapes through: **three parameter roles** (fixed / sampled / search) with
search parameterized by an `optimization:` *method*, a single **objective = a chosen
measure reduced over chosen axes** formulation that covers both optimization and the
Sobol target, and **xarray** as the in-memory block (with netCDF as its authoritative
store). The dotted-path override mechanism, the `Point` channel, and the optimizer's loops
from earlier iterations are superseded and never get built (git history holds those
designs and their rationale). Sensitivity *viz* stays deferred to the `plots.py` rebuild.

## The pipeline

The model is one pure function — the **kernel**: a fully-specified point in parameter
space, plus a **composition** (platform × drivetrain × sources × strategy — the discrete,
structural coordinates), maps to one output row of **measures** (`lcot` + itemization).
Around it:

```
values + ranges  ->  design  ->  execution  ->  store  ->  views
  (config)        (roles ->      (kernel on    (xarray  (queries: reductions,
                   labeled axes)  blocks)       + parquet) traces, indices, scenarios)
```

- **Design** decides *which points to evaluate* and expresses the answer as array-valued
  config leaves on **named axes**: sampled params get jointly-drawn columns sharing one
  axis, search params (swept or optimized) get their own factorial axes, everything else
  stays scalar.
- **Execution** is one kernel call per composition; broadcasting does what the sweep and
  grid-search loops used to do. Optimized axes are then reduced away by the objective;
  swept axes are retained.
- **Views** answer every question as a query over the stored block: an optimum is an
  argmin over the lever axes, a trace is a select, Sobol indices are an estimator over a
  Saltelli-shaped run, a named scenario ("tender, transpacific, pessimistic batteries") is
  a select — and may span the composition dimension ("cheapest nuclear option") as well as
  slice it.

The frame maps one-to-one onto the exploratory-modeling / Robust Decision Making
literature (Bankes 1993; Lempert et al., RAND) — **XLRM**: X, exogenous uncertainties =
sampled ranges *and* retained swept conditions; L, levers = optimized params (`op_v_kn`);
R, relationships = the composition (the "integer param"); M, measures = the output row.
"Cases as names for regions in the result space" is *scenario discovery* (PRIM/CART);
"sensitivity ≈ choosing what to plot" is Saltelli's *factor mapping*.

## Three roles, and the objective

Parameters have no intrinsic roles — role is a per-run assignment, one line in a study.
There are three, and the shapes they produce are the whole architecture:

| role | axis | cost | reduction |
|---|---|---|---|
| **fixed** | none (0-d scalar) | — | — |
| **sampled** | shares axis 0 (Saltelli joint draw) | **additive** — one param grows axis 0 by one `N`-slab | variance decomposition along axis 0 |
| **search** | its own factorial axis | **multiplicative** — each axis multiplies the block | per its `optimization:` method (below) |

The additive/multiplicative split is the cost model: sampled params are cheap to pile on
(that's the headroom that makes "sample everything, then screen" the default opening
move); each search axis costs a factor of its grid length, so budget swept and optimized
axes the same way.

**Swept and optimized are the same kind of axis** — both are factorial grid dimensions,
built identically. The only difference is one line of post-processing, expressed as the
axis's optimization *method*:

- `optimization: none` — **swept**: the axis is *retained*. It's a condition you want the
  answer *as a function of* (route length, fuel price scenario), never one you collapse.
  Collapsing a condition would answer the wrong question ("nuclear is cheapest *at*
  12 000 km" is not a decision you get to make).
- `optimization: exhaustive_search` — **optimized (lever)**: argmin over the grid. Because
  we have the headroom, the grid *is* the landscape and the argmin *is* the optimum, from
  one materialization — "mark the optimum on the curve" is just starring the argmin index.

That leaves a clean seam for a future `newton`/`scipy` solver method (precise optimum, no
grid) — deferred as YAGNI while `exhaustive_search` covers every current need. The seam
holds because "materialize a grid on this axis" and "run a solver on this axis" are
independent operations; a solver-plus-coarse-landscape combo is buildable later without a
schema change, and we don't build it now.

**The method decides *where* the lever axis collapses — and that is the whole reason
`optimization:` is an axis property, not an `analyze` property.** Exhaustive search is the
special case where the collapse is a tidy *post-kernel reduction*: execution materializes
the full grid, and the argmin runs afterward. That only works because we can afford to
evaluate every grid point. An adaptive optimizer is the opposite — it *owns the kernel
calls*, evaluating a few chosen points and using each result to pick the next, so it
collapses the axis *during* execution and the grid is never materialized. "Optimize only
after the kernel" would therefore foreclose adaptive search entirely; making the method an
axis property is exactly what keeps it open.

| method | who collapses the lever axis | when | grid materialized? |
|---|---|---|---|
| `none` (sweep) | nobody — retained | — | yes (kept) |
| `exhaustive_search` | `analyze`, as a post-kernel argmin | after full eval | yes (kept for landscape) |
| solver (`newton`/`bopt`, later) | an optimizer loop wrapping the kernel | during execution | no — only visited points |

The invariant that keeps everything downstream generic: **whichever method runs, the
output is the same contract** — a block with the lever dims collapsed, every measure
carried at the optimum. Sobol and the views consume that shape and never learn how it was
produced. So only the collapse step itself is method-specific; it is isolated behind the
method dispatch at the execution boundary. Two consequences for building exhaustive now:
the numpy-safe kernel must stay callable at *arbitrary* lever values (not just grid nodes
— it already is, since it broadcasts over whatever the leaves carry), and `analyze` must
not assume the grid is always materialized. A vectorized adaptive optimizer, when it comes,
loops iterations in Python and vectorizes each across the *sample* axis (every sample steps
at once, converged ones masked) — same kernel, block speedup intact.

**The objective is a chosen measure reduced over chosen axes.** Nothing about `lcot` is
privileged; it's the default measure, not a hardcoded objective. The block produces a row
of named measures per cell (`lcot`, `cargo`, `capex`, …, plus *derived* ones like a
cross-strategy margin `lcot_nuclear − lcot_fuel`). Then:

- **Optimization** = `argopt` (argmin/argmax) of the objective measure over the lever
  axes, **carrying every other measure along at that arg** (so the winning speed, the
  cargo at the optimum, etc. come for free — `take_along_axis` / xarray `isel`).
- **Sensitivity** = variance-decompose a chosen measure along the sample axis. It need not
  be the same measure as the optimization objective: feed `lcot` to ask "what drives
  cost," or feed the cross-strategy margin to ask "what drives *whether* nuclear wins" (a
  crossover-driver study — the "M = measures" slot in XLRM).

So the whole thing is a small algebra: **measures × axes × reductions**. A crossover
*point* is a view (compare two compositions' `lcot` surfaces over a swept axis); "what
drives the crossover" is a Sobol study with a cross-strategy measure as its target.

### Swept axes make a *family* of Sobol analyses

A retained swept axis of length `K` multiplies the block and multiplies the number of
Sobol analyses — one per condition slice:

```
block:            (M samples, K swept, L levers)
argmin levers  -> (M samples, K swept)
Sobol along axis 0, once per swept slice -> indices of shape (K, d)
```

That is the answer to "how do the sensitivities shift as route length grows" — sweeping
*and* sampling in one run is the intended, powerful combination. What doesn't make sense is
collapsing a condition (hence `optimization: none`), and what's redundant is sampling a
param you also sweep — one role per param.

Two structural consequences carry over:

- **Optimize is a reduction, not a phase.** The old optimizer evaluated a grid and kept
  the argmin; the block keeps everything and the argmin is a query. A smarter-than-grid
  optimizer is *adaptive* (chooses points from results), so it returns later as a
  design-side loop over the same kernel — the `optimization:` method seam is exactly where
  it plugs in.
- **Roles move to run-time; they don't disappear.** Dense designs must stay
  low-dimensional (search axes multiply); global designs must be sparse (one shared sample
  axis, additive). Blast globally by sampling → read indices → assign search roles for the
  local views the indices point at.

## Modules and boundaries

The load-bearing idea for reuse: a **producer** emits a **block** (the labeled bundle of
measures — an xarray `Dataset` in memory, netCDF + flat parquet on disk), and *everything
downstream of the block is producer-agnostic*. Running the strategies is one producer;
"any other set of functions" is another, under the same contract. Strategy knowledge lives
only in the kernel; question knowledge lives only in a small derived-measures file;
everything between and after is generic over the block.

The block is **structurally generic but semantically specific** — the measures it carries
(`lcot`, `cargo`, `tender_reactor_kw`) are what we ran, but `analyze` never names them in
code; it names them *from the study* (`objective: lcot`, `optimize: [op_v_kn]`). The dims
are named by `design` according to roles, and `analyze` reads those names back. That
name-as-config coupling — design names axes by role, analyze trusts the names — is the one
thing the two layers must agree on; xarray's contribution is only making "reduce over the
axis called `op_v_kn`" first-class, not the genericity itself.

| stage | module | strategy-aware? |
|---|---|---|
| parse `config.yaml` → schema + harvest ranges | `load_config.py` (exists) | no |
| parse `studies.yaml`, resolve roles against ranges | `studies.py` (new) | no |
| **design** — roles → named axes, Saltelli matrix + factorial grids, place into config, build Cases | `design.py` (new, ~100 lines) | no |
| **kernel** — the strategies (the producer) | `strategies/` (exists) | **yes — the only place** |
| **execution** — loop compositions, call kernel, assemble the block; dispatch the lever collapse on `optimization:` method | `execute.py` (new / fold into `run.py`) | no |
| derived / cross-strategy measures (`lcot_fuel − lcot_nuclear`) | `measures.py` (new, small) | **question-specific, isolated here** |
| **analysis** — objective argmin (carry-by-index) for exhaustive, Sobol per slice, feasibility summary → persisted artifacts | `analyze.py` (new) | no |
| **store** — write/read netCDF + parquet + study.yaml | `store.py` (new / fold) | no |
| **views** — plots/tables over the store, on demand | `plots.py` (rebuild) | no |

Two consumer roles, split only by persistence: **analyses** (block → derived artifact on
disk — the optimized view, `indices.parquet`, feasibility report; canonical, run once per
study) and **views** (block + artifacts → plots on demand; disposable). They are the same
kind of operation — reductions/selects over the block — but the persistence boundary is
real, so `analyze.py` and `plots.py` stay separate and Sobol logic never leaks into the
plotting layer.

## Design stance — guards inform, they don't gate

The model's correctness rests on judgment the code can't check (is 0.02 a sane
`detach_frac`?); the code's job is to make what *is* checkable loud and everything else
frictionless:

- **Structural validation stays loud and blocking.** An unknown config key `TypeError`s
  in the loader; ranges are declared *on* the value (no path to misspell); varying a
  param whose value feeds a Python-level branch fails immediately with an ambiguous-truth
  error naming the line. Typos and structural impossibilities, never intent.
- **Intent-level checks report, they never block.** A flat parameter (no effect along its
  axis — a nan-aware variance check on the block), an infeasible region, an unresolvable
  param under default-all selection: surfaced in run summaries and columns, judged by the
  person reading the run.

Target workflow: add a parameter's value and range to `config.yaml` (plus its one-line
schema field), and every subsequent sweep or sensitivity blast picks it up across every
case it resolves in — no per-study, per-case, per-param wiring.

## Measured baseline

From this machine (Apple-silicon VM, CPython 3.11, numpy 2.x). Scalar numbers first:

| quantity | measured |
|---|---|
| full `run.py` (8 cases × 36 sweep × 18 speeds ≈ 5.2k evals), scalar loops | ~0.5 s |
| one scalar kernel eval | 2.6–5.4 µs |

Vectorized prototype (`port_swap_battery` rewritten numpy-safe, evaluated as one
1000-samples × 18-speeds block against the scalar strategy looped over the same grid):

| quantity | measured |
|---|---|
| agreement on feasible cells | max abs diff 1.1e-16 (one ulp) |
| infeasibility masks / argmin-over-speed | identical |
| block eval | 0.30 ms for 18k points — **17 ns/point**, ~240× the scalar loop |

Consequently every realistic design is sub-second per case: a d=10, N=1024 Saltelli
first-order design (12 288 samples × 18 speeds ≈ 221k cells) is ~4 ms of kernel time; a
d=100 "every param" blast (104k samples × 18 ≈ 1.9M cells) is tens of ms and ~15 MB per
live array — chunk the sample dimension if intermediate memory (~30 live arrays) ever
matters. Performance is simply off the table; vectorization is justified here by what it
*deletes*, not by speed.

## The pieces

### 1. Values + ranges (the input end)

**Ranges live with values in `config.yaml`**: a leaf is either a scalar or
`{value: 250, range: [80, 300], dist: unif}`; the loader unwraps `value` for the nominal
schema and harvests ranges into a path-keyed library. A parameter's plausible range is
*data about the parameter* (and what the future tech-data library will tag with sources);
*which params vary in a run* is study design and lives in the study file. Declaring the
range on the value also means there is no separate path spec to misspell.

Alternatives recorded: separate ranges file keyed by dotted path (previous iteration —
survives only as the studies file); ranges on `Axis` (conflates grid descriptors with
priors); ranges in the Excel mirror (no). Wrinkles: per-case route values live in
`cases.csv` cells, so their per-case ranges go in the study file as case-rooted paths
until case definitions move to YAML (flagged below); `sync_excel.py` must round-trip the
scalar-or-dict leaf shape — check during implementation. A param with a value but no
range can still be varied: studies may apply a default perturbation (e.g. ±20%) for
screening.

### 2. Compositions (the structural dimension)

`cases.csv` stays the hand-written composition table: platform × drivetrain × sources ×
strategy + nominal route values + the seed axes behind `run.py`'s default artifact.
Compositions are the one loop that remains — they differ structurally (source types, the
`next(...)` source selection), which arrays cannot span. What `cases.csv` stops being is
the roster of every subspace worth looking at: named scenarios are filters on the store
(later PRIM boxes), not input rows. Flagged, not acted on: at a genuinely large
composition count the CSV's multi-row grouping will strain; the escape hatch is YAML case
definitions (anchors give shared defaults + deltas) — which would also give per-case
route ranges a natural home.

### 3. The kernel, numpy-safe (the core decision)

**One codepath, dimension-agnostic**: strategies do pure arithmetic that broadcasts over
whatever shape the config leaves carry — scalars (today's behavior, and the debugging
path: one point is a 0-d evaluation) or blocks. The prototype shows the rewrite is tamer
than earlier iterations priced it; per the old Stage-2 inventory:

- `max`/`min` → `np.maximum`/`np.minimum` (`carried`, `BatterySource.size`, `crf`),
  `math.ceil` → `np.ceil` (`ContainerizedReactor.size`) — cosmetic.
- Feasibility early-returns (`if cargo <= 0: return _infeasible(...)`; `tether_charge`'s
  `tethered_km <= 0` / speed-cap / `detach_frac >= 1` guards) become end-of-function
  masks: compute under `np.errstate(all="ignore")`, then `lcot = inf` where the mask
  bites and strategy-specific fields `NaN`-ed there — matching today's short infeasible
  row after the column union. This is where the bugs will live; the acceptance diff
  (below) is the net.
- **Branches on config scalars stay Python** (`min_discharge_h > 0`,
  `fuel is not None`, the fuel-price quote forms); only branches on varied quantities
  become `np.where`. Corollary, stated as the rule it is: a param read inside a Python
  branch cannot be varied until that branch is numpy-ified — attempting it fails loudly
  at the branch (ambiguous truth value), which is the correct, structural failure.
  Structural facts (source presence, quote form, `None`-ness) can never be varied by
  arrays; varying *structure* means more compositions.

**Superseded alternatives** (earlier iterations, recorded because the reversal is
instructive): (a) scalar loop + widening the `Point` resolver — rejected then and now
(never reaches source methods, path spelled twice per read); (b) scalar loop + per-sample
dotted-path `dataclasses.replace` overrides — a validated 28 µs/sample prototype, chosen
when evaluation was one-point-at-a-time. Vectorization removes its premise: values are
placed **once per run, not once per sample**, so the placement happens in the *parsed
config dict* before the mechanical loader builds (the loader already constructs
`Block(**yaml_subdict)` — a leaf that is an array simply lands in the field), and the
per-sample-cost argument for replace-over-rebuild evaporates along with the mechanism.
`Point`, read-tracking, and `optimizer.py`'s loops retire with it; the flat-axis check
becomes a variance query on the block.

### 4. Design (which points, as arrays)

The array builder is the one new module (~100 lines): resolve a study's role assignments
against the harvested ranges, allocate named axes, reshape, place into the config dict,
build Cases through the unchanged loader.

- **Axis allocation** — the picture is *not* one axis per parameter (factorial
  explosion): **sampled params share one axis** (SALib's Saltelli output is a matrix of
  jointly-drawn rows, `N(d+2) × d` — column *i*, reshaped to axis 0, becomes param *i*'s
  leaf), while **search params get their own factorial axes** (a lever's 18 speeds are one
  axis; a swept condition's `K` values are another). A block is (samples × search grid),
  dense only where low-dimensional.
- **Why `N(d+2)`** — Saltelli isn't one random cloud; it's two independent base matrices
  **A**, **B** (`N×d` each) plus one hybrid `A_B^(i)` per parameter (**A** with column *i*
  from **B**), `d` of them, so the estimator can isolate each param's conditional-variance
  contribution by reusing the same base draws. `N(d+2)` for first + total order;
  `N(2d+2)` when second-order indices are on (adds the mirror hybrids `B_A^(i)`).
- **SALib wiring** (chosen earlier over scipy-QMC + hand-rolled estimators and over fully
  hand-rolled — the estimator subtleties and bootstrap CIs are exactly what we shouldn't
  own): build the problem dict from the selected ranges, `sample.sobol` (N a power of 2,
  `calc_second_order` per study), evaluate, reduce the lever axes by the objective → Y in
  row order → `analyze.sobol`, once per swept slice. One-shot; no feedback loop within a
  study (the "loop" is the human reframing roles between studies). Morris screening is a
  cheaper first pass if d ever reaches the many hundreds — noted, not planned.
- **Studies** assign roles and narrow; cases and params default to *everything*, so the
  blast-everything study is nearly empty. Roles are `sample` (default for anything with a
  range under a sensitivity study), `optimize` (a lever — `optimization: exhaustive_search`
  today), `sweep` (a retained condition — `optimization: none`), and `fix` (a constant for
  this run):

```yaml
# studies.yaml — role assignment + narrowing over the ranges declared in config.yaml
studies:
  blast:                        # defaults: every case, every ranged param -> sample
    n: 1024
  tender-screening:
    cases: [tender, fossil]     # one shared sample matrix across member cases
    sample:  [sources.tender-reactor.*, params.route.detach_frac]  # paths or globs
    optimize: [params.route.op_v_kn]        # lever: argmin, collapsed
    sweep:   [params.route.d_km]            # condition: retained -> per-slice Sobol
    fix:     {params.route.standoff_nm: 12} # constant for this run
    objective: lcot             # measure to optimize + decompose (default: lcot)
    n: 1024
    second_order: false
```

Semantics: one shared sample matrix per study across member cases (cross-case Ys — gaps,
rankings, crossover-driver measures — stay answerable post-hoc from the store); a path
whose source name is absent from a member case is skipped for that case (its index is
exactly zero by construction — correct), any other unresolvable segment errors as a typo;
an explicitly listed param resolving in *no* member case errors, under default-all it is
simply not selected (noted in the run summary). A pure `sweep` study (no `sample`) is the
mass-produced version of a hand-written sweep axis: per-condition traces, no Saltelli.
Levers stay levers: speed is a decision, not an uncertainty, so the Sobol Y is the argmin
view (grid quantization adds staircase noise — acceptable for screening; a solver method
on the lever axis if it shows in the confidence intervals).

**Infeasible samples are signal, not failure**: wide ranges *will* cross feasibility
edges. Saltelli pairing can't drop rows, so the run always reports the infeasible
fraction per case; with zero it computes objective indices normally, otherwise it also
computes indices on the *feasibility indicator* (which params push a case off the cliff)
and, for the objective, substitutes a study-declared penalty (`infeasible_value:`) or
skips that case's objective indices with a note. Nothing errors; all cells land in the
store.

### 5. Execution

One kernel call per composition per study; broadcasting replaces the sweep and
grid-search loops. After evaluation, the executor reduces the block per each search axis's
method:

- `optimization: none` (sweep) — leave the axis in place.
- `optimization: exhaustive_search` (optimize) — `argopt` the objective measure over the
  axis and **carry every other measure at that index** (xarray `isel`, so the optimal
  lever value and all itemization at the optimum survive). The *full* pre-reduction block
  is retained too — the landscape and the optimum are the same materialization.

Chunk the sample dimension if intermediate memory ever matters (~15 MB/array at the d=100
blast). Process pools and numba/jax are moot at nanoseconds per point. Adaptive optimizers
later plug into the method seam: loop the iterations, vectorize each across the sample axis
(every sample's candidate lever evaluated simultaneously), or fall back to scalar 0-d
evaluation — same kernel either way.

### 6. The store

The in-memory block is an **xarray `Dataset`**: named dims (`sample`, `op_v_kn`, `d_km`,
…) and one variable per measure. This *is* the "measures × axes × reductions" algebra —
the optimizer is `ds.isel(op_v_kn=ds[objective].argmin("op_v_kn"))` (carrying every
measure), per-slice Sobol is `ds.sel(d_km=v)`, and the views are selects. Chosen over
plain numpy + pandas because the reduce-over-some-axes-while-keeping-others operation
(optimize levers, retain conditions) is groupby/idxmin/merge gymnastics in pandas and one
labeled call here; the cost is one dependency and a flatten-on-write.

Three storage tiers, each doing one job:

- **netCDF** (`results/sobol/<study>/block.nc`, via `h5netcdf`) — the authoritative N-D
  block, lossless: dims, coords, and every measure preserved. The full evaluated grid
  persists, not just winners. (zarr is a one-line swap if a blast ever outgrows RAM or we
  want append-per-case / lazy reads — not now.)
- **parquet** (`samples.parquet`) — the *derived* flat table (each measure broadcast to
  full shape and raveled, varied coordinates as columns), regenerable from the netCDF, for
  plotly and any later PRIM/scenario-discovery tooling that wants rows. `indices.parquet` +
  `indices.csv` hold long-form indices: `case, slice, param, S1, S1_conf, ST, ST_conf`
  (+ S2 pairs when computed).
- **study.yaml** — snapshot of the study spec (roles, ranges, N, seed) that produced the
  run.

`run.py` keeps writing `results/lcot.{parquet,csv}` — reframed as the argmin *view* over
its stored block.

### 7. Views (the analysis end)

All queries over the store; the full pre-reduction block is what makes several of them
free (the optimum is an index *into* the block, not a recomputation). The four we want
first:

- **Sobol indices** — `S1`/`ST` per param as sorted horizontal bars (the tornado analog;
  a proper tornado is a one-at-a-time ± idiom, this is the correct Sobol version), CI
  whiskers from the bootstrap, optional `S2` heatmap. With a swept axis, small-multiples
  across its slices ("how sensitivity shifts with the condition").
- **swept vs objective, per composition** — a line per strategy over the swept axis;
  crossovers are where lines cross.
- **swept vs optimized** — the swept condition on x, the argmin lever value on y (e.g.
  `d_km` vs optimal `op_v_kn`): how the best decision shifts with the condition.
- **lever vs objective, optimum marked** — the pre-reduction lever curve for a chosen
  sample with the argmin starred; the same plot as swept-vs-objective with the axes'
  roles swapped, which is exactly why swept and optimized being one kind of axis pays off.

Sensitivity analysis and subspace selection are the same activity — indices are hints for
which views to open next. Viz joins the `plots.py` rebuild.

## Borrowing from the literature, not the runner

`ema_workbench` (TU Delft) implements this loop — but its execution layer is
loop-shaped (a scalar callable per experiment), which the vectorized kernel makes
redundant; adopting it would trade our nanoseconds-per-point block path for its
per-experiment dispatch plus heavy deps. Dropped from the critical path. What stays
borrowable on top of *our* store: its analysis side (PRIM/CART for scenario discovery,
feature scoring) reads any tidy experiments/outcomes table, and our `samples.parquet` is
one. Revisit when scenario discovery earns a dependency. The four-piece core
(values+ranges → design → store → views) stays model-agnostic and portable to other
projects; extract it when a second project actually exists, not before.

## Implementation order

1. **Numpy-safe kernel pass** — strategies, `_shared`, `sources` methods, `helpers.crf`;
   behavior under scalars unchanged, existing loops untouched. Acceptance:
   `results/lcot.csv` byte-identical.
2. **Design/array builder + block execution** — roles become named axes, `Point` and
   `optimizer.py`'s loops retire (strategies drop the `point` parameter), the block is an
   xarray `Dataset`, the objective reduction reproduces the artifact, flat-axis variance
   check + feasibility masking land here. Acceptance: `results/lcot.csv` identical from the
   block path (the prototype already demonstrates one-ulp agreement, identical masks and
   argmins on `port_swap_battery`).
3. **Ranges-with-values + studies** — loader unwrap (+ `sync_excel.py` round-trip check),
   `studies.yaml` with the three roles, `salib` + `xarray` + `h5netcdf` dependencies;
   sample / optimize / sweep, per-slice Sobol, objective as a chosen measure.
4. **Views/artifacts** — netCDF block + `samples.parquet` + indices under
   `results/sobol/`; the four plots in the `plots.py` rebuild; update TODO.md (collapse the
   readiness section; retire the old Stage-1/Stage-2 vectorization framing, which this plan
   supersedes).

Verification beyond the golden diffs: a single-param study must return S1 ≈ ST ≈ 1; a
two-param study on params with known relative leverage must rank them; one real study at
N and 2N must agree within the bootstrap confidence intervals.
