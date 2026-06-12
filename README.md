# lcox-teukm

A Tier-1 techno-economic model for container shipping decarbonization. It computes the
**levelized cost of transport (LCOT)**, in US cents per TEU·km, for a container ship and
compares several powertrains:

- **Fossil** — conventional VLSFO-burning two-stroke.
- **E-methanol** — the same two-stroke (dual-fuel) burning synthetic e-methanol: identical ship,
  only the fuel and its (much higher) price differ. The price is a **placeholder**
  (`efuel_usd_per_kwh`) from the `supply.efuel_chemical` stub pending an
  electrolyzer+DAC+synthesis production model; the optimizer slow-steams it harder than fossil
  because the costly fuel rewards it.
- **Battery-electric (LFP)** — with containerized battery swapping at port calls.
- **Battery-electric (iron-air)** — same swap concept with a Form-Energy-class 100-hour
  chemistry: very cheap per kWh but ~45% round-trip efficient and power-limited (the pack
  must be sized for peak draw × its 100 h discharge rating, not just leg energy), which
  pins its optimal speed near the minimum.
- **Nuclear (onboard SMR, direct-drive)** — reactor CAPEX per kW, cheap HALEU fuel, high fixed
  O&M; no D_max-driven sizing, so its LCOT depends on D_max only through port-call frequency.
- **Nuclear-electric (containerized / leased / integrated)** — onboard reactor driving an electric
  motor (reactor → electricity → propeller). Slightly lower end-to-end efficiency than direct-drive,
  but earns the electric-drive hull/prop gains; modular containerized units vs a single integrated
  plant. The **leased** variant is the same containerized hardware as a reactor-as-a-service: the
  reactor's CAPEX is recovered through a per-kWh rate levelized over the reactor's own *pool*
  utilization, so the ship isn't charged for the reactor sitting idle during its port calls — a win
  that's large on short hops (lots of reclaimed port-idle) and negligible on long ones.
- **Mobile-reactor charge** — a battery-electric ship recharged **at sea** by a dedicated uncrewed
  nuclear tender instead of port swaps. The ship runs untethered on battery through coastal waters,
  then cables up to the tender at the regulatory border and crosses the open ocean tethered (the
  tender drives propulsion *and* recharges the coastal drain). The pack only covers the worst
  untethered stretch — the coastal transit or a storm-survival disconnect — so it is far smaller
  than a port-swap battery ship; energy is priced at the tender's levelized $/kWh, and speed while
  tethered is capped by the floating charging cable.

Cargo accounting applies **volume (slots), mass (deadweight) and power** constraints together, and
supports **asymmetric headhaul/backhaul legs** (`load_factor_imbalance`).

## What it answers

The comparison axis is **`D_max`** — the longest hop between swap-capable ports (km). This
distance sets the required battery size, which drives both CAPEX and the cargo slots
displaced by batteries. Everything that scales the ships together (load factor, port
time, route geometry) is held at representative values so the model reads **absolute LCOT**,
not just a ratio. Speed is optimized independently for each ship: the battery ships have an
extra incentive to slow down (less energy/km → smaller battery → fewer displaced slots +
less CAPEX), while the nuclear ship's cheap fuel and expensive capital push it to maximum
speed.

The script reports:

- Energy cost per useful kWh for every powertrain.
- An LCOT breakdown (fixed vs. energy share, cargo capacity, battery size/life) at sample
  hop lengths.
- The **crossover `D_max`** below which each battery ship is cheaper than fossil.
- A sensitivity table of LFP crossover `D_max` vs. battery cost and electricity price.
- Interactive Plotly plots (LCOT vs. `D_max`, optimal speed vs. `D_max`, a sensitivity
  tornado, and a technology/cargo-capacity comparison) saved to `results/`.

## Project layout

```
.
├── config.yaml           # all model inputs — edit here to run scenarios
├── scripts/
│   ├── run.py            # entry point (run this): loads config, orchestrates
│   ├── units.py          # unit conversions — single source of truth
│   ├── params.py         # Params schema + load_params(config.yaml)
│   ├── finance.py        # capital recovery factor
│   ├── energy.py         # ship physics: power, leg energy, legs/year
│   ├── sizing.py         # shared sizing & economics primitives (cargo accounting,
│   │                     #   battery/reactor/tender sizing & pricing)
│   ├── cases.py          # the 3 axes (Platform × Drivetrain × EnergySource) + case registry
│   ├── cost.py           # levelized_cost(case, p, v, d): one entry point for every case
│   ├── analysis.py       # speed optimization + crossover distance
│   ├── report.py         # console tables
│   ├── plots.py          # Plotly figures
│   └── regression_check.py  # golden-output regression test (vs golden_output.txt)
├── results/              # generated plots (gitignored)
├── pyproject.toml        # project + dependencies (numpy, plotly, pyyaml)
├── uv.lock               # pinned dependency versions
└── LICENSE               # MIT
```

The model is split along its natural seams: parameters, units, physics,
finance, the cost models, analysis, and reporting each live in their own
module, so a change to (say) battery sizing or output formatting is localized
to one file.

## Setup & running

This project uses [uv](https://docs.astral.sh/uv/) to manage Python and
dependencies. Dependencies are declared in `pyproject.toml` and pinned in
`uv.lock`; uv also provisions the right Python version (3.11+), so no separate
Python install is required.

```bash
# 1. Install uv (skip if you already have it)
#    macOS / Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh
#    or with Homebrew: brew install uv
#    Windows (PowerShell): irm https://astral.sh/uv/install.ps1 | iex

# 2. Clone the repo
git clone https://github.com/Paul-Particle/lcox-teukm.git
cd lcox-teukm

# 3. Install dependencies into a local .venv (first run only)
uv sync

# 4. Run the model
uv run scripts/run.py
```

That's the whole setup — `uv sync` creates an isolated `.venv` with the pinned
dependencies, and `uv run` executes against it without you needing to activate
anything.

Results print to stdout; the interactive figure is written to
`results/lcot_vs_dmax.html` (a self-contained file — open it in any browser),
alongside a static `results/lcot_vs_dmax.png` for slides/papers. The `results/`
directory is generated on each run and is gitignored, so a fresh clone has none
until you run the model.

## Assumptions & key parameters

All model inputs live in **`config.yaml`** — hull size, load factor, CAPEX, efficiencies,
energy prices, and battery characteristics. Edit that file to explore scenarios; values are
validated on load against the `Params` schema in `scripts/params.py` (an unknown or
non-numeric key is rejected rather than silently ignored). Units: energy in kWh, power in
kW, time in hours, distance in km, speed in knots, mass in kg, money in US$.

Case-specific caveats:

- **Iron-air**: the deadweight (mass) constraint *is* enforced — iron-air is roughly 4× heavier
  per kWh than LFP at system level, so it is mass-limited at most ranges and infeasible long-haul.
  Cost and density values are based on announced targets (Form Energy), not delivered systems.
- **Nuclear**: refueling and regulatory outages are assumed inside the shared
  `availability`; incremental O&M (specialized crew, security, bespoke insurance) is the
  least-quantified input in the literature, and reactor CAPEX spans $750–8,000/kW depending
  on assumed fleet-scale learning — the base case uses a near-term $6,000/kW.

> Note: this is a first-draft Tier-1 cut intended for order-of-magnitude comparison, not a
> detailed naval-architecture or financial model.

## Concept notes

Two of the cases rest on operational concepts that aren't yet commercial; this is how the model
treats them. The reactor in both is an **AMPERA-class** micro-reactor (thorium TRISO, subcritical,
sCO₂ cycle ~50% thermal→electric, ~30 MWe net per two-core module in a footprint of two 40-ft
containers plus shielding on all sides ≈ 36 TEU; refuels only every few decades).

### Mobile nuclear tender (dedicated escort)

An uncrewed nuclear tender recharges a battery-electric ship **at sea** rather than at port:

1. The ship leaves port and sails untethered on battery through coastal/territorial waters
   (`coastal_untethered_distance_nm`).
2. At the regulatory border it meets the tender, which has just dropped its previous companion.
3. They establish a power cable and cross the open ocean together; the tender supplies continuous
   power to drive propulsion *and* recharge the battery, so the ship arrives at the far border fully
   charged for its inbound coastal run. While tethered, ship speed is capped (`mob_cable_v_cap_kn`)
   well below its free design speed, leaving ample bus headroom for the charging load.
4. In severe sea states the cable is disconnected and the ship rides out the storm on battery
   (`storm_survival_duration_h`).

Modeling consequences: the battery is sized for the worst **untethered** stretch —
`max(coastal transit energy, storm-survival energy)` — not the whole crossing, so it is far smaller
than a port-swap pack. The tender is priced as a service: its annualized cost (hull + reactor CAPEX
+ O&M + fuel, including parasitic and cable losses) is levelized over the bus energy it pushes across
the cable per year, where one escort occupies the crossing plus `tender_idle_h` waiting at the border.
A feasibility check enforces that net reactor power (after parasitics) covers the tethered bus draw
through `cable_efficiency`. The `ships/tender` ratio (≈1) is a face-validity diagnostic only; it does
not feed back into LCOT. `coastal_untethered_distance_nm` defaults to the 12 nm UNCLOS territorial-sea
minimum (Freedom-of-Navigation); set it to ~200 nm to test a full-EEZ regulatory standoff.

### Leased containerized reactor (reactor-as-a-service)

The containerized nuclear-electric ship can either **own** its reactor modules (amortized over their
life on the ship's balance sheet) or **lease** them from a shared fleet pool:

1. The ship loads one or more reactor modules at port just before heading to sea.
2. The reactor powers the electric drivetrain across the ocean.
3. On arrival the module is removed and returned to a shared pool, where it may wait
   (`nucc_pool_idle_h`) before being loaded onto the next departing ship.

Modeling consequences: the leased and owned cases are physically identical (same drivetrain, same slot
overhead while a module is aboard). The only difference is financial — under the lease the reactor's
CAPEX is recovered through a per-kWh service rate levelized over the reactor's **own** pool utilization
(`nucc_pool_availability`, `nucc_pool_idle_h`), not one ship's duty cycle. Because a pooled reactor is
not idle during the ship's port calls (it is powering another ship), its fixed cost spreads over more
operating hours, so leasing is cheaper than owning by roughly the reclaimed port-idle fraction — a large
win on short hops, negligible on long ones. The `ships/reactor` ratio (>1 ⇒ one reactor serves several
ships) is a diagnostic only. Caveats: the lease recovers reactor CAPEX + fuel only (the model has no
separate reactor-O&M line — it sits in the ship's non-crew residual, kept ship-side), and nuclear-
specialist crew is not bundled into the lease (`crew_count_nuclear` is the ship's whole complement).

## Glossary

Maritime / model terms used in the code and outputs:

- **LCOT** — levelized cost of transport: total annualized cost ÷ annual cargo·distance (here, US¢ per TEU·km). The headline metric.
- **TEU** — twenty-foot equivalent unit; one standard container "slot." Hull capacity and battery containers are counted in TEU.
- **D_max** — the longest hop between battery-swap-capable ports (km); the comparison axis. Sets battery size, hence CAPEX and displaced cargo.
- **Headhaul / backhaul** — the two directions of a round trip. Trade is directionally imbalanced (a full headhaul, a lighter backhaul), captured by `load_factor_imbalance`.
- **Load factor** — average fraction of available cargo slots actually filled (≈0.8); reflects trade imbalance, demand variability, weight-vs-volume, empty repositioning.
- **Deadweight (DWT)** — the mass a ship can carry (cargo + fuel + stores). `deadweight_t` is the cargo+energy mass budget; batteries/bunkers eat into it (the mass constraint).
- **Reefer** — refrigerated container; draws power continuously, the large/variable part of hotel load.
- **Hotel load** — non-propulsion electrical load (reefers, accommodation, ship systems).
- **Slow steaming** — sailing below design speed to cut fuel (power ∝ speed³); the basis for optimizing cruise speed per ship.
- **Service / design speed & sea margin** — the speed the plant is sized for, plus a power margin (~15%) for weather/fouling. Here installed power is sized at `v_design_max`.
- **Sea margin** — extra installed power reserve for real-world weather/hull-fouling losses vs calm-water trials.
- **Propulsion (power) factor** — fractional reduction in propulsion power at a given speed from hull form, anti-fouling coatings, propeller/pods, wider motor-efficiency, and weather routing (the electric-drive stack `_elec_propulsion_factor`; `fossil_propulsion_factor` for fossil). Broader than just the propeller.
- **Pods / azimuth thrusters** — steerable electric propulsion units; better low-speed maneuverability (faster berthing, fewer tugs) and freedom to use larger, more efficient propellers.
- **Admiralty (cube) law** — propulsion power scales as speed³ (`prop_power_kw`).
- **DoD** — depth of discharge: the routine usable fraction of battery capacity; deeper discharge is an emergency-only buffer.
- **RTE** — round-trip efficiency: energy out ÷ energy in for a battery (here `eta_charge × eta_discharge`).
- **C-rate / C/50** — discharge rate relative to capacity; C/50 means full discharge over 50 h (iron-air is power-limited).
- **Battery swapping** — exchanging depleted containerized battery packs for charged ones at port, rather than plugging in.
- **Tender** — a support vessel; here the mobile nuclear reactor that recharges battery ships at sea.
- **EEZ** — Exclusive Economic Zone, to ~200 nm offshore. The mobile tender stays clear of a regulatory standoff (`coastal_untethered_distance_nm`: the 12 nm UNCLOS territorial-sea minimum by default, or the full ~200 nm EEZ as a conservative test), so the ship crosses that coastal band untethered on battery before meeting the tender.
- **nm / knot** — nautical mile (1.852 km) / one nautical mile per hour.
- **VLSFO** — very low sulfur fuel oil, the conventional marine fuel.
- **SMR / HALEU** — small modular reactor; high-assay low-enriched uranium fuel.

## License

Released under the [MIT License](LICENSE).
