"""
schema.py — the config schema, as frozen dataclasses.

The leaf sub-blocks mirror config.yaml one-to-one, so the loader builds them mechanically
(`Block(**yaml_subdict)`); the top-level composites (Platform, Drivetrain, Case, Params) it
assembles by hand from a name plus the nested blocks, and the per-case route/axes come from
cases.csv. Three nouns — Platform, Drivetrain, EnergySource (fuel / battery / reactor); a
`Case` composes them plus everything non-component (a `Params` block, a strategy name, axes in
parameter space to optimize/sweep). Top-level structures first; the sub-blocks they compose at
the bottom.

The `EnergySource` base lives here (it's the empty slot `Case.sources` composes); the concrete
fuel/battery/reactor subclasses and their cost methods live in sources.py.
"""

from __future__ import annotations

from dataclasses import dataclass


# ================================================ top-level structures ====

@dataclass(frozen=True)
class Case:
    """One composition plus how to explore it. Pure data: the optimizer reads `sweep`/`optimize`
    and drives sweep -> optimize -> strategy."""
    name: str
    sources: tuple[EnergySource, ...]   # zero or more (zero = fueled-for-life converter)
    platform: Platform
    drivetrain: Drivetrain
    strategy: str                       # names the function in the strategies package
    params: Params
    optimize: tuple[Axis, ...]          # free axes: searched per swept point for min lcot
    sweep: tuple[Axis, ...]             # swept axes: iterated to trace LCOT-vs-X (D_max…)


@dataclass(frozen=True)
class EnergySource:
    """Base for the energy-supplying technologies (concrete subclasses in sources.py)."""
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


# ================= sub-blocks (detail; mostly mirror config.yaml's sub-blocks) ====

# ---- case ----
@dataclass(frozen=True)
class Params:
    """The Case's non-component inputs. """
    economics: Economics    # general economic assumptions, equal across cases to keep them comparable
    margins: Margins        # design sizing margins (energy reserve + propulsion power margin)
    route: Route            # per-case fixed route/condition params


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
class Route:
    """Per-case fixed route/condition params. Strategy-specific ones are optional (a fuel case needs no battery/tender field)."""
    load_factor: float                      # mean cargo load factor (for the entire route/market)
    load_factor_imbalance: float            # head/back-haul split (directions are not equal in demand for transportation)
    d_km: float = 10000.0                    # nominal D_max hop; the swept axis overrides it in most cases
    op_v_kn: float = 14.0                    # nominal operating speed; the optimized axis overrides it in most cases
    design_v_kn: float | None = None        # nominal design speed the cheap converter is sized to; like d_km/op_v_kn an axis can override it, though it's usually fixed (the integrated-reactor cases size to op speed and ignore it)
    storm_duration_h: float | None = None   # storm-buffer energy for the tender case: a battery ship riding out a storm while detached from the tether
    standoff_nm: float | None = None        # coastal sub-leg each side of the tether (tender)
    idle_h: float | None = None             # tender reposition-or-wait between escorts


@dataclass(frozen=True)
class Axis:
    """A point-coordinate the optimizer varies over a grid. Same shape whether `optimize`
    (searched for min lcot) or `sweep` (traced as LCOT-vs-X) — the Case's list decides which."""
    param: str                      # the point-dict key it sets, e.g. "op_v_kn" or "d_km"
    lo: float
    hi: float
    n: int                          # number of grid points


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


