# Exploration architecture — vectorize-first

Status: **proposal**, revised 2026-07-15 (v4). This iteration promotes the numpy-safe
kernel from "deferred optimization" to the **architectural basis**: with ranges declared
in the input and strategies that broadcast, the sampling machinery collapses into array
construction — the dotted-path override mechanism, the `Point` channel, and the
optimizer's loops from earlier iterations are superseded and never get built (git history
holds those designs and their rationale). Sensitivity *viz* stays deferred to the
`plots.py` rebuild.

## The pipeline

The model is one pure function — the **kernel**: a fully-specified point in parameter
space, plus a **composition** (platform × drivetrain × sources × strategy — the discrete,
structural coordinates), maps to one output row (`lcot` + itemization). Around it:

```
values + ranges  ->  design  ->  execution  ->  store  ->  views
  (config)        (which points,  (kernel on    (rows)   (queries: argmin, traces,
                   as arrays)      blocks)                 indices, named scenarios)
```

- **Design** decides *which points to evaluate* and expresses the answer as array-valued
  config leaves: sampled params get jointly-drawn columns sharing one dimension, swept and
  optimized params get their own factorial dimensions, everything else stays scalar.
  Parameters have no intrinsic roles — *constant / swept / optimized / sampled* are
  per-run assignments, one line in a study.
- **Execution** is one kernel call per composition; broadcasting does what the sweep and
  grid-search loops used to do.
- **Views** answer every question as a query over the stored rows: an optimum is
  groupby-argmin over the lever dims, a trace is a filter, Sobol indices are an estimator
  over a Saltelli-shaped run, a named scenario ("tender, transpacific, pessimistic
  batteries") is a filter — and may span the composition dimension ("cheapest nuclear
  option") as well as slice it.

The frame maps one-to-one onto the exploratory-modeling / Robust Decision Making
literature (Bankes 1993; Lempert et al., RAND) — **XLRM**: X, exogenous uncertainties =
sampled/swept ranges; L, levers = optimized params (`op_v_kn`); R, relationships = the
composition (the "integer param"); M, measures = the output row. "Cases as names for
regions in the result space" is *scenario discovery* (PRIM/CART); "sensitivity ≈ choosing
what to plot" is Saltelli's *factor mapping*.

Two structural consequences:

- **Optimize-by-grid is a view, not a phase.** The old optimizer evaluated its grid and
  kept the argmin; the block simply keeps everything, and argmin-over-lever-dims is a
  query. A smarter-than-grid optimizer is *adaptive* — it chooses points from results —
  so it returns as a design-side loop (iterations loop, each iteration evaluated
  vectorized across all samples at once, or plain scalar: the same kernel runs 0-d
  arrays).
- **Roles move to run-time; they don't disappear.** Dense designs must stay
  low-dimensional (factorial dims), global designs must be sparse (one shared sample
  dim). Blast globally by sampling → read indices → assign dense x/y roles for the local
  views the indices point at.

## Design stance — guards inform, they don't gate

The model's correctness rests on judgment the code can't check (is 0.02 a sane
`detach_frac`?); the code's job is to make what *is* checkable loud and everything else
frictionless:

- **Structural validation stays loud and blocking.** An unknown config key `TypeError`s
  in the loader; ranges are declared *on* the value (no path to misspell); varying a
  param whose value feeds a Python-level branch fails immediately with an ambiguous-truth
  error naming the line. Typos and structural impossibilities, never intent.
- **Intent-level checks report, they never block.** A flat parameter (no effect along its
  dim — a nan-aware variance check on the block), an infeasible region, an unresolvable
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

The array builder is the one new module (~100 lines): resolve a study's selections
against the harvested ranges, allocate dimensions, reshape, place into the config dict,
build Cases through the unchanged loader.

- **Dimension allocation** — the picture is *not* one dim per parameter (factorial
  explosion): **sampled params share one dimension** (SALib's Saltelli output is a matrix
  of jointly-drawn rows, N(d+2) × d — column *i*, reshaped to dim 0, becomes param *i*'s
  leaf), while **swept and optimized params get their own factorial dims** (a lever's 18
  speeds are dim 1). A block is (samples × lever grid), dense only where low-dimensional.
- **SALib wiring** (chosen earlier over scipy-QMC + hand-rolled estimators and over fully
  hand-rolled — the estimator subtleties and bootstrap CIs are exactly what we shouldn't
  own): build the problem dict from the selected ranges, `sample.sobol` (N a power of 2,
  `calc_second_order` per study), evaluate, argmin over lever dims → Y in row order →
  `analyze.sobol`. One-shot; no feedback loop. Morris screening is a cheaper first pass
  if d ever reaches the many hundreds — noted, not planned.
- **Studies** select and assign; cases and params default to *everything*, so the
  blast-everything study is an empty spec:

```yaml
# studies.yaml — role assignment + narrowing over the ranges declared in config.yaml
studies:
  blast:                        # defaults: every case, every param that has a range
    mode: sobol                 # sobol -> Saltelli + indices | sweep -> per-param 1-D traces
    n: 1024
  tender-screening:
    mode: sobol
    cases: [tender, fossil]     # one shared sample matrix across member cases
    params: [sources.tender-reactor.*, params.route.detach_frac]   # paths or globs
    fix: {params.route.d_km: 8000}      # constants for this run (override the nominal)
    n: 1024
    second_order: false
```

Semantics: one shared sample matrix per study across member cases (cross-case Ys — gaps,
rankings — stay answerable post-hoc from the store); a path whose source name is absent
from a member case is skipped for that case (its index is exactly zero by construction —
correct), any other unresolvable segment errors as a typo; an explicitly listed param
resolving in *no* member case errors, under default-all it is simply not selected (noted
in the run summary). `mode: sweep` shares everything but the math: per-param 1-D traces,
others at nominal — the mass-produced version of a hand-written sweep axis. Levers stay
levers: speed is a decision, not an uncertainty, so the Sobol Y is the argmin view (grid
quantization adds staircase noise — acceptable for screening; scipy 1-D refinement if it
shows in the confidence intervals).

**Infeasible samples are signal, not failure**: wide ranges *will* cross feasibility
edges. Saltelli pairing can't drop rows, so the run always reports the infeasible
fraction per case; with zero it computes LCOT indices normally, otherwise it also
computes indices on the *feasibility indicator* (which params push a case off the cliff)
and, for LCOT, substitutes a study-declared penalty (`infeasible_lcot:`) or skips that
case's LCOT indices with a note. Nothing errors; all rows land in the store.

### 5. Execution

One kernel call per composition per study; broadcasting replaces the sweep and
grid-search loops. Chunk the sample dimension if intermediate memory ever matters
(~15 MB/array at the d=100 blast). Process pools and numba/jax are moot at nanoseconds
per point. Adaptive optimizers later: loop the iterations, vectorize each iteration
across the sample dim (every sample's candidate lever evaluated simultaneously), or fall
back to scalar 0-d evaluation — same kernel either way.

### 6. The store

Blocks flatten to rows (each output column broadcast to full shape and raveled, with the
varied coordinates as columns): the *full* evaluated grid persists, not just winners.
`run.py` keeps writing `results/lcot.{parquet,csv}` — reframed as the argmin *view* over
its stored grid; studies write under `results/sobol/<study>/`:

- `samples.parquet` — one row per (cell × case), all itemization columns + `feasible`;
  sweep-mode runs write the same shape.
- `indices.parquet` + `indices.csv` — long form: `case, param, S1, S1_conf, ST, ST_conf`
  (+ S2 pairs when computed).
- `study.yaml` — snapshot of the study spec that produced the run.

### 7. Views (the analysis end)

All queries over the store: argmin tables (the current artifact), LCOT-vs-X traces, Sobol
indices, feasibility maps, named scenarios (filters; PRIM boxes when scenario discovery
earns a dependency). Sensitivity analysis and subspace selection are the same activity —
indices are hints for which views to open next. Viz joins the `plots.py` rebuild.

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
2. **Design/array builder + block execution** — axes become dims, `Point` and
   `optimizer.py`'s loops retire (strategies drop the `point` parameter), argmin view
   reproduces the artifact, flat-axis variance check + feasibility masking land here.
   Acceptance: `results/lcot.csv` identical from the block path (the prototype already
   demonstrates one-ulp agreement, identical masks and argmins on `port_swap_battery`).
3. **Ranges-with-values** — loader unwrap (+ `sync_excel.py` round-trip check),
   `studies.yaml`, `salib` dependency; `mode: sobol` (shared sample dim → indices) and
   `mode: sweep`.
4. **Views/artifacts** — indices + summaries under `results/sobol/`; update TODO.md
   (collapse the readiness section; retire the old Stage-1/Stage-2 vectorization framing,
   which this plan supersedes).

Verification beyond the golden diffs: a single-param study must return S1 ≈ ST ≈ 1; a
two-param study on params with known relative leverage must rank them; one real study at
N and 2N must agree within the bootstrap confidence intervals.
