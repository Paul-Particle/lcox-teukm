"""
config.py — load the two YAMLs and build the Study objects the pipeline runs.

`get_studies(assumptions_path, studies_path)` returns one `Study` per entry in `studies.yaml`.
For each study it overlays that study's parameter overrides onto the assumptions tree, validates
the merged tree into a typed `Library` (schema), builds the member `Case`s from it, and collects
the `Probe`s (which parameters vary, and how).

A `studies.yaml` param entry has two independent parts, written together for convenience:
`range:` is a data override (value / lo / hi / dist), deep-merged onto the assumptions leaf;
`probe:` says how to vary it (kind + grid `n`). A bare scalar is shorthand for `range: {value: x}`.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy

import yaml
from pydantic import BaseModel, ConfigDict

import schema

from typing import Literal

ProbeKind = Literal["sample", "sweep", "optimize"]      # fixed = no probe


class Probe(BaseModel):
    """How a study probes one parameter: the dotted config path, the kind, and the grid size.
    The bounds/distribution come from the `Range` at `path` (sampling ignores `n`)."""
    model_config = ConfigDict(extra="forbid")
    path: str
    kind: ProbeKind
    n: int | None = None


@dataclass
class Case:
    """One ship concept: the composed library components plus the shared block, ready for a
    strategy to read. Fields follow the assumptions.yaml case order."""
    name: str
    platform: schema.Platform
    drivetrain: schema.Drivetrain
    sources: list[schema.EnergySource]
    strategy: str
    shared: schema.Shared


@dataclass
class Study:
    name: str
    cases: list[Case]
    probes: list[Probe]
    optimize_by: str                    # measure the lever collapse optimizes
    minimize: bool                      # argmin (True) vs argmax (False) of optimize_by
    decompose: tuple[str, ...]          # Sobol target measures; () -> default to (optimize_by,)
    saltelli_sample_n: int              # Saltelli base-N for sampled probes
    second_order: bool
    infeasible_value: float | None      # objective penalty for infeasible samples (else skip the slice)


def load_yaml(path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def get_studies(assumptions_path, studies_path) -> list[Study]:
    """Build every study in `studies.yaml` against `assumptions.yaml`."""
    assumptions = load_yaml(assumptions_path)
    studies_file = load_yaml(studies_path)
    case_defs = studies_file["cases"]
    studies = [
        build_study(name, body, assumptions, case_defs)
        for name, body in studies_file["studies"].items()
    ]
    return studies


def build_study(name: str, body: dict, assumptions: dict, case_defs: dict) -> Study:
    """Overlay this study's overrides onto the assumptions, validate the merged tree into a typed
    Library, build its member cases, and collect its probes."""
    overrides, probes = _split_params(body.get("params", {}))
    merged = _overlay(assumptions, overrides)
    for probe in probes:
        _descend(merged, probe.path)        # a probe must name a real config leaf
    library = schema.Library.model_validate(merged)
    case_names = body.get("cases") or list(case_defs)
    unknown = [n for n in case_names if n not in case_defs]
    if unknown:
        raise ValueError(f"study {name!r}: unknown case(s) {unknown}; known: {list(case_defs)}")
    cases = [_build_case(n, case_defs[n], library) for n in case_names]

    decompose = body.get("decompose", ())
    decompose = (decompose,) if isinstance(decompose, str) else tuple(decompose)
    return Study(
        name=name,
        cases=cases,
        probes=probes,
        optimize_by=body.get("optimize_by", "lcot"),
        minimize=bool(body.get("minimize", True)),
        decompose=decompose,
        saltelli_sample_n=int(body.get("saltelli_sample_n", 1024)),
        second_order=bool(body.get("second_order", False)),
        infeasible_value=(float(body["infeasible_value"])
                          if body.get("infeasible_value") is not None else None),
    )


def _split_params(params: dict) -> tuple[dict, list[Probe]]:
    """Split each `studies.yaml` param entry into its data override (the `range:` block, deep-merged
    later) and its `Probe` (the `probe:` block). A bare scalar is `range: {value: scalar}`."""
    overrides: dict[str, dict] = {}
    probes: list[Probe] = []
    for path, entry in params.items():
        if not isinstance(entry, dict):
            overrides[path] = {"value": float(entry)}
            continue
        extra = set(entry) - {"probe", "range"}
        if extra:
            raise ValueError(f"param {path!r}: unexpected keys {sorted(extra)}; "
                             "a param entry is {probe: {...}, range: {...}}")
        if "range" in entry:
            overrides[path] = entry["range"]
        if "probe" in entry:
            probes.append(Probe(path=path, **entry["probe"]))
    return overrides, probes


def _overlay(assumptions: dict, overrides: dict) -> dict:
    """Deep-merge each override onto the assumptions leaf it names, on a copy."""
    merged = copy.deepcopy(assumptions)
    for path, override in overrides.items():
        _merge_leaf(merged, path, override)
    return merged


def _merge_leaf(tree: dict, path: str, override: dict) -> None:
    """Merge `override` onto the leaf at dotted `path`. A scalar leaf becomes `{value: scalar}`
    first, so an override may add a band to a plain nominal."""
    node, leaf = _descend(tree, path)
    current = node[leaf]
    base = current if isinstance(current, dict) else {"value": current}
    node[leaf] = {**base, **override}


def _descend(tree: dict, path: str) -> tuple[dict, str]:
    """Return `(parent_dict, leaf_key)` for a dotted `path`, raising if any segment — including the
    leaf — is missing. The shared path check for overrides and probes."""
    node = tree
    *parents, leaf = path.split(".")
    for segment in parents:
        if not isinstance(node, dict) or segment not in node:
            raise ValueError(f"path {path!r} is not a config leaf — "
                             "check the dotted path against assumptions.yaml")
        node = node[segment]
    if not isinstance(node, dict) or leaf not in node:
        raise ValueError(f"path {path!r} is not a config leaf — "
                         "check the dotted path against assumptions.yaml")
    return node, leaf


def _build_case(name: str, spec: dict, library: schema.Library) -> Case:
    """Compose one Case from its `cases:` entry against the built library."""
    return Case(
        name=name,
        platform=library.platforms[spec["platform"]],
        drivetrain=library.drivetrains[spec["drivetrain"]],
        sources=[library.sources[s] for s in spec.get("sources", [])],
        strategy=spec["strategy"],
        shared=library.shared,
    )
