"""
schema.py — the typed assumptions library (pydantic models), mirroring assumptions.yaml 1:1.

Every numeric leaf is a plain `float`. A leaf may carry an optional sampling range — a
`{value, lo, hi, dist}` mapping in the YAML: the value stays on the leaf, and the range
(`lo`/`hi`/`dist`) is peeled off onto the owning model's `ranges` at load. `Library.model_validate`
builds and validates the whole nested tree in one call — a malformed leaf, a bad range, an unknown
source `type`, or a stray key all raise a precise error.

The peel is type-driven: `_split_ranges` reads each field's declared type to decide leaf vs
sub-block (`_is_scalar`), so there's no key-guessing, and pydantic's own nested validation drives
the recursion. After load the leaves are ordinary floats — compose swaps arrays onto the probed
ones, everything else reads them bare. Every model shares one base, `Parameters`; models with no
leaves (`Library`, `Range`) just carry an empty `ranges`.

The source family is a discriminated union on `type` (fuel / battery / containerized-reactor /
tender-reactor). Components are keyed by name in their catalogs, so the models carry no `name`
field. Layout is big-picture-first (`Library` → components → sub-blocks → the leaf range); forward
references are resolved by `model_rebuild()` at the bottom, once every model exists. The
studies.yaml input schema lives in config.py, alongside the Study objects it builds.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import UnionType
from typing import Annotated, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator

Distribution = Literal["unif", "loguniform"]     # sampling draw / grid spacing (linear vs geometric)


def _is_scalar(annotation) -> bool:
    """True for a numeric leaf field — `float` or `float | None` — the fields a range may sit on."""
    return annotation is float or (
        get_origin(annotation) in (Union, UnionType) and float in get_args(annotation))


def _peel(field, value):
    """Split a range off one field's raw value: a numeric leaf given a `{value, lo, hi}` mapping
    yields `(value, range_dict)`; anything else (a scalar leaf, a sub-block, a str) passes through
    as `(value, None)`. Leaf-ness is read from the field *type*, never guessed from keys."""
    if field is None or not _is_scalar(field.annotation) or not isinstance(value, Mapping):
        return value, None
    range_dict = {key: value[key] for key in ("lo", "hi", "dist") if key in value}
    return value["value"], range_dict or None


class Parameters(BaseModel):
    """Base for every assumptions model: reject unknown keys (a typo is an error, not a silently
    ignored field), and peel each numeric leaf's optional range ({value, lo, hi, dist}) off into
    `ranges` (keyed by leaf name) so the leaf itself stays a plain float. Models with no leaves just
    carry an empty `ranges`."""
    model_config = ConfigDict(extra="forbid")
    ranges: dict[str, Range] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _split_ranges(cls, data):
        if not isinstance(data, dict):
            return data
        peeled = {name: _peel(cls.model_fields.get(name), value) for name, value in data.items()}
        cleaned = {name: value for name, (value, _range) in peeled.items()}
        cleaned["ranges"] = {name: range_dict for name, (_value, range_dict) in peeled.items() if range_dict}
        return cleaned


# ============================================================= library ====

class Library(Parameters):
    """The whole assumptions.yaml, typed: the shared scalars plus the component catalogs (keyed by
    name). Built and validated in one `Library.model_validate(assumptions_dict)` call."""
    shared: Shared
    platforms: dict[str, Platform]
    drivetrains: dict[str, Drivetrain]
    sources: dict[str, EnergySource]


# ======================================================== shared scalars ====

class Shared(Parameters):
    """The cross-case assumptions from `shared:` — equal across cases to keep them comparable. The
    voyage scalars (d_km / op_v_kn / design_v_kn) are ordinary leaves a study may vary."""
    discount_rate: float
    crew_cost_usd_yr: float          # loaded annual cost per crew member
    load_factor: float               # mean cargo load factor over the route/market
    load_factor_imbalance: float     # head/back-haul demand split
    d_km: float                      # nominal D_max hop
    op_v_kn: float                   # nominal operating speed
    design_v_kn: float               # design speed the cheap converter is sized to
    margins: Margins


# ============================================================ platform ====

class Platform(Parameters):
    cargo_unit: str                  # "TEU" | "tonne" — capacity & LCOT denominator, and the discriminator
    capacity: Capacity
    capex: HullCapex
    resistance: Resistance
    hotel_base_kw: float
    slot_limits: SlotLimits


# ============================================================ drivetrain ====

class Drivetrain(Parameters):
    """Energy -> shaft, including the integral powerplant's CAPEX (separable sources sit on the
    Case; what's fixed to the drivetrain is here)."""
    type: str                        # "mechanical" | "electric"; selects the electric-only propulsion factors
    efficiency: DriveEfficiency
    capex: DrivetrainCapex
    overhead: Overhead
    operations: Operations
    propulsion_factor: PropulsionFactor


# ==================================== energy sources (discriminated on `type`) ====

class FuelSource(Parameters):
    """Thin commodity source — just a price (folded in), plus onboard carrier mass."""
    type: Literal["fuel"]
    price: FuelPrice
    energy_mass_t: float             # onboard energy-carrier mass (bunkers; 0 for fission fuel)


class BatterySource(Parameters):
    type: Literal["battery"]
    capex: BatteryCapex
    energy: BatteryEnergy
    efficiency: BatteryEfficiency
    min_discharge_h: float           # power limit (max kW = installed kWh / this); 0 = none
    charge_usd_per_kwh: float        # grid/shore charge price, folded in


class ContainerizedReactor(Parameters):
    """A reactor module that replaces cargo containers on an electric ship: occupies slots, adds an
    onboard hotel load, bills $/kWh levelized over its fleet-pooled utilization."""
    type: Literal["containerized-reactor"]
    capex: ReactorCapex
    fuel_usd_per_kwh_th: float
    generation: float                # reactor thermal -> electricity
    overhead: Overhead               # slot footprint (teu_per_mwe, sized from power)
    hotel_delta_kw: float            # extra onboard hotel a containerized reactor adds
    pool: Pool                       # fleet-pooled utilization


class TenderReactor(Parameters):
    """A separate uncrewed vessel that tethers an electric ship and feeds it over a cable; $/kWh
    levelized over a tethered/idle duty cycle, not a slot footprint."""
    type: Literal["tender-reactor"]
    capex: ReactorCapex              # capex.hull_usd is the tender vessel ex-reactor
    fuel_usd_per_kwh_th: float
    generation: float
    parasitic_kw: float              # uncrewed DP station-keeping + cooling
    om_other_usd_yr: float           # uncrewed remote ops + asset-loss insurance
    availability: float
    idle_h: float                    # reposition-or-wait between escorts (a non-delivering hour)
    tether: Tether


EnergySource = Annotated[
    FuelSource | BatterySource | ContainerizedReactor | TenderReactor,
    Field(discriminator="type"),
]


# ==================================================== sub-blocks (detail) ====

class Margins(Parameters):
    """Design margins applied during sizing."""
    energy_reserve: float            # spare energy on a battery ship's pack (weather/contingency)
    sea: float                       # power margin on installed propulsion (weather/fouling vs calm trials)


# ---- platform ----
class Capacity(Parameters):
    gross: float                     # hull capacity in cargo_unit (TEU slots / DWT tonnes)
    unit_mass_t: float               # mass per cargo unit (t/TEU laden mix)
    deadweight_t: float              # cargo + onboard-energy mass budget


class HullCapex(Parameters):
    hull_usd: float
    life_yr: float


class Resistance(Parameters):
    p_ref_kw: float                  # propulsion power at v_ref (admiralty P~v^3 curve)
    v_ref_kn: float


class SlotLimits(Parameters):
    batt_empty_usable_frac: float    # slack a battery may take free before displacing cargo
    container_max_gross_t: float     # effective per-TEU mass cap


# ---- drivetrain ----
class DriveEfficiency(Parameters):
    drive: float                     # source output -> shaft
    hotel: float                     # source output -> hotel bus
    generation: float | None = None  # reactor thermal -> electricity (integrated reactor only)


class DrivetrainCapex(Parameters):
    """Capital cost of the integral powerplant, $/kW of rated useful power. `converter_usd_per_kw`
    is the final converter to shaft/electric (engine / direct-drive reactor / electric motor);
    `reactor_usd_per_kw` is the separate reactor+generator stage that exists only on the
    integrated-electric drivetrain."""
    converter_usd_per_kw: float
    life_yr: float
    reactor_usd_per_kw: float | None = None
    reactor_life_yr: float | None = None


class Overhead(Parameters):
    """Cargo-displacing footprint: a fixed count or a per-MWe rate. Shared by drivetrains and
    reactor sources; the tonne-based fields are placeholders for future bulk platforms."""
    slots: float | None = None
    teu_per_mwe: float | None = None
    mass_t: float | None = None
    mass_t_per_mwe: float | None = None


class Operations(Parameters):
    port_hours: float
    availability: float
    tug_usd_per_call: float
    hotel_delta_kw: float            # this drivetrain's adjustment to platform.hotel_base_kw
    crew_count: float                # complement, x crew_cost_usd_yr -> annual crew cost
    om_other_usd_yr: float           # other fixed O&M (maintenance, insurance, stores, admin)


class PropulsionFactor(Parameters):
    """Itemized efficiency multipliers; their product scales required propulsion power.
    propeller/wider_eff are electric-only (1.0 on mechanicals)."""
    hull_form: float
    coating: float
    propeller: float
    wider_eff: float
    routing: float


# ---- sources ----
class FuelPrice(Parameters):
    # different fuels quote differently; the cost model reads whichever is set
    usd_per_t: float | None = None
    lhv_kwh_per_kg: float | None = None
    usd_per_kwh_chem: float | None = None
    usd_per_kwh_th: float | None = None


class BatteryCapex(Parameters):
    usd_per_kwh: float
    cycle_life: float
    calendar_life_yr: float


class BatteryEnergy(Parameters):
    kwh_per_teu: float
    pack_wh_per_kg: float            # system density -> battery mass (deadweight)
    dod: float                       # usable depth of discharge


class BatteryEfficiency(Parameters):
    charge: float
    discharge: float


class ReactorCapex(Parameters):
    usd_per_kw: float
    life_yr: float
    hull_usd: float | None = None    # tender only: the vessel ex-reactor


class Pool(Parameters):
    idle_h: float                    # wait in the shared pool between assignments
    availability: float


class Tether(Parameters):
    cable_efficiency: float
    cable_v_cap_kn: float            # max speed while tethered (source-imposed speed cap)
    standoff_nm: float               # coastal sub-leg each side of the tether
    detach_duration_h: float         # longest continuous cable-dropped stretch the pack sails unassisted (sizing event)
    detach_frac: float               # expected fraction of tethered time the tether is dropped for weather


# =============================================================== the leaf range ====

class Range(Parameters):
    """A sampling range peeled off a numeric leaf: the prior a study varies it over (bounds plus
    draw/grid spacing). Lives on the owning model's `ranges`, keyed by leaf name, and is harvested
    by a probe. `dist` doubles as the grid spacing for sweeps/optimizes (`unif` -> linear,
    `loguniform` -> geometric)."""
    lo: float
    hi: float
    dist: Distribution = "unif"

    @model_validator(mode="after")
    def _check(self):
        if not self.lo < self.hi:
            raise ValueError(f"range lo {self.lo} must be < hi {self.hi}")
        return self

    def recentered(self, value: float) -> Range:
        """The range shifted to sit symmetrically around `value`, keeping its width."""
        half_width = (self.hi - self.lo) / 2
        return Range(lo=value - half_width, hi=value + half_width, dist=self.dist)


# resolve the forward references now that every model above exists (big-picture-first layout).
for _model in (Library, Range):
    _model.model_rebuild()
