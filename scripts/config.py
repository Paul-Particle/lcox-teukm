"""
config.py — load the two YAMLs and build the Study objects the pipeline runs.

`get_studies(...)` validates assumptions.yaml into a typed `Library` once, then builds one `Study`
per entry in studies.yaml. For each study it deep-copies the library, overlays that study's data
overrides onto the copy, and resolves its member cases against the copy.

`Study`/`Case`/`Probe` are the pipeline's own vocabulary and live here; the data/validation
vocabulary — the `*Input` models, `Range`, `ProbeKind`, the domain schema — lives in schema.py. A
studies.yaml param entry (`ParamInput`) has two independent parts: `range:` overrides the leaf's
data, merged onto the existing `Range` so unset fields are inherited; `probe:` says how to vary it.
`_overlay` reaches each leaf with `_walk`, which crosses the library the way it is shaped — dict-key
through the catalog map, getattr through an object.
"""

from __future__ import annotations

from collections.abc import Mapping

import yaml
from pydantic import BaseModel, ConfigDict

from schema import (CaseInput, Drivetrain, EnergySource, Library, ParamInput, Platform,
                    ProbeKind, Range, Shared, StudiesInput, StudyInput)


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a typo in construction is an error, not a silent field


class Case(Node):
    """One ship concept: the composed library components plus the shared block, ready for a
    strategy. The components point into the study's library, so a value placed on a component is
    seen by every case that uses it."""
    name: str
    platform: Platform
    drivetrain: Drivetrain
    sources: list[EnergySource]
    strategy: str
    shared: Shared


class Probe(Node):
    """How a study probes one parameter: the dotted path, the kind, and the grid size. Bounds and
    distribution come from the `Range` at `path` (sampling ignores `n`)."""
    path: str
    kind: ProbeKind
    n: int | None = None


class Study(Node):
    """A study, built: its member cases, its probes (which parameters vary, and how), and the meta
    carried straight over from `StudyInput`."""
    name: str
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
    """Build every study in studies.yaml against assumptions.yaml. The assumptions library is
    validated once and deep-copied per study, so one study's overrides never leak into another."""
    assumptions_library = Library.model_validate(load_yaml(assumptions_path))
    studies_input = StudiesInput.model_validate(load_yaml(studies_path))
    return [
        build_study(name, study_input, assumptions_library, studies_input.cases)
        for name, study_input in studies_input.studies.items()
    ]


def build_study(name: str, study_input: StudyInput, assumptions_library: Library,
                case_defs: dict[str, CaseInput]) -> Study:
    """Overlay this study's overrides onto a copy of the library, resolve its cases against that
    copy, and carry its probes + meta."""
    library = assumptions_library.model_copy(deep=True)
    _overlay(library, study_input.params)
    probes = [Probe(path=path, kind=entry.probe.kind, n=entry.probe.n)
              for path, entry in study_input.params.items() if entry.probe is not None]
    cases = [build_case(case_name, case_defs, library) for case_name in study_input.cases]

    meta = study_input.model_dump(exclude={"cases", "params"})
    meta["decompose"] = meta["decompose"] or [study_input.optimize_by]
    return Study(name=name, cases=cases, probes=probes, **meta)


def build_case(name: str, case_defs: dict[str, CaseInput], library: Library) -> Case:
    """Resolve one case's composition (library keys) into the actual components on `library`."""
    spec = case_defs[name]
    return Case(
        name=name,
        platform=library.platforms[spec.platform],
        drivetrain=library.drivetrains[spec.drivetrain],
        sources=[library.sources[source] for source in spec.sources],
        strategy=spec.strategy,
        shared=library.shared,
    )


def _overlay(library: Library, params: dict[str, ParamInput]) -> None:
    """Merge each param's `range:` override onto its `Range` leaf, in place. Unset override fields
    are inherited from the existing leaf, so a study never has to supply a value it doesn't mean to
    set. Params with only a probe are left untouched."""
    for path, entry in params.items():
        if entry.range is None:
            continue
        parent, leaf = _walk(library, path)
        existing = getattr(parent, leaf)
        if not isinstance(existing, Range):
            raise ValueError(f"path {path!r} is not a numeric leaf (it's a {type(existing).__name__}); "
                             "only a Range leaf takes a range override")
        setattr(parent, leaf, Range.model_validate(
            {**existing.model_dump(), **entry.range.model_dump(exclude_none=True)}))


def _walk(root, path: str) -> tuple[object, str]:
    """Return `(parent, leaf)` for a dotted `path`, crossing the catalog map by key and an object by
    attribute — the single navigator the overlay uses. A wrong segment raises the underlying
    KeyError/AttributeError, which names it."""
    *parents, leaf = path.split(".")
    node = root
    for segment in parents:
        node = node[segment] if isinstance(node, Mapping) else getattr(node, segment)
    return node, leaf
