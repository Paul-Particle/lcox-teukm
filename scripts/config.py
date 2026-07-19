"""
config.py — load the two YAMLs and build the Study objects the pipeline runs.

`get_studies(assumptions_path, studies_path)` validates `assumptions.yaml` into a typed `Library`
*once*, then builds one `Study` per entry in `studies.yaml`. For each study it deep-copies the
library, overlays that study's parameter overrides onto the copy, resolves its member cases against
the copy, and collects its probes (which parameters vary, and how).

A `studies.yaml` param entry has two independent parts, written together for convenience (schema
`ParamInput`): `range:` is a data override (value / lo / hi / dist), merged onto the assumptions
`Range` at that dotted path; `probe:` says how to vary it (kind + grid `n`). A bare scalar is
shorthand for `range: {value: x}`.

Overrides reach their leaf with `_leaf`, which walks a dotted path across the library the way the
library is actually shaped: dict-key through a catalog map (`platforms` / `drivetrains` /
`sources`), getattr through an object everywhere else. No component is referenced by string except
where the data genuinely is a map — a case naming its parts, or a path naming a catalog entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import yaml

import schema


@dataclass
class Probe:
    """How a study probes one parameter: the dotted config path, the kind, and the grid size.
    The bounds/distribution come from the `Range` at `path` in the study's library (sampling
    ignores `n`)."""
    path: str
    kind: schema.ProbeKind
    n: int | None = None


@dataclass
class Case:
    """One ship concept: the composed library components plus the shared block, ready for a
    strategy to read. The components point into the study's `Library`, so a value placed on the
    library is seen by every case that uses that component."""
    name: str
    platform: schema.Platform
    drivetrain: schema.Drivetrain
    sources: list[schema.EnergySource]
    strategy: str
    shared: schema.Shared


@dataclass
class Study:
    name: str
    library: schema.Library             # this study's assumptions, overrides applied; probes resolve here
    cases: list[Case]                   # member cases, pointing into `library`
    probes: list[Probe]
    optimize_by: str                    # measure the lever collapse optimizes
    minimize: bool                      # argmin (True) vs argmax (False) of optimize_by
    decompose: tuple[str, ...]          # Sobol target measures; defaults to (optimize_by,)
    saltelli_sample_n: int              # Saltelli base-N for sampled probes
    second_order: bool
    infeasible_value: float | None      # objective penalty for infeasible samples (else skip the slice)


def load_yaml(path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def get_studies(assumptions_path, studies_path) -> list[Study]:
    """Build every study in `studies.yaml` against `assumptions.yaml`. The library is validated once
    and deep-copied per study, so a study's overrides never leak into another."""
    library = schema.Library.model_validate(load_yaml(assumptions_path))
    studies_input = schema.StudiesInput.model_validate(load_yaml(studies_path))
    return [
        build_study(name, study_input, library, studies_input.cases)
        for name, study_input in studies_input.studies.items()
    ]


def build_study(name: str, study_input: schema.StudyInput, library: schema.Library,
                case_defs: dict[str, schema.CaseInput]) -> Study:
    """Overlay this study's overrides onto a copy of the library, resolve its member cases against
    that copy, and collect its probes."""
    study_library = library.model_copy(deep=True)
    overrides, probes = _split_params(study_input.params)
    for path, override in overrides.items():
        _apply_override(study_library, path, override)
    for probe in probes:
        _leaf(study_library, probe.path)                # a probe must name a real config leaf
    cases = [_build_case(case_name, case_defs, study_library) for case_name in study_input.cases]

    return Study(
        name=name,
        library=study_library,
        cases=cases,
        probes=probes,
        optimize_by=study_input.optimize_by,
        minimize=study_input.minimize,
        decompose=tuple(study_input.decompose) or (study_input.optimize_by,),
        saltelli_sample_n=study_input.saltelli_sample_n,
        second_order=study_input.second_order,
        infeasible_value=study_input.infeasible_value,
    )


def _split_params(params: dict[str, schema.ParamInput]) -> tuple[dict[str, schema.RangeInput], list[Probe]]:
    """Split each `params:` entry into its data override (`range:`) and its `Probe` (`probe:`).
    An entry may carry either, both, or (validation aside) neither — they are independent."""
    overrides: dict[str, schema.RangeInput] = {}
    probes: list[Probe] = []
    for path, entry in params.items():
        if entry.range is not None:
            overrides[path] = entry.range
        if entry.probe is not None:
            probes.append(Probe(path=path, kind=entry.probe.kind, n=entry.probe.n))
    return overrides, probes


def _apply_override(library: schema.Library, path: str, override: schema.RangeInput) -> None:
    """Merge a `range:` override onto the `Range` leaf at dotted `path`, in place. Unset override
    fields are inherited from the existing leaf (so overriding only `value` keeps the band, and
    overriding only the band keeps the nominal)."""
    parent, leaf = _leaf(library, path)
    existing = _get(parent, leaf, path)
    if not isinstance(existing, schema.Range):
        raise ValueError(f"path {path!r} is not a numeric leaf (it's a {type(existing).__name__}); "
                         "only a Range leaf takes a range override")
    merged = {**existing.model_dump(), **override.model_dump(exclude_none=True)}
    _set(parent, leaf, schema.Range.model_validate(merged))


def _build_case(name: str, case_defs: dict[str, schema.CaseInput], library: schema.Library) -> Case:
    """Resolve one case's composition (library keys) into the actual components on `library`."""
    if name not in case_defs:
        raise ValueError(f"unknown case {name!r}; known: {list(case_defs)}")
    spec = case_defs[name]
    return Case(
        name=name,
        platform=_catalog_get(library.platforms, spec.platform, "platform", name),
        drivetrain=_catalog_get(library.drivetrains, spec.drivetrain, "drivetrain", name),
        sources=[_catalog_get(library.sources, s, "source", name) for s in spec.sources],
        strategy=spec.strategy,
        shared=library.shared,
    )


# ---------------------------------------------------- path navigation ----
# The library is an object tree with a single map layer — the catalogs (`platforms` /
# `drivetrains` / `sources`). `_leaf` walks a dotted path across it, dict-keying a map and
# getattr-ing an object, so no glom-style machinery is needed.

def _leaf(root, path: str) -> tuple[object, str]:
    """Return `(parent, leaf_name)` for a dotted `path`, raising if any segment — including the
    leaf — is missing. Shared by overrides (to set) and probes (to validate the path)."""
    *parents, leaf = path.split(".")
    node = root
    for segment in parents:
        node = _get(node, segment, path)
    _get(node, leaf, path)                              # the leaf must exist too
    return node, leaf


def _get(node, key: str, path: str):
    """One step of navigation: a map is dict-keyed, an object is getattr-ed."""
    try:
        return node[key] if isinstance(node, Mapping) else getattr(node, key)
    except (KeyError, AttributeError):
        raise ValueError(f"path {path!r}: no {key!r} here — "
                         "check the dotted path against assumptions.yaml") from None


def _set(node, key: str, value) -> None:
    if isinstance(node, Mapping):
        node[key] = value
    else:
        setattr(node, key, value)


def _catalog_get(catalog: dict, key: str, kind: str, case_name: str):
    """Fetch a component from its catalog by name, with a message that names the case and the
    known keys when the name is a typo."""
    try:
        return catalog[key]
    except KeyError:
        raise ValueError(f"case {case_name!r}: unknown {kind} {key!r}; "
                         f"known: {list(catalog)}") from None
