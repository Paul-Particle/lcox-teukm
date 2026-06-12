"""
params.py — model inputs.

`Params` is the typed schema for every economic/physical input to the model,
with representative defaults. `load_params()` reads the canonical values from
config.yaml and overrides those defaults, validating that the file contains no
unknown keys (a typo'd parameter is an error, not a silent no-op).

All values are in the model's base units (see units.py): energy kWh, power kW,
time hours, distance km, speed knots, mass kg, money US$.
"""

from dataclasses import dataclass, fields


@dataclass
class Params:
    # ---- shared hull / route (scale both ships, fixed to representative values)
    gross_slots: float = 3000.0        # nominal hull container capacity (TEU)
    load_factor: float = 0.80          # avg fraction of available slots filled
    load_factor_imbalance: float = 0.2 # headhaul/backhaul split: head=LF*(1+imb), back=LF*(1-imb);
                                       # default models real trade imbalance (head 0.96 / back 0.64
                                       # at LF 0.8). 0 = symmetric; fixed battery bites the fuller
                                       # leg first (carried_teu). TODO: richer fill distribution.
    hull_capex_usd: float = 45e6       # newbuild hull excl. propulsion
    discount_rate: float = 0.08
    hull_life_yr: float = 25.0
    port_hours_per_call: float = 18.0  # fossil/nuclear-direct berthing + cargo (conventional)
    port_hours_elec: float = 16.0      # electric-drive: pods/azimuth maneuverability -> faster
                                       # berthing (swap assumed ~neutral; see TODO.md)
    availability: float = 0.95         # fraction of the year in service (fossil/nuclear)
    availability_elec: float = 0.97    # battery/electric-drive: lower drivetrain maintenance (EV-like)
    weather_reserve: float = 0.20      # ROUTE requirement: extra leg-energy margin for weather /
                                       # headwinds / detours, carried by any battery ship (not a
                                       # chemistry property). Routine; deep-discharge below dod is an
                                       # emergency-only buffer, not for everyday bad weather.
    deadweight_t: float = 41000.0      # total deadweight for cargo + onboard energy carrier (t).
                                       # Each ship subtracts its OWN energy-carrier mass (fossil
                                       # bunkers, battery pack, nuclear ~0) -> cargo mass budget.
    cargo_t_per_teu: float = 12.0      # avg laden mass per TEU (full+empty mix); sets mass limit
    bunker_mass_t: float = 3000.0      # fossil onboard fuel mass — explicit deadweight it carries
                                       # (battery/nuclear carry ~none). TODO: scales with range.
    iso_container_max_gross_t: float = 30.0  # ISO 20ft max gross mass; caps energy per battery
                                             # container -> dense, heavy chemistries (iron-air) need
                                             # more (weight-limited) containers, displacing more cargo
    iso_container_margin: float = 0.20       # tolerance over ISO max for marinized/reinforced units

    # ---- crew & port services (O&M itemized; see maneuverability credit) -------
    crew_cost_usd_yr: float = 90000.0  # loaded annual cost per crew member (rotation, benefits)
    crew_count_fossil: float = 22.0
    crew_count_elec: float = 20.0      # electric-drive: fewer engine-room engineers
    crew_count_nuclear: float = 30.0   # reactor operators + security (also nuclear-electric)
    tug_usd_per_call: float = 5000.0   # conventional berthing tug fees per port call
    tug_usd_per_call_elec: float = 2000.0  # azimuth pods/thrusters -> much less tug assistance

    # ---- powertrain sizing reference (admiralty-style P ~ v^3)
    p_ref_kw: float = 20000.0          # propulsion power at v_ref
    v_ref_kn: float = 18.0
    p_hotel_kw: float = 1500.0         # baseline hotel/reefer load (fossil). TODO: reefer part is
                                       # variable & battery-costly (reefer-heavy penalizes battery
                                       # ships) — see hotel sensitivity / TODO.md
    hotel_delta_elec_kw: float = -150.0   # electric: a few fewer engine-room engineers (accommodation)
    hotel_delta_nuclear_kw: float = 250.0 # nuclear: more crew + security + reactor auxiliaries
    v_design_max_kn: float = 22.0      # sizes the installed motor/engine
    v_min_kn: float = 5.0              # minimum economic sailing speed
    v_max_kn: float = 22.0

    # ---- conversion efficiencies
    eta_fossil: float = 0.48           # fuel chemical -> useful (good 2-stroke). TODO: constant in
                                       # speed; real engines droop at part-load, so slow-steaming
                                       # should favour electric over fossil (see TODO.md)
    eta_elec: float = 0.88             # battery pack -> useful (drivetrain); ~flat across speed
    eta_nuclear: float = 0.30          # reactor thermal -> useful (marine PWR steam cycle)
    eta_hotel: float = 0.97            # source bus -> hotel load: hotel draws directly off the
                                       # ship-service bus, NOT through the drive motor — so on
                                       # electric/battery/nuclear-electric ships it bypasses eta_elec
    eta_aux_gen: float = 0.42          # fossil: auxiliary genset fuel -> hotel electricity (hotel
                                       # runs off aux diesels, not the main 2-stroke -> below eta_fossil)

    # ---- energy prices
    fuel_usd_per_t: float = 550.0      # VLSFO
    fuel_lhv_kwh_per_kg: float = 11.1  # ~40 MJ/kg
    elec_usd_per_kwh: float = 0.09     # delivered industrial / shore power

    # ---- fossil powertrain
    engine_usd_per_kw: float = 400.0
    engine_life_yr: float = 25.0
    om_fossil_other_usd_yr: float = 1.52e6   # NON-crew O&M: insurance, repairs, lube, stores (crew is now
                                       # crew_count_fossil x crew_cost_usd_yr; tugs are separate)
    fossil_overhead_slots: float = 120.0  # engine room + bunkers, in slot-equivalents
    fossil_propulsion_factor: float = 0.95 # smaller hull-form/coating/routing gain fossil can adopt
                                           # once the design barrier is overcome (vs electric's stack)

    # ---- electric powertrain
    motor_usd_per_kw: float = 120.0
    motor_life_yr: float = 25.0
    om_elec_other_usd_yr: float = 1.2e6      # NON-crew O&M: insurance, repairs (fewer moving parts), stores
    elec_fixed_overhead_slots: float = 30.0  # compact motors only (no big engine/tanks)
    # Electric-drive hull/propeller efficiency, itemized (the product scales
    # propulsion power; shared by LFP, iron-air, nuclear-electric, mobile).
    # Source ranges: hull form up to -20%, coatings ~-3%, propeller/pods -15..20%,
    # wider motor eff -5..10%, weather/trim routing ~-8%; values are conservative-end.
    elec_hull_form_factor: float = 0.92    # optimized hull form
    elec_coating_factor: float = 0.98      # anti-fouling coatings
    elec_propeller_factor: float = 0.88    # larger low-RPM props on pods, cleaner flow
    elec_wider_eff_factor: float = 0.96    # motor efficient across a wide speed range
    elec_routing_factor: float = 0.96      # weather routing, trim/draft, on-time speed
    batt_empty_usable_frac: float = 0.40   # fraction of the empty (1-load_factor) slack that
                                           # batteries may occupy before displacing cargo;
                                           # <1 for dangerous-goods/stability/access limits.
                                           # 1.0 = batteries use all slack first. Hard cap;
                                           # TODO: linear ramp 0->1 over [frac*slack, slack] (TODO.md).
    battery_usd_per_kwh: float = 250.0     # installed, marinized system level
    battery_kwh_per_teu: float = 3000.0    # energy per battery container (3 MWh/TEU)
    battery_pack_wh_per_kg: float = 130.0  # LFP system energy density (conservative; cells ~160-180,
                                           # system/pack lower) -> battery mass (deadweight)
    battery_dod: float = 0.90              # routine usable depth of discharge (below it = emergency)
    battery_cycle_life: float = 4000.0
    battery_calendar_life_yr: float = 12.0
    battery_eta_charge: float = 0.97       # LFP grid -> stored
    battery_eta_discharge: float = 0.98    # LFP stored -> delivered (round-trip ~0.95)
    battery_min_discharge_h: float = 0.0   # rated discharge-duration floor; 0 = no power limit

    # ---- iron-air battery powertrain (Form Energy class; shares hull, motor,
    # drivetrain, electricity price, and swap logistics with the LFP ship).
    # Iron-air's ~4x mass per kWh IS now enforced via the deadweight constraint
    # (carried_teu), so its weight bites: mass-limited short-haul, infeasible long.
    ironair_usd_per_kwh: float = 30.0      # installed system (chemistry target <$20/kWh)
    ironair_kwh_per_teu: float = 1500.0    # ~half LFP volumetric density per container
    ironair_dod: float = 0.95              # routine usable depth (chemistry tolerates deep discharge)
    ironair_cycle_life: float = 10000.0    # non-binding at 100-h rates
    ironair_calendar_life_yr: float = 20.0
    ironair_eta_charge: float = 0.55       # iron-air grid -> stored; charge-limited chemistry
    ironair_eta_discharge: float = 0.82    # iron-air stored -> delivered (round-trip ~0.45 AC-AC).
                                           # TODO: the charge/discharge split is approximate; only
                                           # the documented ~40-50% round-trip is well-sourced
    ironair_min_discharge_h: float = 50.0  # C/50 max sustained discharge: max pack kW = installed kWh / 50 h
    # Discharge fixed at C/50 (2x Form's ~C/100 design point): still >100x below the
    # ~3C passivation onset for additive-stabilized Fe electrodes, so efficiency barely moves.
    # Binding limits at this rate are heat rejection (~I·η, super-linear) and air-electrode O2 transport, not RTE.
    ironair_pack_wh_per_kg: float = 30.0   # system density (~4x heavier than LFP); enforced as a
                                           # deadweight constraint -> bites long-haul iron-air.
                                           # TODO: key uncertain input — sweep in the tornado

    # ---- nuclear powertrain (onboard SMR; no D_max-driven sizing)
    nuclear_usd_per_kw: float = 6000.0     # installed reactor + steam plant + drivetrain,
                                           # per useful kW (lit. $5-8k/kWe; fleet-scale
                                           # vendor targets as low as $750-2000/kW)
    nuclear_life_yr: float = 25.0
    nuclear_fuel_usd_per_kwh_th: float = 0.012  # HALEU fuel cycle, ~$12/MWh thermal
    om_nuclear_other_usd_yr: float = 7.3e6       # NON-crew O&M: security, bespoke insurance pools,
                                           # regulatory (crew = crew_count_nuclear x crew_cost)
    nuclear_overhead_slots: float = 120.0  # reactor + shielding ~ conventional engine room

    # ---- nuclear-electric powertrains (reactor -> electricity -> electric motor;
    # reuse eta_nuclear, eta_elec, motor_*, the electric propulsion-factor stack). End-to-end
    # useful eff = eta_nuclear*eta_elec (~0.26) vs 0.30 direct-drive, but unlocks
    # the electric-drive hull/prop gains and compact overhead.
    # TODO: nucc_* (modular marine reactor) cost/size are speculative — sweep them;
    # the integrated single-shaft case may not earn the full pod benefit of the
    # electric propulsion-factor stack (consider a separate factor).
    # (a) containerized modular reactor (AMPERA-class). Sized CONTINUOUSLY to the
    # ship's design power for now (no integer-module discretization).
    nucc_usd_per_kw: float = 5000.0        # factory-built modular, below integrated
    nucc_life_yr: float = 15.0             # swappable/leased modules refreshed sooner
    nucc_overhead_teu_per_mwe: float = 1.2  # reactor slot footprint per MWe installed, scaled
                                            # LINEARLY with reactor power. AMPERA anchor: 36 TEU /
                                            # 30 MWe (two 40ft cores + shielding all sides). TODO:
                                            # shielding is a surface effect, so really sub-linear.
    nucc_om_other_usd_yr: float = 5.3e6          # NON-crew residual (crew = crew_count_nuclear x crew_cost)
    nucc_fuel_usd_per_kwh_th: float = 0.012     # HALEU, same cycle as direct-drive
    # (a2) leased containerized reactor (Reactor-as-a-Service): same modules as (a),
    # but CAPEX recovered via a per-kWh rate levelized over the reactor's POOL
    # utilization, not one ship's duty cycle. Speculative — sweep.
    nucc_pool_idle_h: float = 8.0          # reactor wait in the shared pool between ship
                                           # assignments (< ship port time -> the pooling benefit)
    nucc_pool_availability: float = 0.92   # leased reactor's own uptime (maintenance/refuel rota)
    # (b) integrated single reactor:
    nuci_usd_per_kw: float = 6500.0        # adds generator + power electronics vs direct-drive
    nuci_life_yr: float = 30.0
    nuci_overhead_slots: float = 140.0     # reactor + shielding + switchboard
    nuci_om_other_usd_yr: float = 7.3e6          # NON-crew residual (crew = crew_count_nuclear x crew_cost)
    nuci_fuel_usd_per_kwh_th: float = 0.012

    # ---- mobile nuclear reactor tender (charges battery ships at sea) ----------
    # Uncrewed micro-reactor tender (AMPERA-x class: thorium TRISO, subcritical,
    # sCO2 ~50%, two-core ~30 MWe, no refuel for decades, containerized). Stays in
    # international waters (avoids the EEZ) for trivial licensing; lean open-ocean
    # build, asset-loss insurance. Speculative — engineering estimates (sweep).
    # Dedicated-escort model: the ship sails untethered through coastal waters,
    # meets the tender at the regulatory border, and they cable up to cross the
    # open ocean together (tender drives propulsion + recharges the coastal drain).
    coastal_untethered_distance_nm: float = 12.0  # untethered battery run at each end. 12 nm = UNCLOS
                                               # territorial-sea limit (Freedom-of-Navigation minimum);
                                               # set 200.0 to test a full-EEZ standoff. Sweep it.
    storm_survival_duration_h: float = 12.0    # worst-case at-sea cable disconnect (severe sea state);
                                               # ship rides it out on battery. Sizes pack if > coastal.
    cable_efficiency: float = 0.96             # tether electrical transmission efficiency (bus / reactor)
    mob_cable_v_cap_kn: float = 16.0           # max safe speed while cable-connected (< free design max)
    mob_tender_reactor_kw: float = 30000.0     # AMPERA two-core net electric (15 MWe x 2)
    mob_tender_parasitic_kw: float = 2500.0    # uncrewed; DP station-keeping + cooling (sCO2, no water)
    mob_tender_usd_per_kw: float = 7000.0      # microreactor NOAK ~$7k/kWe (FOAK $10-35k — sweep)
    mob_tender_capex_hull_usd: float = 50.0e6  # small uncrewed DP vessel, ex-reactor (lean build)
    mob_tender_life_yr: float = 25.0
    mob_tender_om_other_usd_yr: float = 4.0e6        # UNCREWED: remote ops + asset-loss insurance; no crew,
                                               # no refuel, few port calls
    mob_tender_fuel_usd_per_kwh_th: float = 0.012  # thorium core, multi-decade ~one-time (negligible)
    mob_tender_eta_nuclear: float = 0.45       # reactor thermal -> electric (sCO2 ~50%)
    tender_idle_h: float = 5.0                 # TENDER-side "port-time equivalent": hours at the border
                                               # between dropping one ship and picking up the next; sets
                                               # tender utilization. Estimate — sweep it.
    mob_tender_availability: float = 0.95      # decades without refuel, stays at sea; rare maintenance
    mob_port_hours_per_call: float = 12.0      # no battery swap in port -> shorter than 18


def load_params(path) -> Params:
    """Build a Params from a YAML config file, overriding the defaults.

    Raises ValueError if the file contains a key that is not a Params field,
    so a mistyped parameter name surfaces immediately instead of being ignored.
    """
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping of parameter -> value")

    valid = {f.name for f in fields(Params)}
    unknown = set(data) - valid
    if unknown:
        raise ValueError(
            f"{path}: unknown parameter(s) {sorted(unknown)}; "
            f"valid keys are {sorted(valid)}")

    # Every Params field is numeric. Coerce here so a value that YAML parsed as
    # a string (e.g. "45.0e6" — PyYAML needs a signed exponent, "45.0e+6") or a
    # genuine typo fails loudly at load time, not deep inside the math.
    coerced = {}
    for key, value in data.items():
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{path}: {key!r} must be a number, got {value!r}")
        try:
            coerced[key] = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{path}: {key!r} must be a number, got {value!r}")
    return Params(**coerced)
