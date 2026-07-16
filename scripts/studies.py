"""
studies.py — parse studies.yaml and resolve each study's role assignments against the ranges.

A study is one line of role assignment over the parameters config.yaml already describes. Roles:

- **sample** — jointly Saltelli-drawn on the shared sample axis (variance-decomposed). Paths or
  globs over config leaves (a source capex `sources.lfp.capex.usd_per_kwh`, a tether field
  `sources.tender-reactor.tether.detach_frac` — every param is an ordinary config leaf now);
  omitted means *every ranged config param* (the blast default), `[]` means none. Each sampled
  path resolves a
  `Range`: the range harvested from config.yaml (declared on the value), else — for a leaf with a
  value but no range — a default +/-20% perturbation for screening. Ranges live in config, never
  in a study.
- **optimize** / **sweep** — factorial axis grids the STUDY owns, written `{path: [lo, hi, n]}`
  over the SAME config leaves `sample`/`fix` address (lever argmin-collapsed / condition retained,
  one Sobol analysis per swept slice). Omitted -> none. Any leaf works — `shared.op_v_kn` as the
  usual lever, `shared.d_km` the usual sweep, but `shared.design_v_kn` or a source field just as well.
- **fix** — pin a config leaf (a source or route field) to a constant for this run.
- **objective** — the measure to optimize and decompose (default `lcot`).

Resolution is structural and loud (a glob matching no config leaf, a sampled non-leaf, a study
that still declares `ranges:`, all error); *which* params are interesting is the reader's
judgment, expressed as the study.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

import yaml

import schema

DEFAULT_PERTURB = 0.20      # +/-fraction for a sampled config leaf with a value but no range


@dataclass(frozen=True)
class Study:
    name: str
    sample: dict[str, schema.Range]     # resolved: path -> Range (globs expanded, ranges chosen)
    fix: dict[str, float]               # config path -> constant override (source or route field)
    optimize: tuple[schema.Axis, ...]   # study-owned lever grids ((): none)
    sweep: tuple[schema.Axis, ...]      # study-owned condition grids ((): none)
    objective: str
    n: int
    second_order: bool
    cases: tuple[str, ...] | None       # None -> every case
    infeasible_value: float | None      # objective penalty for infeasible samples (else skip that slice)


def load_studies(studies_path, ranges: dict[str, schema.Range],
                 raw_config: dict) -> dict[str, Study]:
    """Parse studies.yaml and resolve every study against the harvested `ranges` and the config
    leaf values (needed for the +/-20% default and to validate paths)."""
    with open(studies_path) as f:
        spec = yaml.safe_load(f)["studies"]
    leaves = _flatten(raw_config)
    return {name: _resolve(name, body or {}, ranges, leaves)
            for name, body in spec.items()}


def _resolve(name, body: dict, ranges: dict[str, schema.Range],
             leaves: dict[str, float]) -> Study:
    if "ranges" in body:
        raise ValueError(f"study {name!r} declares `ranges:` — parameter ranges now live in "
                         "config.yaml on the value ({value:, range: [lo, hi]}); a study only "
                         "chooses which params to sample, not how wide they are")
    return Study(
        name=name,
        sample=_resolve_sample(name, body.get("sample"), ranges, leaves),
        fix={path: float(value) for path, value in body.get("fix", {}).items()},
        optimize=_axes(body.get("optimize")),
        sweep=_axes(body.get("sweep")),
        objective=body.get("objective", "lcot"),
        n=int(body.get("n", 1024)),
        second_order=bool(body.get("second_order", False)),
        cases=tuple(body["cases"]) if body.get("cases") is not None else None,
        infeasible_value=(float(body["infeasible_value"])
                          if body.get("infeasible_value") is not None else None),
    )


def _resolve_sample(name, entries, ranges, leaves) -> dict[str, schema.Range]:
    """Expand the `sample` list to a path -> Range map. Omitted -> every harvested config range
    (blast default); `[]` -> none."""
    if entries is None:
        return dict(ranges)             # blast default: every ranged config param
    resolved: dict[str, schema.Range] = {}
    for entry in entries:
        if "*" in entry:
            matches = fnmatch.filter(leaves, entry)
            if not matches:
                raise ValueError(f"study {name!r}: sample glob {entry!r} matched no config leaf")
        else:
            matches = [entry]
        for path in matches:
            resolved[path] = _range_for(name, path, ranges, leaves)
    return resolved


def _range_for(name, path, ranges, leaves) -> schema.Range:
    """The Range for a sampled path: config-harvested (declared on the value), else +/-20% of the
    config nominal. A path that is no config leaf at all errors."""
    if path in ranges:
        return ranges[path]
    if path in leaves:
        nominal = leaves[path]
        return schema.Range(nominal * (1 - DEFAULT_PERTURB), nominal * (1 + DEFAULT_PERTURB))
    raise ValueError(f"study {name!r}: sampled path {path!r} is not a config leaf — check the "
                     "dotted path against config.yaml")


def _axes(spec) -> tuple[schema.Axis, ...]:
    """Parse an optimize/sweep block `{path: [lo, hi, n]}` into `Axis` grids ((): omitted/none).
    `path` is the dotted config leaf the grid replaces (`shared.op_v_kn`, `shared.d_km`, or any
    other leaf) — the same addressing `sample`/`fix` use; a bad path surfaces as a KeyError when
    `design` places it."""
    if not spec:
        return ()
    return tuple(schema.Axis(path, float(lo), float(hi), int(n))
                 for path, (lo, hi, n) in spec.items())


def _flatten(node, prefix: str = "") -> dict[str, float]:
    """Every numeric scalar leaf of the config, keyed by dotted path (strings/lists ignored)."""
    out: dict[str, float] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        out[prefix] = float(node)
    return out
