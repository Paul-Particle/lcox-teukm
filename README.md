# lcox-teukm

A Tier-1 techno-economic model for container shipping decarbonization. It computes the
**levelized cost of transport (LCOT)**, in US cents per TEU·km, for a container ship and
compares two powertrains:

- **Fossil** — conventional VLSFO-burning two-stroke.
- **Battery-electric** — with containerized battery swapping at port calls.

## What it answers

The comparison axis is **`D_max`** — the longest hop between swap-capable ports (km). This
distance sets the required battery size, which drives both CAPEX and the cargo slots
displaced by batteries. Everything that scales the two ships together (load factor, port
time, route geometry) is held at representative values so the model reads **absolute LCOT**,
not just a ratio. Speed is optimized independently for each ship, since the electric ship
has an extra incentive to slow down (less energy/km → smaller battery → fewer displaced
slots + less CAPEX).

The script reports:

- Energy cost per useful kWh, fossil vs. electric.
- An LCOT breakdown (fixed vs. energy share, cargo capacity, battery size/life) at sample
  hop lengths.
- The **crossover `D_max`** below which electric is cheaper.
- A sensitivity table of crossover `D_max` vs. battery cost and electricity price.
- An interactive Plotly plot of LCOT vs. `D_max` saved to `results/lcot_vs_dmax.html`.

## Project layout

```
.
├── config.yaml           # all model inputs — edit here to run scenarios
├── scripts/
│   ├── run.py            # entry point (run this): loads config, orchestrates
│   ├── units.py          # unit conversions — single source of truth
│   ├── params.py         # Params schema + load_params(config.yaml)
│   ├── finance.py        # capital recovery factor
│   ├── energy.py         # ship physics: power, leg energy, cycles/year
│   ├── lcot.py           # the two cost models (fossil, electric)
│   ├── analysis.py       # speed optimization + crossover distance
│   └── report.py         # console tables + plotting
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

> Note: this is a first-draft Tier-1 cut intended for order-of-magnitude comparison, not a
> detailed naval-architecture or financial model.

## License

Released under the [MIT License](LICENSE).
