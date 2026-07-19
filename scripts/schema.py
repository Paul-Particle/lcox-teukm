"""
schema.py — the typed config schema (pydantic models), mirroring assumptions.yaml 1:1.

Every numeric leaf is a `Range`: a nominal `value` plus an optional sampling band
(`lo`/`hi`/`dist`). A bare YAML scalar becomes `Range(value=x)`; a `{value, lo, hi, dist}` mapping
is a ranged leaf. `Library.model_validate(assumptions_dict)` builds and validates the whole nested
tree in one call — a malformed leaf, a bad band, an unknown source `type`, or a stray key all raise
a precise error.

The source family is a discriminated union on `type` (fuel / battery / containerized-reactor /
tender-reactor). Components are keyed by name in their catalogs, so the models carry no `name`
field. Layout is big-picture-first (`Library` → components → sub-blocks → `Range`); forward
references are resolved by `model_rebuild()` at the bottom, once every model exists.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Distribution = Literal["unif", "loguniform"]     # sampling draw / grid spacing (linear vs geometric)
ProbeKind = Literal["sample", "sweep", "optimize"]      # how a study varies a parameter (fixed = none)


class Node(BaseModel):
    """Base for every config node: reject unknown keys, so a typo in the YAML is an error rather
    than a silently-ignored field."""
    model_config = ConfigDict(extra="forbid")


# ============================================================= library ====

class Library(Node):
    """The whole assumptions.yaml, typed: the shared scalars plus the component catalogs (keyed by
    name). Built and validated in one `Library.model_validate(assumptions_dict)` call."""
    shared: Shared
    platforms: dict[str, Platform]
    drivetrains: dict[str, Drivetrain]
    sources: dict[str, EnergySource]


# ======================================================== shared scalars ====

class Shared(Node):
    """The cross-case assumptions from `shared:` — equal across cases to keep them comparable. The
    voyage scalars (d_km / op_v_kn / design_v_kn) are ordinary leaves a study may vary."""
    discount_rate: Range
    crew_cost_usd_yr: Range          # loaded annual cost per crew member
    load_factor: Range               # mean cargo load factor over the route/market
    load_factor_imbalance: Range     # head/back-haul demand split
    d_km: Range                      # nominal D_max hop
    op_v_kn: Range                   # nominal operating speed
    design_v_kn: Range               # design speed the cheap converter is sized to
    margins: Margins


# ============================================================ platform ====

class Platform(Node):
    cargo_unit: str                  # "TEU" | "tonne" — capacity & LCOT denominator, and the discriminator
    capacity: Capacity
    capex: HullCapex
    resistance: Resistance
    hotel_base_kw: Range
    slot_limits: SlotLimits


# ============================================================ drivetrain ====

class Drivetrain(Node):
    """Energy -> shaft, including the integral powerplant's CAPEX (separable sources sit on the
    Case; what's fixed to the drivetrain is here)."""
    type: str                        # "mechanical" | "electric"; selects the electric-only propulsion factors
    efficiency: DriveEfficiency
    capex: DrivetrainCapex
    overhead: Overhead
    operations: Operations
    propulsion_factor: PropulsionFactor


# ==================================== energy sources (discriminated on `type`) ====

class FuelSource(Node):
    """Thin commodity source — just a price (folded in), plus onboard carrier mass."""
    type: Literal["fuel"]
    price: FuelPrice
    energy_mass_t: Range             # onboard energy-carrier mass (bunkers; 0 for fission fuel)


class BatterySource(Node):
    type: Literal["battery"]
    capex: BatteryCapex
    energy: BatteryEnergy
    efficiency: BatteryEfficiency
    min_discharge_h: Range           # power limit (max kW = installed kWh / this); 0 = none
    charge_usd_per_kwh: Range        # grid/shore charge price, folded in


class ContainerizedReactor(Node):
    """A reactor module that replaces cargo containers on an electric ship: occupies slots, adds an
    onboard hotel load, bills $/kWh levelized over its fleet-pooled utilization."""
    type: Literal["containerized-reactor"]
    capex: ReactorCapex
    fuel_usd_per_kwh_th: Range
    generation: Range                # reactor thermal -> electricity
    overhead: Overhead               # slot footprint (teu_per_mwe, sized from power)
    hotel_delta_kw: Range            # extra onboard hotel a containerized reactor adds
    pool: Pool                       # fleet-pooled utilization


class TenderReactor(Node):
    """A separate uncrewed vessel that tethers an electric ship and feeds it over a cable; $/kWh
    levelized over a tethered/idle duty cycle, not a slot footprint."""
    type: Literal["tender-reactor"]
    capex: ReactorCapex              # capex.hull_usd is the tender vessel ex-reactor
    fuel_usd_per_kwh_th: Range
    generation: Range
    parasitic_kw: Range              # uncrewed DP station-keeping + cooling
    om_other_usd_yr: Range           # uncrewed remote ops + asset-loss insurance
    availability: Range
    idle_h: Range                    # reposition-or-wait between escorts (a non-delivering hour)
    tether: Tether


EnergySource = Annotated[
    FuelSource | BatterySource | ContainerizedReactor | TenderReactor,
    Field(discriminator="type"),
]


# ==================================================== sub-blocks (detail) ====

class Margins(Node):
    """Design margins applied during sizing."""
    energy_reserve: Range            # spare energy on a battery ship's pack (weather/contingency)
    sea: Range                       # power margin on installed propulsion (weather/fouling vs calm trials)


# ---- platform ----
class Capacity(Node):
    gross: Range                     # hull capacity in cargo_unit (TEU slots / DWT tonnes)
    unit_mass_t: Range               # mass per cargo unit (t/TEU laden mix)
    deadweight_t: Range              # cargo + onboard-energy mass budget


class HullCapex(Node):
    hull_usd: Range
    life_yr: Range


class Resistance(Node):
    p_ref_kw: Range                  # propulsion power at v_ref (admiralty P~v^3 curve)
    v_ref_kn: Range


class SlotLimits(Node):
    batt_empty_usable_frac: Range    # slack a battery may take free before displacing cargo
    container_max_gross_t: Range     # effective per-TEU mass cap


# ---- drivetrain ----
class DriveEfficiency(Node):
    drive: Range                     # source output -> shaft
    hotel: Range                     # source output -> hotel bus
    generation: Range | None = None  # reactor thermal -> electricity (integrated reactor only)


class DrivetrainCapex(Node):
    """Capital cost of the integral powerplant, $/kW of rated useful power. `converter_usd_per_kw`
    is the final converter to shaft/electric (engine / direct-drive reactor / electric motor);
    `reactor_usd_per_kw` is the separate reactor+generator stage that exists only on the
    integrated-electric drivetrain."""
    converter_usd_per_kw: Range
    life_yr: Range
    reactor_usd_per_kw: Range | None = None
    reactor_life_yr: Range | None = None


class Overhead(Node):
    """Cargo-displacing footprint: a fixed count or a per-MWe rate. Shared by drivetrains and
    reactor sources; the tonne-based fields are placeholders for future bulk platforms."""
    slots: Range | None = None
    teu_per_mwe: Range | None = None
    mass_t: Range | None = None
    mass_t_per_mwe: Range | None = None


class Operations(Node):
    port_hours: Range
    availability: Range
    tug_usd_per_call: Range
    hotel_delta_kw: Range            # this drivetrain's adjustment to platform.hotel_base_kw
    crew_count: Range                # complement, x crew_cost_usd_yr -> annual crew cost
    om_other_usd_yr: Range           # other fixed O&M (maintenance, insurance, stores, admin)


class PropulsionFactor(Node):
    """Itemized efficiency multipliers; their product scales required propulsion power.
    propeller/wider_eff are electric-only (1.0 on mechanicals)."""
    hull_form: Range
    coating: Range
    propeller: Range
    wider_eff: Range
    routing: Range


# ---- sources ----
class FuelPrice(Node):
    # different fuels quote differently; the cost model reads whichever is set
    usd_per_t: Range | None = None
    lhv_kwh_per_kg: Range | None = None
    usd_per_kwh_chem: Range | None = None
    usd_per_kwh_th: Range | None = None


class BatteryCapex(Node):
    usd_per_kwh: Range
    cycle_life: Range
    calendar_life_yr: Range


class BatteryEnergy(Node):
    kwh_per_teu: Range
    pack_wh_per_kg: Range            # system density -> battery mass (deadweight)
    dod: Range                       # usable depth of discharge


class BatteryEfficiency(Node):
    charge: Range
    discharge: Range


class ReactorCapex(Node):
    usd_per_kw: Range
    life_yr: Range
    hull_usd: Range | None = None    # tender only: the vessel ex-reactor


class Pool(Node):
    idle_h: Range                    # wait in the shared pool between assignments
    availability: Range


class Tether(Node):
    cable_efficiency: Range
    cable_v_cap_kn: Range            # max speed while tethered (source-imposed speed cap)
    standoff_nm: Range               # coastal sub-leg each side of the tether
    detach_duration_h: Range         # longest continuous cable-dropped stretch the pack sails unassisted (sizing event)
    detach_frac: Range               # expected fraction of tethered time the tether is dropped for weather


# =============================================================== the leaf ====

class Range(Node):
    """The universal numeric leaf: a nominal `value` the model reads when the param is fixed, plus
    an optional sampling band (`lo`/`hi`/`dist`) — a prior a study samples/sweeps/optimizes against.
    Accepts a bare scalar (`value` only) or a `{value, lo, hi, dist}` mapping. `dist` doubles as the
    grid spacing for sweeps/optimizes (`unif` -> linear, `loguniform` -> geometric)."""
    value: float
    lo: float | None = None
    hi: float | None = None
    dist: Distribution = "unif"

    @model_validator(mode="before")
    @classmethod
    def _coerce_scalar(cls, data):
        if isinstance(data, bool):
            raise ValueError("a numeric leaf cannot be a bool")
        if isinstance(data, (int, float)):
            return {"value": float(data)}
        return data

    @model_validator(mode="after")
    def _check_band(self):
        if self.lo is not None or self.hi is not None:
            if self.lo is None or self.hi is None:
                raise ValueError(f"a sampling band needs both lo and hi (got lo={self.lo}, hi={self.hi})")
            if not self.lo < self.hi:
                raise ValueError(f"sampling band lo {self.lo} must be < hi {self.hi}")
        return self


# ============================================= studies.yaml input schema ====
# The same move as `Library`, for the other YAML: pydantic models mirror studies.yaml so one
# `StudiesInput.model_validate(...)` validates the whole file (the case catalog + the studies).

class StudiesInput(Node):
    cases: dict[str, CaseInput]           # the composition catalog
    studies: dict[str, StudyInput]        # name -> study definition


class CaseInput(Node):
    """One composition: library keys (platform / drivetrain / sources) + a strategy name."""
    platform: str
    drivetrain: str
    sources: list[str] = []
    strategy: str


class StudyInput(Node):
    """One study: which cases, how each parameter is probed and/or overridden, plus the meta."""
    cases: list[str]                      # required — forgetting it errors
    params: dict[str, ParamInput] = {}
    optimize_by: str = "lcot"
    minimize: bool = True                 # argmin (True) vs argmax (False) of optimize_by
    decompose: list[str] = []             # Sobol targets; empty -> (optimize_by,)
    saltelli_sample_n: int = 1024
    second_order: bool = False
    infeasible_value: float | None = None


class ParamInput(Node):
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


class ProbeInput(Node):
    kind: ProbeKind
    n: int | None = None                  # grid points (sweep/optimize); sampling ignores it


class RangeInput(Node):
    """A data override for one leaf — any subset of a `Range`'s fields, deep-merged onto the
    assumptions leaf (so `value` is inherited unless you set it)."""
    value: float | None = None
    lo: float | None = None
    hi: float | None = None
    dist: Distribution | None = None


# resolve the forward references now that every model above exists (big-picture-first layout).
for _model in (Library, StudiesInput):
    _model.model_rebuild()
