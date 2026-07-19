# Walkthrough notes

Running notes from a line-by-line walk of the execution path
(`uv run python scripts/run.py run` → …). Problems and refactors spotted along the way, to act on
later / fold into TODO.md.

## `run.py` — the CLI entry point

Three problems with how `_cmd_run` (`scripts/run.py:23–30`) reaches into the build pipeline:

1. **Loaders surface working material in the CLI.** `load_assumptions` and `load_studies` return
   raw, working-stage data (basically dicts — bound here as `raw, ranges` and
   `case_specs, studies_raw`), and the CLI holds those locals directly. A pure CLI wrapper
   shouldn't be juggling half-built intermediate material.
   *(To confirm when we read `load_assumptions` — just how raw is it.)*

2. **Schema application is nested into the CLI.** `apply_schema(...)` is even more of a build
   stage, and calling it inline inside `run_study(apply_schema(...), raw, case_specs)` buries a
   real pipeline stage in the argument list of a CLI dispatch.

3. **The wrapper is too thin to justify its own file.** Once 1–2 are cleaned up there's almost
   nothing left in `run.py` — the argparse could live directly in the script that runs the loop
   over the studies. → **DONE: merged `run.py` and `pipeline.py`** into one entry+orchestration
   script. `run.py` now owns the CLI plumbing, the central `config.get_studies(...)` loop, and
   `run_study(study)` (compose → evaluate → analyze → store). `viz/plots.py` calls `run.run_study`
   directly.

Related — **DONE**: dropped the `from config import load_assumptions` (bare-name) import style;
call sites now read `config.get_studies(...)` (context-preserving).

## `pipeline.py` — the one-study pipeline

- **Bug (fixed): `pipeline.py:34` annotated `indices: pd.DataFrame` with no `pandas` import.**
  Introduced when `build_results` (and its `import pandas as pd`) was deleted in the unification
  commit; survived only because `from __future__ import annotations` (L12) never evaluates the
  annotation. pyright would flag it.
- **Supports the merge (import style):** `pipeline.py` already uses qualified module imports
  (`import compose, evaluate, analyze, store` → `compose.build_study`), i.e. the context-preserving
  form `run.py` is missing. The merged script should adopt pipeline's convention.
- **Design smell (echoes problem #1):** `run_study(study, raw, case_specs)` re-threads raw working
  material (`raw`, `case_specs`) alongside the *built* `study`. The built object isn't
  self-contained — the pipeline still handles what the study was built from. Params are also
  untyped. Worth revisiting what a "built study" should carry so the pipeline takes one cohesive
  object.
- **DONE: moved the reporting + feasibility summary out of the runner into `analyze.report`.** The
  index-reporting and the feasibility/print summary now live in `analyze.py`, so a non-sampling run
  still has analyze doing something meaningful (it summarizes feasibility even when it decomposes
  nothing). `run_study` is now just compose → evaluate → analyze → store, plus the one status line
  + `analyze.report`.

### Clarification on problems #1/#2 (working material in the CLI)

Assigning variables to raw "working material" is fine — it just must not happen in `run.py`. The
reason the config stays a raw dict is deliberate: grab both raw YAMLs and combine them *as we build
the `Study`* so we validate **once**. The fix is the **run/pipeline merge**: after it, `run.py`
owns only the CLI plumbing and the central loop, and stepping into the first file should already
show what's going on — roughly:

```python
studies = config.build_studies(ASSUMPTIONS_PATH, STUDIES_PATH)
for study in studies:
    ...   # compose -> evaluate -> analyze -> store
```

No raw intermediates surfaced at this level.

## `config.py` — parsing the two YAMLs (needs radical simplification)

Overall sentiment: at this point in the pipeline all we've done is parse text + numbers into a
slightly nicer format — not yet turned the ranges wishlist into vectors, not sampled anything. It
should be *much* simpler than it is. The validation gymnastics feel out of proportion; the model
should mostly be **"input doesn't fit the schema → error."**

### Target shape (assembly line)

```python
# config.py
def build_studies(assumptions_path, studies_path):
    a_raw = load_assumptions(assumptions_path)   # ideally just a thin load_yaml wrapper, no _unwrap inline
    s_raw = load_studies(studies_path)
    studies = [ _build_study(a_raw, study_body, case_defs)   # expand each study's case list against
                for study_body in s_raw ]                    # studies.yaml `cases:` before building
    return studies

def _build_study(a_raw, study_overrides, case_defs):
    #  combine raw assumptions + study overrides into this study's params
    #  turn the dead dict-tree into a tree with Ranges everywhere (incl. default-range making)
    #  turn the tree into a real Config/Library object
    #  loop the case list, build each Case from the Config components
    #  take the meta info (optimize_by, roles, ...) from the raw study
    #  assemble Cases + meta -> Study
    return Study
```

The important work (what `_unwrap` does, plus overlaying overrides and building typed objects)
belongs inside `_build_study`, as clear steps — not smeared across load-time helpers.

### `load_assumptions` / `load_studies`

- Fine as thin wrappers around YAML parsing — but `load_assumptions` doing a **last-minute
  `_unwrap`** is not great; that's a build step, it shouldn't ride inside the loader. In the
  target, `load_*` just returns the raw parsed tree (a nested dict). Consider a single
  `load_yaml(path)` wrapper so the raw-parse detail isn't inline in two places.
- `load_studies` iterating into `(cases, studies)` is premature: just parse both files and hand
  them to the study builder. The non-override parts of the study body have their natural home
  **inside the `Study` object**, not as loose dict juggling here.
- Likely a clean library approach: **overlay** the study overrides onto the assumptions tree
  (deep-merge) and validate the result — rather than hand-threading `(raw, ranges, case_specs,
  body)` around.
- Cleanup: **redundant `import yaml`** inside `load_assumptions` (L148) — already imported at
  module level (L29).

### `_unwrap` is smelly — must go

- Too many nested `if`s (even one nested `if` is ugh) + recursion. The future maintainer isn't an
  experienced programmer, so this has to be readable. A recursive hand-rolled walker is the wrong
  tool.
- What it actually does: distinguish a **ranged-leaf wrapper** (`{value, range, dist}`) from a
  structural sub-block, strip wrappers to their `value`, harvest `range`s, and validate them.
- Better: make **constructing the `Range` schema the test** — feed it what the YAML gives and if a
  valid `Range` can't be built (bad `lo`/`hi`, unknown `dist`), that *is* the error. Same for the
  whole config: "doesn't fit schema → error", via typed construction, not bespoke checks.
- Consider packaging ranges in the YAML so they're self-identifying (a `range`-keyed dict, or a
  list form) instead of `_unwrap` inferring "is this a leaf or a wrapper?" retroactively.
- Consider a structuring/validation lib (pydantic / cattrs / dacite) to turn the parsed dict
  straight into the typed config with validation — see boilerplate note below.

### Validation timing (open question)

The only validation here is "is any leaf a malformed range?". Since ranges are a single input type,
maybe validate them **once, later**, when the Study is built (studies used to carry the same range
input type). Need to confirm there's no reason it must happen at assumptions-load. *(See the one
real constraint below — this is where it bites.)*

### Boilerplate

`return schema.Sthsth(**d["sthsth"])` repeated per block is exactly the boilerplate dataclasses were
supposed to spare us. Plain dataclasses don't build themselves from nested dicts — but a structuring
lib (dacite `from_dict` / cattrs `structure` / pydantic) does, recursively, **with** validation. That
would delete most of `_platform`/`_drivetrain`/`_source`/`_economics`/`_margins` *and* fold the range
checks into schema construction.

### Abstract vs domain types

`config`/`schema` mix **abstract pipeline concepts** (`Study`, `Case`, `Range`, `Axis`) with
**real-world domain types** (`Platform`, `Drivetrain`, `EnergySource`/`Fuel`, ...). Separate them:
`Study`, `Case`, `Range` should live in **config** (the pipeline's own vocabulary); the domain
schema is its own thing. And **`Axis` is probably unnecessary** — revisit whether sweep/optimize
grids need a dedicated type at all.

### The one real constraint (what NOT to lose in the simplification)

A numeric leaf is polymorphic across the pipeline's life: it's a **scalar or `Range`** at
config-build time, but becomes an **`xr.DataArray`** at evaluation time (compose places the Saltelli
columns / sweep grids on it). The *current* loose-dict design exists precisely so `compose` can
swap an array into any leaf by dotted path and rebuild — a strict typed/frozen config with `float`
fields would reject the array.

So "validate strictly against the schema" cleanly applies to the **raw scalar+range config at build
time**, not to the post-placement config. The target separation (build a typed config-with-Ranges
in `_build_study`, then vectorize/sample as a *later* step) is the right instinct and can dissolve
this — but the **array-placement mechanism is the piece to design carefully** so it doesn't fight
frozen typed objects. This polymorphic leaf is the actual hard part; everything else here
(`_unwrap` nesting, `schema.X(**d)` boilerplate, split validation) really can collapse.

Note: ranges *currently* live only in `assumptions.yaml` (a study picks *which* paths to sample, not
their widths — `apply_schema` rejects `ranges:` in a study body). See the decided direction below,
which reverses that.

### Decided direction: override and role are two orthogonal things

**Key correction (I had this conflated):** overriding a param and giving it a role are
*fundamentally different operations*, written near each other in `studies.yaml` only for
convenience.

- **Override** = editing the param's *data* (`value` / `range` / `dist`), identical in kind to
  editing `assumptions.yaml`. Lands in the **config tree**. Reverse the current "studies can't
  declare ranges" rule and use the **same param syntax in both YAMLs** — fat-fingering a study
  override is the exact same validation problem as editing assumptions, so validation is genuinely
  shared: assumptions ⊕ study-overrides → one tree → validate once → build.
- **Role** = "how do I want to vary this?" — `sample` / `sweep` / `optimize`, default **`fixed`**.
  This is **meta**, a small list in the `Study` (path → kind, carrying `n`), pointing *into* the
  tree by path. Roles are **not** baked into the tree; the config tree stays pure parameter data
  (values, ranges, dists, a few strings).

Per param the user independently: (1) writes the path to hit it, then **AND/OR** (2) gives it a
variation role, (3) overrides its value/range/dist.

Two things fall out:

- **`Axis` dissolves.** A varied param's *bounds* already live in its `range` (tree); the role only
  adds `n` and how to consume it (sample = Saltelli draw; sweep/optimize = linspace of `n`). No need
  for a separate `(path, lo, hi, n, method)` type — bounds from the tree, `n`/kind from the role
  meta.
- **`fix` was never a role.** Overriding a value is a tree edit; "fixed" is just the *absence* of a
  variation role. So roles = `{sample, sweep, optimize}` only, and today's `Study.fix` conflates
  "override this value" with "this param doesn't vary" — split them.

### Drop `frozen=True` on the dataclasses

No reason to freeze them. Nothing downstream (the mechanical sampling / sweeping / optimize-collapse
code) needs defending against — we validate at the boundary, then do what we need with the objects.
Bonus: mutable config objects let `compose` place array leaves **in place** instead of the
deepcopy-dict-then-rebuild dance, which also softens "the one real constraint" above (validate the
scalar/range config once at build, then mutate arrays in for eval — with a structuring lib that
validates at build only, not on assignment).
