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
  for 22 kn is wasted CAPEX if it cruises slower. Real practice sizes installed
  power from a *service speed* + ~15% sea/weather margin. At minimum **sweep
  `v_design_max`**; better, model a service-speed + margin basis and let nuclear's
  own speed/sizing optimum emerge. The AMPERA re-base makes this urgent: ~32 MWe
  design demand just exceeds one 30 MWe module, forcing 2 modules (60 MWe, 87%
  overshoot) — a small drop in design speed would land under 30 MWe and roughly
  halve containerized reactor CAPEX + overhead.
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

# Appendix — deferred 3-axis platform refactor

The big refactor: support bulk carriers & chemical tankers (tonne·km, not just TEU·km) and
let every powertrain compose cleanly. Most cases are recombinations along three orthogonal
axes: **Platform** (cargo/route) × **Drivetrain** (energy→shaft) × **Energy-source/logistics**.
Today all three are baked into one flat `Params` + one `lcot_*(p,v,d)->dict` per case (the
deliberate non-DRY duplication across the new cases is what motivates this).

**Target:** a cost model = `compose(Platform, Drivetrain, EnergySource)`, with a single shared
`levelized_cost(case, v, d)` instead of N `lcot_*` functions.

- **Three frozen dataclasses** —
  - `Platform`: `name`, `cargo_unit` ("TEU"/"tonne"), `gross_capacity`, `load_factor`, hull
    capex/life, port hours, availability, `batt_usable_frac`, + a `displace(overhead, storage)` fn.
  - `Drivetrain`: `eta`, `propulsion_factor`, prop capex/life, om, overhead.
  - `EnergySource`: `kind` ∈ fuel/battery/reactor, price, `BatterySpec|None`, reactor capex/life,
    + a `size_storage` strategy fn. **Lease-vs-ownership and at-sea-charge become pricing/sizing
    strategies on this axis** (the mobile tender and leased reactor are early instances).
  Composed by a `Case` namedtuple registry in a new `cases.py` (one row per case).

- **`carried_teu` generalizes** to `carried(demand, capacity)` — *identical arithmetic*, but the
  kWh→capacity-unit mapping is platform-specific: container displaces **volume** (TEU via
  `kwh_per_teu`); bulk/chemical displace **deadweight** (tonnes via `pack_wh_per_kg`). The mass
  constraint added on the flat model is the same idea — the refactor makes it the primary binding
  metric on tonne·km platforms.

- **Config → nested YAML** (`platforms:` / `drivetrains:` / `sources:`), with a section-validating
  loader preserving the strict reject-unknown-keys behaviour per section (string allow-list for
  `name`/`kind`/`cargo_unit`, numeric coercion otherwise).

- **Overhead & O&M are per-(platform,drivetrain) cells** — keep drivetrain defaults + a per-case
  override map in `cases.py` (a nuclear bulk carrier's O&M ≠ a nuclear container ship's).

- **Mixed cargo units** → plots/crossover **facet by platform** (can't share a TEU·km / tonne·km
  axis); `crossover_dmax` asserts same `cargo_unit`. Dict keys generalize `teukm→unitkm`,
  `battery_*→storage_*`, add `cargo_unit`.

- **Migration order (runnable + parity-gated each step):** snapshot baseline stdout → add axis
  dataclasses + nested loader *alongside* the flat `Params` (adapter) → write `levelized_cost` +
  `cases.py` for the container cases → parity-test vs old `lcot_*` on a v×d grid (match to ~1e-9;
  **no formula tweaks during the refactor**) → switch `analysis.py`/`report.py` to consume `Case`
  → delete the flat-`Params` shim → restructure config → then add bulk/chemical platforms (data only).

**Bulk/chemical economics** (DWT, hull capex, load factors, port economics, cargo-value notes) were
deliberately left as placeholders — research them when the platforms are actually populated.
