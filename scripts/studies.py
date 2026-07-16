"""
studies.py — parse studies.yaml and resolve each study's role assignments against the ranges.

A study is one line of role assignment over the parameters config.yaml already describes. Roles:

- **sample** — jointly Saltelli-drawn on the shared sample axis (variance-decomposed). Paths or
  globs over config leaves; omitted means *every ranged config param* (the blast default). Each
  sampled path resolves a `Range`: the study's own `ranges:` block wins, else the range harvested
  from config.yaml, else — for a config leaf with a value but no range — a default +/-20%
  perturbation for screening. A route param (`params.route.*`) has no config nominal (its value is
  per-case in cases.csv), so sampling one *requires* an explicit study range.
- **optimize** / **sweep** — factorial axes reusing the member case's cases.csv grid for the named
  param (lever argmin-collapsed / condition retained). Omitted -> the case's own axes (the seed
  artifact's op_v_kn optimize + d_km sweep); `[]` -> none.
- **fix** — pin a leaf (config or route) to a constant for this run.
- **objective** — the measure to optimize and decompose (default `lcot`).

Resolution is structural and loud (a glob matching no config leaf, a route sample with no range,
both error); *which* params are interesting is the reader's judgment, expressed as the study.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

import yaml

import schema

ROUTE_PREFIX = "params.route."
DEFAULT_PERTURB = 0.20      # +/-fraction for a sampled config leaf with a value but no range


@dataclass(frozen=True)
class Study:
    name: str
    sample: dict[str, schema.Range]     # resolved: path -> Range (globs expanded, ranges chosen)
    fix: dict[str, float]               # path -> constant override (config or route)
    optimize: tuple[str, ...] | None    # route param names; None -> the case's own axes; () -> none
    sweep: tuple[str, ...] | None
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
    declared = {path: schema.Range(float(lo), float(hi))
                for path, (lo, hi) in body.get("ranges", {}).items()}
    return Study(
        name=name,
        sample=_resolve_sample(name, body.get("sample"), declared, ranges, leaves),
        fix={path: float(value) for path, value in body.get("fix", {}).items()},
        optimize=_axis_names(body.get("optimize")),
        sweep=_axis_names(body.get("sweep")),
        objective=body.get("objective", "lcot"),
        n=int(body.get("n", 1024)),
        second_order=bool(body.get("second_order", False)),
        cases=tuple(body["cases"]) if body.get("cases") is not None else None,
        infeasible_value=(float(body["infeasible_value"])
                          if body.get("infeasible_value") is not None else None),
    )


def _resolve_sample(name, entries, declared, ranges, leaves) -> dict[str, schema.Range]:
    """Expand the `sample` list to a path -> Range map. Omitted -> every harvested config range."""
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
            resolved[path] = _range_for(name, path, declared, ranges, leaves)
    return resolved


def _range_for(name, path, declared, ranges, leaves) -> schema.Range:
    """The Range for a sampled path: study-declared, else config-harvested, else +/-20% of the
    config nominal. A route path with none of these errors (its nominal is per-case)."""
    if path in declared:
        return declared[path]
    if path in ranges:
        return ranges[path]
    if path in leaves:
        nominal = leaves[path]
        return schema.Range(nominal * (1 - DEFAULT_PERTURB), nominal * (1 + DEFAULT_PERTURB))
    raise ValueError(f"study {name!r}: sampled path {path!r} has no range — declare one under "
                     f"the study's `ranges:` (route/case params carry no config nominal to perturb)")


def _axis_names(entries) -> tuple[str, ...] | None:
    """Normalize an optimize/sweep list to bare param names (stripping the `params.route.`
    prefix). None (omitted) is preserved to mean 'use the case's own axes'."""
    if entries is None:
        return None
    return tuple(e[len(ROUTE_PREFIX):] if e.startswith(ROUTE_PREFIX) else e for e in entries)


def _flatten(node, prefix: str = "") -> dict[str, float]:
    """Every numeric scalar leaf of the config, keyed by dotted path (strings/lists ignored)."""
    out: dict[str, float] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        out[prefix] = float(node)
    return out
