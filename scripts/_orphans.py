
def legs_per_year(p: Params, v_kn: float, d_km: float,
                  port_h: float = None, avail: float = None) -> float:
    """Number of D_max legs completed per year, given sailing + port time.
    A leg is one one-way hop of d_km plus one port call; a round trip is two
    legs. port_h/avail default to the shared Params values; pass per-powertrain
    values for maneuverability (faster berthing) and lower-maintenance uptime."""
    if port_h is None:
        port_h = p.port_hours_per_call
    if avail is None:
        avail = p.availability
    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    leg_h = sail_h + port_h
    return HOURS_PER_YEAR * avail / leg_h


def crf(rate: float, years: float) -> float:
    """Capital recovery factor (annuity): the annual payment that amortizes a
    unit of CAPEX over `years` at discount `rate`."""
    years = max(years, 1e-6)
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def carried(pl, overhead: float, storage_units: float = 0.0,
            energy_mass_t: float = 0.0) -> float:
    """Revenue cargo per leg in the platform's `cargo_unit`, round-trip averaged.
    Volume-bound (capacity slots) and mass-bound (deadweight) limits act together:
    `min(volume-limited, mass-limited)`.

    Three capacity limits combine: VOLUME (cargo demand is `load_factor` of
    cargo-capable slots; energy stores occupy slots but only `batt_empty_usable_frac`
    of the empty slack is store-usable for free, then they displace cargo 1:1), MASS
    (each ship carries its own energy-carrier weight `energy_mass_t`, drawn from the
    shared `deadweight_t`), and POWER (handled in battery sizing, not here). Legs are
    ASYMMETRIC: `load_factor_imbalance` splits the mean load factor into a fuller
    headhaul and lighter backhaul; a fixed store footprint bites the fuller leg first.
    May return <= 0 (store swamps the ship); callers treat that as infeasible.

    `pl` is a `cases.Platform` (duck-typed here to avoid an import cycle). For a
    container platform `gross_capacity` is TEU slots and `unit_mass_t` is t/TEU, so
    the result is in TEU. For a tonne platform
    `unit_mass_t ≈ 1`, so the volume and mass limits coincide and the result is in
    tonnes. `storage_units` is the energy store's footprint in the same cargo unit."""
    cargo_cap = pl.gross_capacity - overhead
    mass_limited = (pl.deadweight_t - energy_mass_t) / pl.unit_mass_t

    def carried_dir(lf):
        demand = lf * cargo_cap
        slack = cargo_cap - demand
        free_empty = pl.batt_empty_usable_frac * slack
        vol_carried = demand - max(0.0, storage_units - free_empty)
        return min(vol_carried, mass_limited)

    imb = pl.load_factor_imbalance
    lf_head = min(1.0, pl.load_factor * (1.0 + imb))
    lf_back = pl.load_factor * (1.0 - imb)
    return 0.5 * (carried_dir(lf_head) + carried_dir(lf_back))