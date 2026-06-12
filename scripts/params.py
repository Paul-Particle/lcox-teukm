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
    load_factor_imbalance: float = 0.0 # headhaul/backhaul split: head=LF*(1+imb), back=LF*(1-imb);
                                       # 0 = symmetric. Mean preserved; fixed battery bites the
                                       # fuller leg first (see carried_teu).
                                       # TODO: a richer fill distribution than a 2-point head/back.
    hull_capex_usd: float = 45e6       # newbuild hull excl. propulsion
    discount_rate: float = 0.08
    hull_life_yr: float = 25.0
    port_hours_per_call: float = 18.0  # cargo + (for electric) battery swap; assumed equal.
                                       # TODO: per-powertrain berthing/maneuverability credit; swap
                                       # adds time only for batteries in empty slots (see TODO.md)
    availability: float = 0.95         # fraction of the year the ship is in service
                                       # TODO: maybe higher for electric/iron-air (lower maintenance)
    deadweight_cargo_t: float = 38000.0  # cargo deadweight budget (t) for a FOSSIL ship, net of its
                                         # bunkers/stores/ballast; batteries consume it (mass limit)
    cargo_t_per_teu: float = 12.0      # avg laden mass per TEU (full+empty mix); sets mass limit
    bunker_mass_t: float = 3000.0      # fossil onboard fuel mass; battery/nuclear ships don't carry
                                       # it, so they recover this as extra cargo deadweight.
                                       # TODO: fixed — really scales with range/speed (TODO.md)

    # ---- powertrain sizing reference (admiralty-style P ~ v^3)
    p_ref_kw: float = 20000.0          # propulsion power at v_ref
    v_ref_kn: float = 18.0
    p_hotel_kw: float = 1500.0         # constant hotel/reefer load. TODO: reefer part is variable &
                                       # battery-costly (reefer-heavy penalizes battery ships), crew
                                       # part is powertrain-dependent — see hotel sensitivity/TODO.md
    v_design_max_kn: float = 22.0      # sizes the installed motor/engine
    v_min_kn: float = 9.0              # TODO: check this minimum speed is justified
    v_max_kn: float = 22.0

    # ---- conversion efficiencies
    eta_fossil: float = 0.48           # fuel chemical -> useful (good 2-stroke). TODO: constant in
                                       # speed; real engines droop at part-load, so slow-steaming
                                       # should favour electric over fossil (see TODO.md)
    eta_elec: float = 0.88             # battery pack -> useful (drivetrain); ~flat across speed
    eta_nuclear: float = 0.30          # reactor thermal -> useful (marine PWR steam cycle)

    # ---- energy prices
    fuel_usd_per_t: float = 550.0      # VLSFO
    fuel_lhv_kwh_per_kg: float = 11.1  # ~40 MJ/kg
    elec_usd_per_kwh: float = 0.09     # delivered industrial / shore power

    # ---- fossil powertrain
    engine_usd_per_kw: float = 400.0
    engine_life_yr: float = 25.0
    om_fossil_usd_yr: float = 3.5e6    # crew, insurance, repairs, lube (ex-fuel). TODO: crew not
                                       # itemized/scaled; tug fees not modeled (see TODO.md)
    fossil_overhead_slots: float = 120.0  # engine room + bunkers, in slot-equivalents
                                          # TODO: fossil may warrant its own (smaller) hull/prop
                                          # efficiency factor once the design barrier is overcome

    # ---- electric powertrain
    motor_usd_per_kw: float = 120.0
    motor_life_yr: float = 25.0
    om_elec_usd_yr: float = 3.0e6      # fewer moving parts, no fuel system (14% below fossil).
                                       # TODO: add a maneuverability tug-saving credit (see TODO.md)
    elec_fixed_overhead_slots: float = 30.0  # compact motors only (no big engine/tanks)
    elec_prop_power_factor: float = 0.90   # hull/propeller/pod/coating/routing gains the
                                           # electric drivetrain enables; scales propulsion
                                           # power (shared by Li-ion + iron-air). Conservative
                                           # 10% lump pending itemized calc — see TODO.md.
    batt_empty_usable_frac: float = 0.40   # fraction of the empty (1-load_factor) slack that
                                           # batteries may occupy before displacing cargo;
                                           # <1 for dangerous-goods/stability/access limits.
                                           # 1.0 = batteries use all slack first. Hard cap;
                                           # TODO: linear ramp 0->1 over [frac*slack, slack] (TODO.md).
    battery_usd_per_kwh: float = 250.0     # installed, marinized system level
    battery_kwh_per_teu: float = 3000.0    # energy per battery container (3 MWh/TEU)
    battery_pack_wh_per_kg: float = 160.0  # Li-ion system energy density -> battery mass (deadweight)
    battery_dod: float = 0.90              # usable depth of discharge
    battery_reserve: float = 0.20          # weather/safety margin on top of leg energy
    battery_cycle_life: float = 4000.0
    battery_calendar_life_yr: float = 12.0
    battery_eta_charge: float = 0.97       # Li-ion grid -> stored
    battery_eta_discharge: float = 0.98    # Li-ion stored -> delivered (round-trip ~0.95)
    battery_min_discharge_h: float = 0.0   # rated discharge-duration floor; 0 = no power limit

    # ---- iron-air battery powertrain (Form Energy class; shares hull, motor,
    # drivetrain, electricity price, and swap logistics with the Li-ion ship).
    # Iron-air's ~5x mass per kWh IS now enforced via the deadweight constraint
    # (carried_teu), so its weight bites: mass-limited short-haul, infeasible long.
    ironair_usd_per_kwh: float = 30.0      # installed system (chemistry target <$20/kWh)
    ironair_kwh_per_teu: float = 1500.0    # ~half Li-ion volumetric density per container
    ironair_dod: float = 0.95              # chemistry tolerates deep discharge
    ironair_reserve: float = 0.20          # weather/safety margin on top of leg energy
    ironair_cycle_life: float = 10000.0    # non-binding at 100-h rates
    ironair_calendar_life_yr: float = 20.0
    ironair_eta_charge: float = 0.55       # iron-air grid -> stored; charge-limited chemistry
    ironair_eta_discharge: float = 0.82    # iron-air stored -> delivered (round-trip ~0.45 AC-AC).
                                           # TODO: the charge/discharge split is approximate; only
                                           # the documented ~40-50% round-trip is well-sourced
    ironair_min_discharge_h: float = 100.0 # 100-h class: max pack kW = installed kWh / 100 h
    ironair_pack_wh_per_kg: float = 30.0   # system density (~5x heavier than Li-ion); enforced as a
                                           # deadweight constraint -> bites long-haul iron-air.
                                           # TODO: key uncertain input — sweep in the tornado

    # ---- nuclear powertrain (onboard SMR; no D_max-driven sizing)
    nuclear_usd_per_kw: float = 6000.0     # installed reactor + steam plant + drivetrain,
                                           # per useful kW (lit. $5-8k/kWe; fleet-scale
                                           # vendor targets as low as $750-2000/kW)
    nuclear_life_yr: float = 25.0
    nuclear_fuel_usd_per_kwh_th: float = 0.012  # HALEU fuel cycle, ~$12/MWh thermal
    om_nuclear_usd_yr: float = 10.0e6      # specialized crew, security, insurance pools,
                                           # regulatory; least-quantified parameter
    nuclear_overhead_slots: float = 120.0  # reactor + shielding ~ conventional engine room

    # ---- nuclear-electric powertrains (reactor -> electricity -> electric motor;
    # reuse eta_nuclear, eta_elec, motor_*, elec_prop_power_factor). End-to-end
    # useful eff = eta_nuclear*eta_elec (~0.26) vs 0.30 direct-drive, but unlocks
    # the electric-drive hull/prop gains and compact overhead.
    # TODO: nucc_* (modular marine reactor) cost/size are speculative — sweep them;
    # the integrated single-shaft case may not earn the full pod benefit of
    # elec_prop_power_factor (consider a separate factor).
    # (a) containerized modular reactor units with a per-unit power cap:
    nucc_unit_kw: float = 15000.0          # net electric per reactor module
    nucc_usd_per_kw: float = 5000.0        # factory-built modular, below integrated
    nucc_life_yr: float = 15.0             # swappable/leased modules refreshed sooner
    nucc_overhead_slots_per_unit: float = 45.0  # module + shielding; scales with unit count
    nucc_om_usd_yr: float = 8.0e6          # some O&M shifts to module lessor
    nucc_fuel_usd_per_kwh_th: float = 0.012     # HALEU, same cycle as direct-drive
    # (b) integrated single reactor:
    nuci_usd_per_kw: float = 6500.0        # adds generator + power electronics vs direct-drive
    nuci_life_yr: float = 30.0
    nuci_overhead_slots: float = 140.0     # reactor + shielding + switchboard
    nuci_om_usd_yr: float = 10.0e6
    nuci_fuel_usd_per_kwh_th: float = 0.012

    # ---- mobile nuclear reactor tender (charges battery ships at sea) ----------
    # Uncrewed micro-reactor tender (AMPERA-x class: thorium TRISO, subcritical,
    # sCO2 ~50%, two-core ~30 MWe, no refuel for decades, containerized). Stays in
    # international waters (avoids the EEZ) for trivial licensing; lean open-ocean
    # build, asset-loss insurance. Speculative — engineering estimates (sweep).
    mob_rendezvous_distance_nm: float = 200.0  # EEZ edge: the ship crosses this no-charge coastal
                                               # zone on battery (tender stays in intl waters)
    mob_cable_v_cap_kn: float = 16.0           # max safe speed while cable-connected (< free max)
    mob_charge_availability: float = 0.85      # fraction of underway time actually charging (sea state)
    mob_disconnect_reserve: float = 0.25       # extra battery to ride out a disconnected spell
    mob_rendezvous_spacing_h: float = 12.0     # SHIP-side: sailing time between its open-ocean top-ups;
                                               # sets the bridging battery only. TODO: fixed; jointly
                                               # optimizing trades battery size vs tender count
    mob_charge_power_kw: float = 25000.0       # cable/connector power limit (~reactor-limited)
    mob_tender_reactor_kw: float = 30000.0     # AMPERA two-core net electric (15 MWe x 2)
    mob_tender_parasitic_kw: float = 2500.0    # uncrewed; DP station-keeping + cooling (sCO2, no water)
    mob_tender_usd_per_kw: float = 7000.0      # microreactor NOAK ~$7k/kWe (FOAK $10-35k — sweep)
    mob_tender_capex_hull_usd: float = 50.0e6  # small uncrewed DP vessel, ex-reactor (lean build)
    mob_tender_life_yr: float = 25.0
    mob_tender_om_usd_yr: float = 4.0e6        # UNCREWED: remote ops + asset-loss insurance; no crew,
                                               # no refuel, few port calls
    mob_tender_fuel_usd_per_kwh_th: float = 0.012  # thorium core, multi-decade ~one-time (negligible)
    mob_tender_eta_nuclear: float = 0.45       # reactor thermal -> electric (sCO2 ~50%)
    mob_tender_idle_h: float = 5.0             # TENDER-side "port-time equivalent": non-charging hours
                                               # per top-up (transit to + waiting for next ship); sets
                                               # utilization. Estimate — sweep it.
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
