# LCOT exploration architecture — v6

Status: **spec, in implementation** (started 2026-07-18). This is the authoritative target.

**Provenance / why this is written fresh.** The prior plan `sobol_sensitivity_plan.md` ("v5") and
the `restructuring_target.md` delta were authored by earlier agent sessions and drove the code this
restructure changes. To avoid re-importing that design attractor, v6 is derived from the
2026-07-16…18 design conversation as its primary source. v5 is retained only as a *checkable-
mechanics* reference (Saltelli's `N(d+2)` shape, numpy broadcast semantics, the vectorize-across-
sample optimizer pattern) — its *design choices* carry no authority here, and v6 overrules several
(cases location, the dict-mutation placement pattern, study-only roles). Where v6 and v5 agree, the
reason is stated independently, not borrowed.

---

## 1. Principles

1. **Broadcasting is lazy.** Only *varied* leaves become arrays; constants stay scalar and ride
   along at stride 0. No "big array"; no tiling of constants.
2. **Ascend, don't mutate.** Build upward: raw dict → validated structured object → arrayed object.
   Builders are pure functions of `(raw, overrides)`; no deepcopy-and-patch of a config dict.
3. **Named axes (xarray) inside the kernel.** Varied leaves are `xr.DataArray`s carrying their dim
   name + coords; strategy arithmetic aligns *by name*. This deletes the manual `_reshaped` and the
   positional dim bookkeeping. (Empirically a leaf-swap: arithmetic, `np.maximum/minimum/ceil`
   already dispatch on DataArrays; only `np.where`→`xr.where` and coord assignment are needed.)
4. **Cases are a loop, not an axis.** Compositions differ structurally (strategy function, source
   set) — arrays can't span that. sample/sweep/optimize are numeric axes; the case set is a loop.
5. **Three roles, and the cost model.** `fixed` (scalar), `sampled` (shares axis 0, additive cost,
   variance-decomposed), `search` (own factorial axis, multiplicative cost). `search` splits into
   `sweep` (retained condition) and `optimize` (lever, collapsed).
6. **The objective is a chosen measure reduced over chosen axes** — and *optimize-by* need not
   equal *decompose*.
7. **Guards inform or block by kind.** Structural impossibility (typo'd path, unknown key) blocks
   loudly; intent-level facts (a flat/unconsumed axis, infeasible regions) are reported, not fatal.

## 2. Files (the input end)

- **`assumptions.yaml` — the parts catalog.** `platforms:`, `drivetrains:`, `sources:`, and one
  **`shared:`** block (economics + margins + voyage scalars `d_km`/`op_v_kn`/`design_v_kn` + market
  load). No `cases:`. A leaf is a scalar or a ranged wrapper `{value:, range: [lo, hi], dist:}`;
  the range/dist is a *prior about the parameter*, kept **on** the leaf.
- **`studies.yaml` — compositions + roles.** Each study names its `cases:` (each a
  platform×drivetrain×sources×strategy composition, optionally with **inline per-case role
  overrides**) and the study-wide roles (`sample`/`sweep`/`optimize`/`fix`), plus `optimize_by` /
  `decompose` / `n` / `second_order`.

> **v6 vs v5:** cases move *out* of assumptions into studies (v5 put them in config). Motive: a case
> and its per-case role overrides belong together, and composition is study design, not parts data.

## 3. Objects (the ascent)

| # | object | built by | holds |
|---|---|---|---|
| ① | `assumptions_raw`, `studies_raw` | `config.load_assumptions/load_studies` | plain nested dicts |
| ② | **`Study`** | `config.apply_schema(assumptions_raw, study_raw)` | validated parts + this study's case specs + roles + `optimize_by`/`decompose`/`n`/… ; sampled paths carry resolved `Range`s |
| ③ | **`Case`** (list) | `compose(Study)` | one composition; leaves as scalars or **named DataArrays** with sample+sweep axes placed (lever *not* yet); the case's lever spec (study + per-case optimize) attached |
| ④ | **`EvaluatedCase`** | `evaluate(Case)` | `xr.Dataset` over (sample, sweep), lever collapsed, every measure carried at the optimum |
| ⑤ | tables + store | `analyze`, `store.write` | Sobol/feasibility long-form; netCDF block; parquet; `study.yaml` snapshot |

`schema.py` frozen dataclasses: a **`Ranged`** leaf (`value`, `range`, `dist`) used during
validation; the consolidated **`Shared`** block; `Platform`/`Drivetrain`/`EnergySource` family
(unchanged data); `Study`, `Case`, `Axis`, `Range`.

## 4. Modules & flow

Flat compute modules under `scripts/` (no `kernel/` package — in v6 the "kernel" is the *strategies*,
the only strategy-aware code; the surrounding stages get neutral names):

```
scripts/
  run.py         entry + orchestration (argparse + the study loop)
  config.py      load_assumptions(), load_studies(), apply_schema() -> Study
  schema.py      frozen dataclasses (Ranged, Shared, Study, Case, Axis, sources…)
  compose.py     Study -> [Case]: place sample/sweep axes as named DataArrays; T3 checks
  evaluate.py    Case -> EvaluatedCase: optimizer loop (method seam) + lever collapse
  optimize.py    lever-collapse methods: exhaustive_search now; solver seam later
  analyze.py     [EvaluatedCase] + decompose -> Sobol tables (per-case; cross-case = future)
  store.py       netCDF + parquet + study.yaml snapshot
  common/        paths.py, helpers.py, units.py   (unchanged utilities)
  model/
    costing.py   per-source cost/sizing (xarray-safe)
    strategies/  THE KERNEL — xarray-native (np.where -> xr.where; else unchanged)
  viz/plots.py
```

```python
# run.py
assumptions_raw = config.load_assumptions()
studies_raw     = config.load_studies()
for name, study_raw in studies_raw.items():
    study     = config.apply_schema(assumptions_raw, study_raw)   # T1+T2
    cases     = compose(study)                                    # T3; sample/sweep placed
    evaluated = [evaluate(case) for case in cases]               # optimizer loop + collapse
    tables    = analyze(evaluated, study)                         # decompose per study.decompose
    store.write(study, evaluated, tables); ...                   # + plots on demand
# `run` (fleet) renders results/lcot.{parquet,csv} as the argmin view of the fleet study's block.
```

## 5. Validation tiers

**T1 + T2 are one schema application** (`apply_schema`, on the *merged* Study — "two drones, one
stone"): T1 = structure/leaf-types/no-stray-keys over the merged parts; T2 = every study-referenced
dotted path resolves to a real leaf, every `Range` has `lo < hi`, every `dist` is known, named cases
and the `optimize_by`/`decompose` measures exist. Error messages name whether a bad leaf came from
assumptions or the study (attribution survives the merge).

**T3 is per-case, in `compose`** (needs the case list, hence the case-build loop before evaluation):

- **Per-case override path resolves in this case** (`shared.*` is in every case; a source param a
  case lacks → error naming the case).
- **Consumption** (structural, because per-case composition makes it visible): a study-wide
  sample/sweep path whose component is absent from this case → **skip for this case; its index is 0
  by construction** (correct; record the case as invariant so `analyze` reports N/A, not NaN). A
  path in **no** case → **error** if explicitly listed, **not selected** (noted) under blast.
- **Full-path placement** validates the *final* segment, not just parents (kills the silent
  dead-key typo).
- **No role collision** on one path.
- **Flat-axis report** (post-eval, in analyze): a varied param with ~0 variance along its axis is
  reported, not fatal.

These tiers are exactly the checks whose absence produced the code-review correctness cluster
(unconsumed axis → NaN Sobol; silent path typo; role clobber; unvalidated `dist`; inverted ±20%
default) — a sign the tiering is placed right.

## 6. Roles, axes, and the optimizer seam

- **sample** — Saltelli joint draw on axis `sample`; study-wide (joint Sobol needs one shared
  matrix across member cases); retained.
- **sweep** — factorial condition axis; study-wide; retained (one Sobol analysis per slice).
- **optimize (lever)** — factorial axis; **collapsed** by `optimize_by`. Because it collapses away
  before anything downstream aligns cases, it may be **per-case** (study-wide default + inline
  per-case additions/overrides). The collapse method is an **axis property**:
  - `exhaustive_search` (now): materialize the grid, `argmin` `optimize_by`, carry every measure at
    that arg (`argmin("dim")` + `isel`). Grid-quantization staircase noise accepted for screening.
  - solver (later): owns the kernel calls — loop iterations in Python, vectorize each across
    sample/sweep, mask converged. Seam only; not built now.

Placement of the lever moves **into** `evaluate`'s optimizer loop (so a future solver picks its own
points); sample/sweep placement is one-shot in `compose`.

## 7. Objective algebra — the optimize/decompose split (built now)

`Study` carries two measure fields (replacing the single `objective`):

- **`optimize_by`** — the measure the lever `argmin` minimizes. Default `lcot`.
- **`decompose`** — the measure(s) Sobol targets, per swept slice. A list; default `[optimize_by]`
  (so an unspecified study behaves as today). May name a *derived* measure later; cross-case /
  derived-measure resolution (v5's `measures.py`) is a documented seam, **deferred** until a study
  needs it.

`evaluate` reads `optimize_by`; `analyze` reads `decompose`. They no longer must agree.

## 8. xarray-native kernel (the leaf-swap)

`compose` assigns each varied leaf as an `xr.DataArray(values, dims=name, coords={name: grid})`;
constants stay Python scalars. Strategies then run unchanged **except** `np.where(...)` →
`xr.where(...)` (in `_shared._finalize`, `costing.battery_life_yr`, and any strategy) — `np.where`
returns a bare ndarray and drops dims, `xr.where` keeps them. The output block is naturally an
`xr.Dataset` with named dims; the collapse is `da.argmin(lever_dims)` + `isel`. `_reshaped`,
`_set_path`, `Design`, and the positional `_collapse` retire.

## 9. Per-case optimize — mechanism now, activation later

The mechanism (inline per-case `optimize:` in a study's case entry) is **built** but **unused by the
current three studies**, so numeric parity is preserved. Activating it for the recurring case —
`nuclear-int-el` optimizing `shared.design_v_kn` instead of fixing it — *changes that case's
numbers by design* and is a modeling decision to make separately, not part of this plumbing
restructure. It needs the peak-power counterforce (TODO) to be meaningful.

## 10. Acceptance & scope

- **Acceptance:** `results/lcot.csv` reproduced from the rebuilt fleet path to numerical-error level
  (byte-identity is not required; the project standard is numeric-error agreement). Plus the three
  studies run end-to-end and the single-param `lfp-price-check` returns S1≈ST≈1.
- **In scope:** the module/flow restructure, `config.py`/`run.py`/`schema.py`, ascent builders,
  xarray-native leaves, T1–T3 validation, the optimize/decompose split, per-case-optimize mechanism,
  cases→studies.yaml, consolidated `shared`.
- **Deferred (not this restructure):** adaptive solver method; cross-case/derived measures +
  `measures.py`; sensitivity viz beyond the current plots; the umbrella-`lcot` package rename;
  model-fidelity TODOs.
