# Restructuring target — v6 delta on `sobol_sensitivity_plan.md`

> **SUPERSEDED by `architecture_v6.md` (2026-07-18).** This "delta on v5" framing structurally
> privileged the prior-agent v5 design as the baseline, which is the anchoring the standalone v6
> was written to avoid. Read `architecture_v6.md` instead; this is kept for history only.

Status: **superseded**, drafted 2026-07-18. The v5 exploration-architecture plan
(`sobol_sensitivity_plan.md`) stands as the architecture and its rationale. This doc records
(a) where the **current code diverged** from v5 and must change to realize it, and (b) the
**deltas beyond v5** decided in the 2026-07-17/18 restructuring discussion. Read v5 first for
the *why*; this is the concrete code target and the decisions v5 didn't yet make.

## Where the code diverged from v5 (fix to realize the plan)

- **`kernel/` misuses the word "kernel."** In v5 the *kernel* is the pure function — the
  **strategies** (the one strategy-aware place). v5 gives the surrounding stages deliberately
  neutral names and argues (v5 §"Why these names") they must not be named after the kernel or the
  optimizer. The code's `kernel/` package (ingest/evaluate/analyze/store) is exactly those neutral
  stages wearing the wrong name. → Drop the `kernel/` umbrella; the strategies are the kernel.
- **Objective is collapsed.** v5 §objective splits *optimize-by* (the measure the lever argmin
  minimizes) from *decompose* (the measure Sobol targets); they need not be the same. The code
  hardwires one `objective` for both. → Build the split.
- **No `optimization:`-method seam.** v5 makes the lever-collapse method an *axis* property
  (`none`=swept, `exhaustive_search`=argmin, future solver) so an adaptive optimizer can own the
  kernel calls later. The code hardcodes the argmin in `evaluate`. → Add the method dispatch.
- **Dropped guards.** The flat-axis variance check and the consumption rules v5 specifies
  (§Design stance, §4 semantics) are absent — this is the cluster of review findings (unconsumed
  axis → NaN Sobol, silent path typos, role collisions).
- **Validation gaps.** Placement checks only parent path segments; `dist:` is unvalidated; the
  ±20% default range inverts for negative/zero nominals. All are v5's "structural validation stays
  loud" promise going unmet.

## Deltas beyond v5 (new decisions, 2026-07-17/18)

1. **Per-case role overrides.** A case may override a param's role inline — most importantly
   `optimize` a param the rest of the study fixes. Motivating case: `nuclear-int-el` wants
   `shared.design_v_kn` optimized (a peak-power counterforce sizes the converter), while every
   other case fixes it. v5 had study-level roles only.
2. **`cases:` move `assumptions.yaml` → `studies.yaml`.** v5 §2 put compositions in
   assumptions.yaml; moving them lets a case carry its per-case role overrides (#1) in one place.
   `assumptions.yaml` becomes the pure parts catalog + shared scalars.
3. **optimize/decompose split — build now.** Realize v5's objective algebra:
   `optimize_by` (measure for the lever argmin) and `decompose` (measure(s) for Sobol), separately
   settable. Defaults preserve today's behavior (see below).
4. **xarray-native inside the kernel.** Make the varied leaves `xr.DataArray`s carrying their dim
   name, so strategy arithmetic aligns *by name* and the block falls out with named dims — deleting
   `_reshaped`, the positional dim bookkeeping in `evaluate`, and the index-based `_collapse`.
   (v5 used xarray only for the output store; leaves were numpy.) **Open:** verify how much strategy
   code actually changes — much numpy-ish code (`np.where`, `np.maximum`) already dispatches on
   DataArrays via `__array_ufunc__`, so this may be closer to a leaf-type swap than a full rewrite.
5. **Names.** `run.py` = entry point **and** orchestration (no separate `pipeline.py`/`lcot.py`);
   one `config.py`; compute stages are flat neutral modules.

## Target module layout

```
scripts/
  run.py             # entry + orchestration (argparse + the study loop)
  config.py          # load_assumptions(), load_studies(), apply_schema() -> Study
  schema.py          # frozen dataclasses; Ranged leaf; consolidated `shared` block
  compose.py         # Study -> [Case]: place sample/sweep axes, build per-case models  (T3)
  evaluate.py        # Case -> EvaluatedCase: optimizer loop (method seam) + lever collapse
  optimize.py        # lever-collapse methods: exhaustive_search now; solver seam for later
  analyze.py         # [EvaluatedCase] + decompose -> Sobol tables (per-case + cross-case)
  store.py           # netCDF block + parquet + study.yaml snapshot
  measures.py        # derived / cross-case measures (v5 §modules) — small, question-specific
  model/
    strategies/      # THE KERNEL — the only strategy-aware code; xarray-native
    costing.py
  viz/plots.py
```

("compose" subsumes v5's "design" and the code's "ingest" — three names for the same stage; pick
one and retire the others.)

## Target flow (decided signatures)

```python
# run.py
assumptions_raw = config.load_assumptions()          # dumb nested dict + {value,range,dist} leaves
studies_raw     = config.load_studies()              # dumb nested dict, one entry per study

for name, study_raw in studies_raw.items():
    study     = config.apply_schema(assumptions_raw, study_raw)   # T1+T2: merge + validate -> Study
    cases     = compose(study)                                    # T3: sample/sweep placed, per case
    evaluated = [evaluate(case) for case in cases]               # optimizer loop + collapse inside
    result    = analyze(evaluated, study.decompose)              # per-case + cross-case Sobol
    store.write(study, evaluated, result)
    # plots on demand
```

`Study` is self-contained: the validated components + this study's case compositions + the roles
(study-wide sample/sweep + per-case optimize) + `optimize_by` / `decompose` / `n` / `second_order`.

## The object ascent, with validation tiers

| # | object | built by | validated |
|---|---|---|---|
| ① | `assumptions_raw`, `studies_raw` | `config.load_*` | nothing (dumb parse) |
| ② | — (merged in ③) | | |
| ③ | **`Study`** — validated config-with-overrides + roles + measures | `config.apply_schema(assumptions_raw, study_raw)` | **T1** structure/leaf-types + **T2** paths resolve, ranges `lo<hi`, dists known, cases & measures exist |
| ④ | **`[Case]`** — per-case models, sample/sweep placed as named DataArrays, lever *not* yet | `compose(Study)` | **T3** (below) |
| ⑤ | **`EvaluatedCase`** — `xr.Dataset` over (sample, sweep), lever collapsed, measures at optimum | `evaluate(Case)` | feasibility is data, not error |
| ⑥ | **Sobol tables** | `analyze(evaluated, decompose)` | flat-axis variance check (report) |
| ⑦ | store + plots | `store.write`, `plots` | — |

**T1 + T2 are one schema application** (your "two drones, one stone"): validate the *merged*
`Study`, not assumptions and studies separately — the overrides are applied and the result is
checked once. Caveat to keep in mind: error messages should still say whether a bad leaf came from
assumptions or the study, so attribution survives the merge.

**T3 (in `compose`, per case)** — this is the tier that needs the case list, so it lives in the
per-case build loop, not the schema pass:

- **Per-case override path resolves in this case.** `nuclear-int-el` optimizing `shared.design_v_kn`
  → `shared.*` is in every case, OK; a case optimizing a source param its composition lacks → error
  naming the case.
- **Study-wide sample/sweep path, consumption** (v5 §4 semantics, made structural by per-case
  composition):
  - path's source absent from this case → **skip for this case; index is exactly 0 by construction**
    (correct, not an error) — record the case as invariant to it so `analyze` reports N/A, not NaN.
  - path in no case at all → **error** if explicitly listed; **not selected** (noted) under blast.
- **Full-path placement.** Setting a leaf validates the *final* segment too (fixes the silent
  dead-key typo), not just its parents.
- **No role collision.** A path assigned two roles → error (checkable at ③ or ④).

This maps almost one-to-one onto the review's correctness findings — they were all missing T2/T3
checks, which is a good sign the tiering is drawn in the right place.

## The optimize / decompose split

`Study.objective` becomes two fields:

- **`optimize_by`** — the measure the lever `argopt` minimizes/maximizes. Default `lcot`.
- **`decompose`** — the measure(s) Sobol targets, per swept slice. Default = `optimize_by`
  (so an unspecified study behaves as today: decompose what you optimized). May be a list, and may
  name a *derived/cross-case* measure (`lcot_nuclear − lcot_fuel`) from `measures.py` — that's the
  "what drives the crossover" study in v5's algebra.

`evaluate` reads `optimize_by`; `analyze` reads `decompose`. They no longer have to agree.

## Per-case optimize — why it's safe and where it goes

The reason this is cheap despite the shared block layout: **of the three roles, only optimize is
collapsed away before anything downstream needs cases to align.** `sample` and `sweep` dims are
*retained* into the datasets and must be study-wide (joint Sobol, cross-case comparison); `optimize`
dims are *transient* — they exist only between placement and collapse, and `evaluate` already
collapses per case. So a case having an extra lever dim its sibling lacks disturbs nothing: after
collapse both are back to `(sample × sweep)` and aligned. Placement of the lever moves *into*
`evaluate`'s optimizer loop (per v5's method seam) — which is also what lets a future solver choose
its own points instead of a full grid.

## Optimizer seam

- **`exhaustive_search`** (now): materialize the lever grid, `argopt` `optimize_by`, carry every
  measure at that index. Trivial; wraps today's `_collapse`. Grid-quantization staircase noise is
  the accepted screening cost (v5).
- **solver** (later): owns the kernel calls — loop iterations in Python, **vectorize each across the
  sample/sweep axes** (all slices step at once, converged ones masked). Bounded work, not a freebie;
  a scalar per-slice `scipy.minimize` would lose the block vectorization.

## Open questions (before implementing)

- **Compute stages: flat modules vs a package?** v5's neutral-names argument leans flat
  (`compose.py`, `evaluate.py`, … directly under `scripts/`). If a package, it must not be named
  `kernel` (that's the strategies). Decide the umbrella (or none).
- **`decompose`: single measure or list; default `optimize_by` or `lcot`?** Leaning list, default
  `optimize_by`.
- **xarray-native strategies: how much actually changes?** Verify on one strategy
  (`port_swap_battery`) whether it's a leaf-type swap + a few idioms or a real rewrite, before
  committing the whole `strategies/` package.
- **Derived/cross-case measures home.** v5 puts them in `measures.py`; confirm that's where
  `decompose` resolves cross-case targets.

## Not in scope of this restructure (still deferred)

Smart/adaptive solver method; sensitivity-viz beyond the four v5 plots; the umbrella-`lcot` package
rename (roadmap item 5); the model-fidelity TODOs. This restructure is plumbing + naming + realizing
v5's already-designed seams, plus the five deltas above.
