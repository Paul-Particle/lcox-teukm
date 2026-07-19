"""
config.py — load the two YAMLs and build the Study objects the pipeline runs.

Two vocabularies live here, both plain `Config` models (reject unknown keys; no range peeling —
ranges live only on the assumptions leaves in schema.py):

- the **studies.yaml input schema** (`StudiesInput` + friends): pydantic models mirroring that file,
  so one `StudiesInput.model_validate(...)` validates the whole thing (the case catalog + the
  studies);
- the **built pipeline objects** (`Study`/`Case`/`Probe`): what the rest of the pipeline runs on.

`get_studies(...)` validates assumptions.yaml into a typed `Library` once (leaves are plain floats;
each leaf's optional sampling range was peeled onto its model's `ranges`), then builds one `Study`
per entry in studies.yaml. For each study it deep-copies the library, applies that study's value
overrides onto the copy, harvests its probes, and resolves its member cases against the copy. A
probe's range comes from the study's `range` override if given, else the default range that sits
beside the value in assumptions.yaml — read straight off the leaf's model (`_default_range`), no
second pass over the raw file. `_walk` crosses the library the way it is shaped — dict-key through
the catalog maps, getattr through an object.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from schema import Distribution, Drivetrain, EnergySource, Library, Platform, Range, Shared

ProbeKind = Literal["sample", "sweep", "optimize"]      # how a study varies a parameter (fixed = none)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a typo in construction/input is an error, not a silent field


# ============================================= studies.yaml input schema ====

class StudiesInput(Config):
    cases: dict[str, CaseInput]           # the composition catalog
    studies: dict[str, StudyInput]        # name -> study definition


class CaseInput(Config):
    """One composition: library keys (platform / drivetrain / sources) + a strategy name."""
    platform: str
    drivetrain: str
    sources: list[str] = []
    strategy: str


class StudyInput(Config):
    """One study: which cases, how each parameter is probed and/or overridden, plus the meta."""
    cases: list[str]                      # required — forgetting it errors
    params: dict[str, ParamInput] = {}
    optimize_by: str = "lcot"
    minimize: bool = True                 # argmin (True) vs argmax (False) of optimize_by
    decompose: list[str] = []             # Sobol targets; empty -> (optimize_by,)
    saltelli_sample_n: int = 1024
    second_order: bool = False
    infeasible_value: float | None = None


class ParamInput(Config):
    """One `params:` entry: an optional `probe` (how to vary it) and/or a `range` override (its
    data). A bare scalar is shorthand for a fixed-value override, `range: {value: scalar}`."""
    probe: ProbeInput | None = None
    range: RangeInput | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_scalar(cls, data):
        if not isinstance(data, dict):
            return {"range": {"value": data}}
        return data


class ProbeInput(Config):
    """How a study varies one parameter. `restrict_to_cases` scopes the probe to a subset of the
    study's member cases (absent -> all of them); optimize probes are typically scoped, sample/sweep
    typically not."""
    kind: ProbeKind
    n: int | None = None                  # grid points (sweep/optimize); sampling ignores it
    restrict_to_cases: list[str] | None = None


class RangeInput(Config):
    """A data override for one leaf — any subset of `{value, lo, hi, dist}`. `value` overrides the
    nominal; `lo`/`hi` (both or neither) override the sampling range a probe reads."""
    value: float | None = None
    lo: float | None = None
    hi: float | None = None
    dist: Distribution | None = None

    @model_validator(mode="after")
    def _check_range(self):
        if (self.lo is None) != (self.hi is None):
            raise ValueError(f"a range override needs both lo and hi (got lo={self.lo}, hi={self.hi})")
        if self.lo is not None and not self.lo < self.hi:
            raise ValueError(f"range lo {self.lo} must be < hi {self.hi}")
        return self


# ================================================= built pipeline objects ====

class Case(Config):
    """One ship concept: the composed library components plus the shared block, ready for a
    strategy. The components point into the study's library, so a value placed on a component is
    seen by every case that uses it."""
    name: str
    platform: Platform
    drivetrain: Drivetrain
    sources: list[EnergySource]
    strategy: str
    shared: Shared


class Probe(Config):
    """How a study probes one parameter: the dotted path, the kind, the range it varies over, and
    (for sweep/optimize) the grid size. `restrict_to_cases` scopes an optimize probe to the cases
    that own that lever (absent -> the whole study)."""
    path: str
    kind: ProbeKind
    range: Range
    n: int | None = None
    restrict_to_cases: list[str] | None = None


class Study(Config):
    """A study, built: the library its axes are placed on, its member cases, its probes (which
    parameters vary, over what range, and how), and the meta carried over from `StudyInput`. The
    cases point into `library`, so compose places an axis once and every consuming case sees it."""
    name: str
    library: Library
    cases: list[Case]
    probes: list[Probe]
    optimize_by: str
    minimize: bool
    decompose: list[str]
    saltelli_sample_n: int
    second_order: bool
    infeasible_value: float | None


# =============================================================== building ====

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
    """Deep-copy the library, apply this study's value overrides onto it, harvest its probes and
    resolve its cases against that copy, and carry its meta."""
    library = assumptions_library.model_copy(deep=True)
    _apply_value_overrides(library, study_input.params)
    probes = [build_probe(path, entry, library)
              for path, entry in study_input.params.items() if entry.probe is not None]
    cases = [build_case(case_name, case_defs, library) for case_name in study_input.cases]
    _check_consumption(name, probes, study_input.cases, case_defs)

    meta = study_input.model_dump(exclude={"cases", "params"})
    meta["decompose"] = meta["decompose"] or [study_input.optimize_by]
    return Study(name=name, library=library, cases=cases, probes=probes, **meta)


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


def build_probe(path: str, entry: ParamInput, library: Library) -> Probe:
    """Assemble a probe: its range is the study's `range` override if that supplies one, else the
    default declared beside the value in assumptions.yaml — re-centered on a value-only override,
    keeping the width."""
    override = entry.range
    if override is not None and override.lo is not None:        # study supplies the range
        sampling_range = Range(lo=override.lo, hi=override.hi, dist=override.dist or "unif")
    else:                                                       # inherit the assumptions default
        sampling_range = _default_range(library, path)
        if override is not None and override.value is not None:
            sampling_range = sampling_range.recentered(override.value)
    probe = entry.probe
    return Probe(path=path, kind=probe.kind, n=probe.n,
                 restrict_to_cases=probe.restrict_to_cases, range=sampling_range)


def _apply_value_overrides(library: Library, params: dict[str, ParamInput]) -> None:
    """Set each param's `value` override on its leaf, in place (a probed leaf is overwritten by its
    axis later, so this only bites the fixed leaves; on a probed leaf the value only re-centers the
    range, handled in `build_probe`)."""
    for path, entry in params.items():
        if entry.range is None or entry.range.value is None:
            continue
        parent, leaf = _walk(library, path)
        setattr(parent, leaf, entry.range.value)


def _default_range(library: Library, path: str) -> Range:
    """The sampling range that sits beside the value in assumptions.yaml, read off the leaf's model
    (`ranges`) by the probe's path — no second pass over the raw file."""
    parent, leaf = _walk(library, path)
    sampling_range = parent.ranges.get(leaf)
    if sampling_range is None:
        raise ValueError(
            f"probe {path!r} has no range: assumptions.yaml declares none beside its value and the "
            "study supplies none — add lo/hi there, or a range on the probe")
    return sampling_range


def _walk(root, path: str) -> tuple[object, str]:
    """Return `(parent, leaf)` for a dotted `path`, crossing the catalog maps by key and an object
    by attribute. A wrong segment raises the underlying KeyError/AttributeError, which names it."""
    *parents, leaf = path.split(".")
    node = root
    for segment in parents:
        node = node[segment] if isinstance(node, Mapping) else getattr(node, segment)
    return node, leaf


# ------------------------------------------------------- consumption (T3) ----
_PART_SELECTOR = {"platforms": "platform", "drivetrains": "drivetrain"}   # head -> case spec attr


def _case_consumes(path: str, spec: CaseInput) -> bool:
    """Whether a member case structurally reaches config leaf `path`: `shared.*` always; a
    `platforms.X`/`drivetrains.X`/`sources.X` leaf only if the case selects part `X`. An
    unattributable head is treated as consumed (we don't claim a leaf we can't reason about is
    dead)."""
    head, *rest = path.split(".")
    if head == "shared" or not rest:
        return True
    part = rest[0]
    if head == "sources":
        return part in spec.sources
    if head in _PART_SELECTOR:
        return part == getattr(spec, _PART_SELECTOR[head])
    return True


def _check_consumption(study_name: str, probes: list[Probe], case_names: list[str],
                       case_defs: dict[str, CaseInput]) -> None:
    """T3: every probed path must be structurally consumed by at least one case it targets. A path
    no case reaches contributes a flat axis — a NaN/degenerate Sobol index or a sweep no measure
    responds to — so we reject it loudly rather than let it surface downstream. A probe's
    `restrict_to_cases` also has to name cases the study actually runs."""
    for probe in probes:
        targets = probe.restrict_to_cases if probe.restrict_to_cases is not None else case_names
        unknown = [name for name in targets if name not in case_names]
        if unknown:
            raise ValueError(
                f"study {study_name!r}: probe {probe.path!r} restricts to {unknown}, "
                f"not among the study's cases {case_names}")
        if not any(_case_consumes(probe.path, case_defs[name]) for name in targets):
            raise ValueError(
                f"study {study_name!r}: probed path {probe.path!r} is consumed by none of its "
                f"cases {list(targets)} — the axis can't affect any output (flat/NaN Sobol). "
                "Check the path or the study's `cases:` selection.")


# resolve forward references now that every model above exists.
for _model in (StudiesInput, Study):
    _model.model_rebuild()
