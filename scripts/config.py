"""
config.py — load the two YAMLs and build the Study objects the pipeline runs.

`get_studies(...)` validates assumptions.yaml into a typed `Library` once, then builds one `Study`
per entry in studies.yaml. For each study it deep-copies the library, applies that study's data
overrides onto the copy, resolves its member cases against the copy, and collects its probes.

The runtime objects (`Study`, `Case`, `Probe`) are the pipeline's own vocabulary and live here; the
data/validation vocabulary — the `*Input` models, `Range`, `ProbeKind`, the domain schema — lives
in schema.py. A studies.yaml param entry (schema `ParamInput`) has two independent parts: `range:`
overrides the leaf's data, merged onto the existing `Range` so unset fields are inherited; `probe:`
says how to vary it. Overrides and probe paths reach their leaf with `_walk`, which crosses the
library the way it is shaped — dict-key through a catalog map, getattr through an object.
"""

from __future__ import annotations

from collections.abc import Mapping

import yaml

import schema


class Probe(schema.Node):
    """How a study probes one parameter: the dotted path, the kind, and the grid size. Bounds and
    distribution come from the `Range` at `path` (sampling ignores `n`)."""
    path: str
    kind: schema.ProbeKind
    n: int | None = None


class Case(schema.Node):
    """One ship concept: the composed library components plus the shared block, ready for a
    strategy. The components point into the study's `Library`, so a value placed on the library is
    seen by every case that uses that component."""
    name: str
    platform: schema.Platform
    drivetrain: schema.Drivetrain
    sources: list[schema.EnergySource]
    strategy: str
    shared: schema.Shared


class Study(schema.Node):
    """A study, built: its overlaid library, resolved member cases, probes, and the meta carried
    straight over from `StudyInput`."""
    name: str
    library: schema.Library             # this study's assumptions, overrides applied; probes resolve here
    cases: list[Case]
    probes: list[Probe]
    optimize_by: str
    minimize: bool
    decompose: list[str]
    saltelli_sample_n: int
    second_order: bool
    infeasible_value: float | None


def load_yaml(path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def get_studies(assumptions_path, studies_path) -> list[Study]:
    """Build every study in studies.yaml against assumptions.yaml. The library is validated once
    and deep-copied per study, so one study's overrides never leak into another."""
    library = schema.Library.model_validate(load_yaml(assumptions_path))
    studies_input = schema.StudiesInput.model_validate(load_yaml(studies_path))
    return [
        build_study(name, study_input, library, studies_input.cases)
        for name, study_input in studies_input.studies.items()
    ]


def build_study(name: str, study_input: schema.StudyInput, library: schema.Library,
                case_defs: dict[str, schema.CaseInput]) -> Study:
    """Overlay this study's overrides onto a copy of the library, resolve its cases against that
    copy, and collect its probes — the assembly line, one visible step at a time."""
    lib = library.model_copy(deep=True)
    probes: list[Probe] = []
    for path, entry in study_input.params.items():
        if entry.range is not None:
            _apply_override(lib, path, entry.range)
        if entry.probe is not None:
            _walk(lib, path)                                    # a probe must name a real leaf
            probes.append(Probe(path=path, kind=entry.probe.kind, n=entry.probe.n))
    cases = [build_case(case_name, case_defs, lib) for case_name in study_input.cases]

    meta = study_input.model_dump(exclude={"cases", "params"})
    meta["decompose"] = meta["decompose"] or [study_input.optimize_by]
    return Study(name=name, library=lib, cases=cases, probes=probes, **meta)


def build_case(name: str, case_defs: dict[str, schema.CaseInput], library: schema.Library) -> Case:
    """Resolve one case's composition (library keys) into the actual components on `library`."""
    spec = _child(case_defs, name, "case")
    return Case(
        name=name,
        platform=_child(library.platforms, spec.platform, f"case {name!r} platform"),
        drivetrain=_child(library.drivetrains, spec.drivetrain, f"case {name!r} drivetrain"),
        sources=[_child(library.sources, source, f"case {name!r} source") for source in spec.sources],
        strategy=spec.strategy,
        shared=library.shared,
    )


def _apply_override(library: schema.Library, path: str, override: schema.RangeInput) -> None:
    """Merge a `range:` override onto the `Range` leaf at `path`, in place. Unset override fields
    are inherited from the existing leaf — overriding only the value keeps the band and vice versa,
    so a study never has to supply a value it doesn't mean to set."""
    parent, leaf = _walk(library, path)
    existing = getattr(parent, leaf)
    if not isinstance(existing, schema.Range):
        raise ValueError(f"path {path!r} is not a numeric leaf (it's a {type(existing).__name__}); "
                         "only a Range leaf takes a range override")
    merged = {**existing.model_dump(), **override.model_dump(exclude_none=True)}
    setattr(parent, leaf, schema.Range.model_validate(merged))


# ---------------------------------------------------- path navigation ----
# The library is an object tree with a single map layer — the catalogs (`platforms` /
# `drivetrains` / `sources`). `_walk` crosses it, dict-keying a map and getattr-ing an object,
# so no glom-style machinery is needed and the catalogs stay plain maps.

def _walk(root, path: str) -> tuple[object, str]:
    """Return `(parent, leaf)` for a dotted `path`, raising a clear error if any segment —
    including the leaf — is missing. Shared by overrides (to set) and probes (to validate)."""
    *parents, leaf = path.split(".")
    node = root
    for segment in parents:
        node = _child(node, segment, f"path {path!r}")
    _child(node, leaf, f"path {path!r}")                        # the leaf must resolve too
    return node, leaf


def _child(node, key: str, context: str):
    """One navigation step — dict-key a map, getattr an object — with a message that lists the
    known keys when `key` is a typo."""
    try:
        return node[key] if isinstance(node, Mapping) else getattr(node, key)
    except (KeyError, AttributeError):
        known = list(node) if isinstance(node, Mapping) else list(type(node).model_fields)
        raise ValueError(f"{context}: unknown {key!r}; known: {known}") from None
