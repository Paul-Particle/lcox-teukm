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
    hull_capex_usd: float = 45e6       # newbuild hull excl. propulsion
    discount_rate: float = 0.08
    hull_life_yr: float = 25.0
    port_hours_per_call: float = 18.0  # cargo + (for electric) battery swap; assumed equal
    availability: float = 0.95         # fraction of the year the ship is in service

    # ---- powertrain sizing reference (admiralty-style P ~ v^3)
    p_ref_kw: float = 20000.0          # propulsion power at v_ref
    v_ref_kn: float = 18.0
    p_hotel_kw: float = 1500.0         # constant hotel/reefer load
    v_design_max_kn: float = 22.0      # sizes the installed motor/engine
    v_min_kn: float = 9.0
    v_max_kn: float = 22.0

    # ---- conversion efficiencies
    eta_fossil: float = 0.48           # fuel chemical -> useful (good 2-stroke)
    eta_elec: float = 0.88             # battery pack -> useful (drivetrain)
    eta_charge: float = 0.95           # grid -> battery pack
    eta_nuclear: float = 0.30          # reactor thermal -> useful (marine PWR steam cycle)

    # ---- energy prices
    fuel_usd_per_t: float = 550.0      # VLSFO
    fuel_lhv_kwh_per_kg: float = 11.1  # ~40 MJ/kg
    elec_usd_per_kwh: float = 0.09     # delivered industrial / shore power

    # ---- fossil powertrain
    engine_usd_per_kw: float = 400.0
    engine_life_yr: float = 25.0
    om_fossil_usd_yr: float = 3.5e6    # crew, insurance, repairs, lube (ex-fuel)
    fossil_overhead_slots: float = 120.0  # engine room + bunkers, in slot-equivalents

    # ---- electric powertrain
    motor_usd_per_kw: float = 120.0
    motor_life_yr: float = 25.0
    om_elec_usd_yr: float = 3.0e6      # fewer moving parts, no fuel system
    elec_fixed_overhead_slots: float = 30.0  # compact motors only (no big engine/tanks)
    battery_usd_per_kwh: float = 250.0     # installed, marinized system level
    battery_kwh_per_teu: float = 3000.0    # energy per battery container (3 MWh/TEU)
    battery_pack_wh_per_kg: float = 160.0  # for the deadweight sanity check
    battery_dod: float = 0.90              # usable depth of discharge
    battery_reserve: float = 0.20          # weather/safety margin on top of leg energy
    battery_cycle_life: float = 4000.0
    battery_calendar_life_yr: float = 12.0
    battery_eta_rt: float = 1.0            # pack round-trip eff.; Li-ion losses sit in eta_charge
    battery_min_discharge_h: float = 0.0   # rated discharge-duration floor; 0 = no power limit

    # ---- iron-air battery powertrain (Form Energy class; shares hull, motor,
    # drivetrain, electricity price, and swap logistics with the Li-ion ship).
    # Deadweight is not enforced for either chemistry; iron-air's ~5x mass per
    # kWh makes the model optimistic for it (see README).
    ironair_usd_per_kwh: float = 30.0      # installed system (chemistry target <$20/kWh)
    ironair_kwh_per_teu: float = 1500.0    # ~half Li-ion volumetric density per container
    ironair_dod: float = 0.95              # chemistry tolerates deep discharge
    ironair_reserve: float = 0.20          # weather/safety margin on top of leg energy
    ironair_cycle_life: float = 10000.0    # non-binding at 100-h rates
    ironair_calendar_life_yr: float = 20.0
    ironair_eta_rt: float = 0.45           # electrochemical round-trip efficiency
    ironair_min_discharge_h: float = 100.0 # 100-h class: max pack kW = installed kWh / 100 h

    # ---- nuclear powertrain (onboard SMR; no D_max-driven sizing)
    nuclear_usd_per_kw: float = 6000.0     # installed reactor + steam plant + drivetrain,
                                           # per useful kW (lit. $5-8k/kWe; fleet-scale
                                           # vendor targets as low as $750-2000/kW)
    nuclear_life_yr: float = 25.0
    nuclear_fuel_usd_per_kwh_th: float = 0.012  # HALEU fuel cycle, ~$12/MWh thermal
    om_nuclear_usd_yr: float = 10.0e6      # specialized crew, security, insurance pools,
                                           # regulatory; least-quantified parameter
    nuclear_overhead_slots: float = 120.0  # reactor + shielding ~ conventional engine room


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
