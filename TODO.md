# TODO / known limitations

Open items only — completed work is in the git history. The big deferred
refactor is sketched in the appendix.

## Speculative parameters
- The new-case params have little/no commercial precedent — all engineering
  estimates. Highest-leverage, most uncertain: `mob_tender_*` (reactor/hull/O&M
  cost, parasitic), `mob_cable_v_cap_kn`, `coastal_untethered_distance_nm`,
  `storm_survival_duration_h`, `cable_efficiency`, `nucc_usd_per_kw`,
  `nucc_overhead_teu_per_mwe`, `nucc_pool_idle_h`, `nucc_pool_availability`. All are swept
  in the per-case tornados (`plot_tornados`); treat the new-case absolute LCOTs
  as order-of-magnitude until grounded in real data.

## Design speed & reactor sizing  ← high value, partly exposed by the AMPERA re-base
- **Design speed is fixed high, cruise is swept low — wrong paradigm for nuclear.**
  Installed power (engine/motor/reactor) is sized at `v_design_max_kn` (22 kn)
  while the optimizer picks a slower cruise — the fossil slow-steaming paradigm
  (cheap to slow down, save fuel). But nuclear has ~free fuel, so a reactor sized
  for 22 kn is wasted CAPEX if it cruises slower.
  - **Surfaced (`print_design_speed_sweep`):** sweeps the *service* speed with
    design coupled to the cruise cap — reactor LCOT bottoms out near **12–14 kn**,
    ~25–30% below the fixed 22 kn design, so sizing for a lower service speed is a
    big, currently-hidden win. Confirms the paradigm is wrong, not just suspect.
  - **Still open:** make it endogenous — let the model choose service speed and add
    a ~15% sea/weather power margin on top, rather than a fixed `v_design_max`; and
    note the AMPERA module granularity (a small drop under 30 MWe → 1 module, ~halving
    containerized reactor CAPEX + overhead) interacts with the chosen service speed.
- **Marginal reactor CAPEX vs size.** `nuclear_usd_per_kw` / `nucc_*` / `nuci_*`
  / `mob_tender_usd_per_kw` are flat $/kW. At these sizes (tens of MW) the marginal
  $/kW likely varies a lot with size (scale economies; step changes at module
  boundaries). Model reactor CAPEX as a size-dependent curve — probably material.

## Case-specific follow-ups
- **Mobile tender:** optionally ride out storms at reduced/zero speed to shrink the
  pack; jointly optimize tethered cruise speed vs battery size vs ships-per-tender
  (currently a fixed cable cap + ~1:1 escort ratio).
- **Leased reactor:** no separate reactor-O&M line (lives in the ship's non-crew
  residual, kept ship-side); nuclear-specialist crew not bundled into the lease
  (`crew_count_nuclear` is the whole complement, not splittable).
- **Nuclear-electric (integrated):** `_elec_propulsion_factor` (pod gains) is applied
  to both nuclear-electric cases; the single-shaft integrated plant may not earn the
  full pod benefit — consider a separate factor.
- **Battery mix for short journeys:** iron-air is power-limited (C/50) up to ~1500 km
  — its pack is sized by *sustained* cruise power (2–6× the energy it uses), which a
  finite LFP/supercap buffer can't relieve. A battery mix may be optimal by route:
  LFP (high power, modest energy) short-haul, iron-air (cheap bulk energy) long-haul
  — or a physical LFP + iron-air split (LFP carries cruise power, iron-air adds range).

## Efficiency & load modeling
- **Slow-steaming asymmetry:** `eta_fossil`/`eta_elec` are constant in speed, so both
  ships get the ideal cube-law energy-vs-speed and fossil slow-steaming is
  over-credited. Real engines droop at part-load while motors stay flat — model
  `eta_fossil` as load/speed-dependent so slowing favours the electric ship.
- **Reefer load coupling:** `p_hotel_kw` is a constant identical across powertrains.
  Reefer load (the large variable part) really scales with reefers carried and is far
  costlier on a battery ship (it comes from the slot-displacing pack, not cheap aux
  gensets). A faithful model couples reefer load to carried cargo and credits reefer
  revenue (high-value cargo the all-TEU-equal model ignores); both out of scope. A
  reefer-light/base/heavy sensitivity (`print_hotel_sensitivity`) surfaces the effect.

## Cargo demand & load factor
- **Empty-slot usability — linear ramp:** `batt_empty_usable_frac` (θ = 0.40) is a hard
  cap (batteries fill θ·slack free, then displace cargo 1:1). Replace with a *marginal*
  cargo cost ramping linearly 0→1 over `[θ·slack, slack]` so constraints bite
  progressively, not at a single threshold.
- **Couple battery-in-slot count to swap time:** the batteries-in-empty-slots quantity
  (`max(0, B - θ·slack)`-ish) is exactly what adds port/swap time — feed it into the
  maneuverability port-time term (the electric port time currently assumes swap ≈ neutral).
- **`bunker_mass_t` is a fixed constant:** really it scales with range/speed (longer
  voyages carry more fuel, tightening fossil's own mass budget); fossil's mass constraint
  is currently never binding.

## Data & itemization
- **Tech-data library:** integrate a techno-economic reference dataset (published reactor /
  battery / hull cost, efficiency, density figures) so params are sourced robustly alongside
  our estimates — tag each with source + uncertainty rather than hand-curated comments.
- **Full O&M & availability itemization:** O&M is partly itemized — crew + tugs + a single
  lumped non-crew residual (`om_*_other_usd_yr`). Break that residual into its components per
  powertrain. Availability is per-powertrain (`availability` vs `availability_elec`) but not
  decomposed; itemize planned maintenance / refueling-or-bunkering / weather downtime.

---

# 3-axis platform refactor — status

**DONE (parity-/golden-gated).** Every powertrain is now a composition of three frozen
dataclasses — `Platform` × `Drivetrain` × `EnergySource` (`scripts/cases.py`) — assembled by
`build_cases(p)` into a `Case` registry and costed through one entry point,
`cost.levelized_cost(case, p, v, d)`. The hand-written `lcot_*` functions are gone; the shared
sizing/economics primitives live in `sizing.py`. `sizing.carried(platform, …)` generalizes the old
`carried_teu` so the binding cargo metric is platform-specific (volume/TEU vs deadweight/tonne).
Lease-vs-ownership and at-sea-charge are pricing/sizing strategies on the EnergySource axis.
`scripts/smoke_check.py` is a lightweight sanity gate (report renders; every case finite & positive
at sample hops). It replaced the byte-exact golden regression once the refactor was done and the
model returned to active flux — a golden test fights every intended change, so it stopped earning
its keep.

**Still open / not yet done:**
- **Config still flat.** `config.yaml` + `Params` remain a single flat namespace; the axes are built
  from it by an adapter in `build_cases`. Nested YAML (`platforms:`/`drivetrains:`/`sources:`) with a
  section-validating loader is deferred — low value while there is one platform, and the user was wary
  of overloading the config.
- **Platform extraction is partial.** Platform carries the cargo/capacity + hull fields; other
  platform scalars (design/min/max speed, prop reference power, efficiencies, crew rate, discount
  rate, route margins, ISO limits) still live in `Params`. Move them onto Platform when a second
  platform needs them to differ.
- **Bulk/chemical platforms** (tonne·km): the `carried`/`Platform` machinery supports `cargo_unit
  ="tonne"`, but no bulk/chemical case exists yet. Adding one needs: real economics (DWT, hull capex,
  load factors, port/cargo-value) — **deliberately left as placeholders, research when populated** —
  plus plot/crossover **faceting by platform** (can't share a TEU·km / tonne·km axis) and generalized
  dict keys (`teukm→unitkm`, `battery_*→storage_*`, add `cargo_unit`).
- **Per-(platform,drivetrain) O&M/overhead overrides:** today resolved per case in `build_cases`; a
  nuclear bulk carrier's O&M ≠ a nuclear container ship's, so a small override map will be wanted once
  platforms multiply.

## Refactor wishes (captured during the build — design the axes to accommodate)

- **Energy-supply-cost layer — DONE (the seam is live; the upstream models are stubs).**
  `supply.py` holds one function per supply (`vlsfo_chemical`, `grid_electricity`, `reactor_thermal`,
  + `ldes_electricity`, `efuel_chemical`), and `build_cases` wires every `EnergySource.supply_usd_per_kwh`
  through one — so supply cost is a named, pluggable strategy on the axis, the analog of the tender.
  Each currently emits the config price; the TODO in each function says what the real model computes
  (refinery; LDES arbitrage; electrolyzer+DAC+synthesis). Remaining: (a) flesh out a real model in any
  stub; (b) ATTACH the not-yet-wired supplies to cases — an e-fuel ship (e-fuel source + a drivetrain;
  `efuel_usd_per_kwh` placeholder exists) and an LDES-charged battery variant. Adding a case is a
  registry row; it needs a drivetrain/platform choice and grows the golden output.
- **Cargo-as-fuel (chemical tankers).** A chemical/e-fuel tanker can burn part of its own cargo as
  fuel. This is a **Platform × EnergySource coupling**: the energy source draws from cargo, so
  (a) the consumed mass is netted out of deliverable cargo (tonne·km denominator shrinks with
  distance/speed), and (b) the fuel is priced at the cargo commodity price (opportunity cost),
  not a separate bunker price. Needs the energy chain to feed back into `carried(...)`.
- **External scenario table (OPTIONAL — only if it doesn't overload the config story).** A
  `scenarios.csv` (or `scenarios:` YAML block) alongside `config.yaml`: a row per named scenario
  layering parameter overrides on the base config, so a sweep of named cases ("2030 NOAK reactor",
  "high VLSFO", "EEZ standoff") runs without editing YAML. Loader reads base then applies each row;
  report/plots iterate scenarios. Defer unless it earns its keep — the user flagged not wanting to
  bloat the config.
