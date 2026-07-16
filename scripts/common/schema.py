"""
schema.py — the config schema, as frozen dataclasses.

The leaf sub-blocks mirror assumptions.yaml one-to-one, so the loader builds them mechanically
(`Block(**yaml_subdict)`); the top-level composites (Platform, Drivetrain, Case, Params) it
assembles by hand from a name plus the nested blocks. Everything — the components AND the cases
that compose them (each with its fixed `route`) — lives in assumptions.yaml. Three nouns — Platform,
Drivetrain, EnergySource (fuel / battery / reactor); a `Case` composes them plus everything
non-component (a `Params` block, a strategy name). Top-level structures first; the sub-blocks
they compose at the bottom.

The `EnergySource` base lives here (it's the empty slot `Case.sources` composes), and so now do
its concrete fuel/battery/reactor subclasses — all pure data. The strategy-independent cost and
sizing functions that operate on them live in `model/costing.py`.
"""

from __future__ import annotations

from dataclasses import dataclass


# ================================================ top-level structures ====

@dataclass(frozen=True)
class Case:
    """One composition: the components plus its fixed route/economics. Pure data. Which axes to
    sweep/optimize and which leaves to sample is NOT here — that is study design (studies.yaml),
    applied to a case by `ingest`; a case is only the thing a study explores."""
    name: str
    sources: tuple[EnergySource, ...]   # zero or more (zero = fueled-for-life converter)
    platform: Platform
    drivetrain: Drivetrain
    strategy: str                       # names the function in the strategies package
    params: Params


@dataclass(frozen=True)
class EnergySource:
    """Base for the energy-supplying technologies (concrete subclasses below)."""
    name: str


@dataclass(frozen=True)
class Platform:
    name: str
    cargo_unit: str         # "TEU" | "tonne" — capacity & LCOT denominator, and the discriminator
    capacity: Capacity
    capex: HullCapex
    resistance: Resistance
    hotel_base_kw: float
    slot_limits: SlotLimits


@dataclass(frozen=True)
class Drivetrain:
    """Energy -> shaft, including the integral powerplant's CAPEX (the separable sources are on
    the Case; what's fixed to the drivetrain is here)."""
    name: str
    type: str                           # "mechanical" | "electric"; selects the electric-only propulsion factors
    efficiency: DriveEfficiency         # conversion losses source output -> shaft / hotel (and reactor thermal -> electricity)
    capex: DrivetrainCapex              # converter (+ integrated reactor) capital cost and life
    overhead: Overhead                  # fixed slot footprint of the drivetrain (displaces cargo)
    operations: Operations              # port/voyage ops: crew, availability, port time, tug, O&M, hotel delta
    propulsion_factor: PropulsionFactor # itemized hull/propeller multipliers scaling propulsion power


# ================= sub-blocks (detail; mostly mirror assumptions.yaml's sub-blocks) ====

# ---- case ----
@dataclass(frozen=True)
class Params:
    """The Case's non-component inputs — the shared assumptions injected from `shared` (equal
    across cases, to keep them comparable). economics/margins stay grouped; the voyage scalars
    are flat, and a study varies any of them (op_v_kn is the usual lever, d_km the usual sweep,
    but design_v_kn or the load factors are just as reachable — all are ordinary config leaves)."""
    economics: Economics            # general economic assumptions, equal across cases to keep them comparable
    margins: Margins                # design sizing margins (energy reserve + propulsion power margin)
    load_factor: float              # mean cargo load factor over the route/market
    load_factor_imbalance: float    # head/back-haul demand split (directions differ in demand)
    d_km: float = 10000.0           # nominal D_max hop; a sweep axis overrides it
    op_v_kn: float = 14.0           # nominal operating speed; an optimize axis overrides it
    design_v_kn: float | None = None  # design speed the cheap converter is sized to (integrated-reactor cases size to op speed and ignore it)


@dataclass(frozen=True)
class Economics:
    """Economic factors that should be the same across cases to keep them
    comparable"""
    discount_rate: float
    crew_cost_usd_yr: float         # loaded annual cost per crew member


@dataclass(frozen=True)
class Margins:
    """Design margins applied during sizing."""
    energy_reserve: float           # spare energy on a battery ship's pack (weather/contingency)
    sea: float                      # "sea margin" (maritime term): power margin on installed propulsion for added resistance from weather/waves and hull fouling/aging


@dataclass(frozen=True)
class Axis:
    """A parameter varied over a grid, becoming one block dimension. Same shape whether
    `optimize` (argmin-collapsed for min lcot) or `sweep` (retained as an LCOT-vs-X trace) —
    the study's block decides which. `path` is the dotted config leaf the grid replaces (the
    SAME addressing `sample`/`fix` use), so ANY leaf can be an axis; `name` (its last segment)
    labels the block dimension."""
    path: str                       # dotted config leaf the grid replaces, e.g. "shared.op_v_kn"
    lo: float
    hi: float
    n: int                          # number of grid points

    @property
    def name(self) -> str:
        return self.path.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class Range:
    """A parameter's plausible range, declared ON its value in assumptions.yaml (a leaf written as
    `{value:, range: [lo, hi], dist:}`) and harvested by the loader into a path-keyed library.
    This is *data about the parameter* (a prior); which params actually vary in a run is study
    design, decided against these ranges in the study file — never here."""
    lo: float
    hi: float
    dist: str = "unif"              # sampling distribution; only "unif" is wired today


# ---- platform ----
@dataclass(frozen=True)
class Capacity:
    gross: float            # hull capacity in cargo_unit (TEU slots / DWT tonnes)
    unit_mass_t: float      # mass per cargo unit (t/TEU laden mix)
    deadweight_t: float     # cargo + onboard-energy mass budget


@dataclass(frozen=True)
class HullCapex:
    hull_usd: float
    life_yr: float


@dataclass(frozen=True)
class Resistance:
    p_ref_kw: float         # propulsion power at v_ref (admiralty P~v^3 curve)
    v_ref_kn: float


@dataclass(frozen=True)
class SlotLimits:
    batt_empty_usable_frac: float   # slack a battery may take free before displacing cargo. Not all is usable due to safety & practicality
    container_max_gross_t: float    # effective per-TEU mass cap (ISO limit + margin; assumes demand pushes container standards/handling to allow heavier marinized units where worth the effort)


# ---- drivetrain ----
@dataclass(frozen=True)
class DriveEfficiency:
    drive: float                    # source output -> shaft
    hotel: float                    # source output -> hotel bus
    generation: float | None = None # reactor thermal -> electricity (integrated reactor only; the source reactors carry their own in ReactorSource)


@dataclass(frozen=True)
class DrivetrainCapex:
    """Capital cost of the integral powerplant, $/kW of rated useful (output-side) power.
    `converter_usd_per_kw` is the FINAL converter to shaft/electric; what it physically buys
    depends on the drivetrain — the engine (fossil mechanical), the reactor+steam+shaft plant
    (nuclear direct-drive: the reactor IS the converter), or just the electric motor (any electric
    drive). `reactor_usd_per_kw` is the SEPARATE reactor+generator stage feeding the motor, which
    exists only on the integrated-electric drivetrain; a direct-drive reactor has no separate line,
    and a reactor that lives off the drivetrain is an EnergySource instead. The strategy sizes the
    kW to the design or operating speed."""
    converter_usd_per_kw: float     # final converter to shaft/electric (see class doc for the per-drivetrain meaning)
    life_yr: float                  # converter amortization life
    reactor_usd_per_kw: float | None = None   # integrated-electric only: the reactor + generator stage ahead of the motor
    reactor_life_yr: float | None = None       # integrated-electric only: reactor+generator amortization life


@dataclass(frozen=True)
class Overhead:
    """Footprint that displaces cargo: a fixed count or a per-MWe rate (sized from power). Shared by
    drivetrains and reactor sources. Slots/TEU today; the tonne-based fields are placeholders for
    future bulk (DWT) platforms and are unused for now."""
    slots: float | None = None              # fixed TEU-slot footprint
    teu_per_mwe: float | None = None        # TEU slots per MWe (sized from power)
    mass_t: float | None = None             # FUTURE (unused): fixed deadweight footprint for bulk (tonne) platforms
    mass_t_per_mwe: float | None = None     # FUTURE (unused): deadweight per MWe for bulk platforms


@dataclass(frozen=True)
class Operations:
    port_hours: float
    availability: float
    tug_usd_per_call: float
    hotel_delta_kw: float           # this drivetrain's adjustment to platform.hotel_base_kw (e.g. negative for electric)
    crew_count: float               # complement, x crew_cost_usd_yr -> annual crew cost; different number of crew required based on technology
    om_other_usd_yr: float          # other fixed O&M (maintenance, insurance, stores, admin)


@dataclass(frozen=True)
class PropulsionFactor:
    """Itemized efficiency multipliers; their product scales required propulsion power. Most reduce
    the power the hull DEMANDS (hull_form/coating/routing/propeller). wider_eff is the electric
    motor's edge: DriveEfficiency.drive is a voyage-average (estimated from a single marine-engine
    figure), and an electric motor stays nearer its optimum across the speed/sea-state range — a
    gain that average misses. propeller/wider_eff are electric-only (1.0 on mechanicals). A
    sea-state-resolved model would eventually supersede these constants (see TODO)."""
    hull_form: float        # optimized hull form
    coating: float          # anti-fouling coatings
    propeller: float        # pods / large low-RPM props (electric only)
    wider_eff: float        # electric motor stays near optimum across speeds/sea states (electric only)
    routing: float          # weather routing, trim/draft optimization, on-time (no rush) speed


# ==================================== energy sources (concrete EnergySource family) ====
# Pure data mirroring assumptions.yaml's source blocks; the cost/sizing functions are in
# model/costing.py. Strategies pick a source by its concrete subclass, then call the matching
# function.

@dataclass(frozen=True)
class FuelSource(EnergySource):
    price: FuelPrice
    energy_mass_t: float            # onboard energy-carrier mass (bunkers; 0 for fission fuel)


@dataclass(frozen=True)
class BatterySource(EnergySource):
    capex: BatteryCapex
    energy: BatteryEnergy
    efficiency: BatteryEfficiency
    min_discharge_h: float          # power limit (max kW = installed kWh / this); 0 = none
    charge_usd_per_kwh: float       # grid/shore charge price, folded in


@dataclass(frozen=True)
class ReactorSource(EnergySource):
    """Base for the two reactor-as-source variants. Holds only the shared reactor block;
    each subtype adds its integration-specific fields and has its own cost function (in
    model/costing.py), so strategies match on the SUBTYPE, not this base."""
    capex: ReactorCapex
    fuel_usd_per_kwh_th: float
    generation: float               # reactor thermal -> electricity


@dataclass(frozen=True)
class ContainerizedReactor(ReactorSource):
    """A reactor module that replaces cargo containers on an electric ship: occupies slots,
    adds an onboard hotel load, bills $/kWh levelized over its fleet-pooled utilization."""
    overhead: Overhead              # slot footprint (teu_per_mwe, sized from power)
    hotel_delta_kw: float           # extra onboard hotel (crew/security) a containerized reactor adds, on top of the drivetrain's
    pool: Pool                      # fleet-pooled utilization


@dataclass(frozen=True)
class TenderReactor(ReactorSource):
    """A separate uncrewed vessel (capex.hull_usd is the ship ex-reactor) that tethers an
    electric ship and feeds it over a cable; $/kWh levelized over a tethered/idle duty
    cycle, not a slot footprint."""
    parasitic_kw: float             # uncrewed DP station-keeping + cooling
    om_other_usd_yr: float          # uncrewed remote ops + asset-loss insurance
    availability: float
    idle_h: float                   # reposition-or-wait between escorts (a non-delivering hour)
    tether: Tether                  # cable efficiency + speed cap + coastal geometry + weather detach


# ---- source sub-blocks (mirror assumptions.yaml's source sub-blocks) ----
@dataclass(frozen=True)
class FuelPrice:
    # different fuels quote differently; the cost model reads whichever is set
    usd_per_t: float | None = None
    lhv_kwh_per_kg: float | None = None
    usd_per_kwh_chem: float | None = None
    usd_per_kwh_th: float | None = None


@dataclass(frozen=True)
class BatteryCapex:
    usd_per_kwh: float
    cycle_life: float
    calendar_life_yr: float


@dataclass(frozen=True)
class BatteryEnergy:
    kwh_per_teu: float
    pack_wh_per_kg: float           # system density -> battery mass (deadweight)
    dod: float                      # usable depth of discharge


@dataclass(frozen=True)
class BatteryEfficiency:
    charge: float
    discharge: float


@dataclass(frozen=True)
class ReactorCapex:
    usd_per_kw: float
    life_yr: float
    hull_usd: float | None = None   # tender only: the vessel ex-reactor


@dataclass(frozen=True)
class Pool:
    idle_h: float                   # wait in the shared pool between assignments
    availability: float


@dataclass(frozen=True)
class Tether:
    cable_efficiency: float
    cable_v_cap_kn: float                   # max speed while tethered (source-imposed speed cap)
    standoff_nm: float                      # coastal sub-leg each side of the tether
    detach_duration_h: float = 0.0          # longest continuous cable-dropped stretch the pack must sail unassisted (SIZING event, not an expected flow)
    detach_frac: float = 0.0                # expected fraction of tethered time with the floating tether dropped for weather; an EXPECTED VALUE, calibrated from weather data / voyage simulation per route


