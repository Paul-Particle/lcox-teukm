# Grounding the config in EU MRV fleet data

A reality check on a few config values, and the empirical basis for a future **ship scale
factor** (a single knob that co-varies a group of parameters within a narrow, realistic band
so a parametric/Sobol sweep can't conjure impossible ships — a 3000-TEU hull with a 100 MW
plant, or a feeder steaming at 25 kn).

**Scope.** Analysis + design only — this branch changes **no** config values and **no** code
paths. Numbers below are produced by `scripts/mrv_fleet.py`; re-run it to refresh them.

## Source & method

EU MRV (THETIS-MRV) annual emissions reports, 2018–2024, container subset:
**13,857 ship-years** pooled from `data/*.xlsx` (gitignored — download from
<https://mrv.emsa.europa.eu/#public/emission-report>).

The public file is thin on raw quantities, so three anchors are **derived**, not read:

| quantity | how | caveat |
|---|---|---|
| distance | `total fuel ÷ fuel-per-distance` | no distance column is published |
| operating speed | `distance ÷ time-at-sea` | annual average across laden + ballast legs |
| operating useful power | `fuel/distance × speed × LHV × drive-eff` (VLSFO 11.1 kWh/kg, η 0.48) | **propulsion + hotel** — ~1.5–2 MW above pure propulsion |
| cargo carried | `fuel/distance ÷ fuel-per-transport-work(mass)` | cargo *moved* (size × utilization), **not nameplate DWT** — a noisy size proxy |

The blind earlier draft assumed a "distance travelled" column, a nameplate-DWT column, and a
"fuel per time at sea" column. The first two don't exist in the public file and the third is
present-but-empty; the rebuilt utility avoids all three by deriving the quantities above, and
matches every column by fuzzy keyword (headers drift: 2018–2023 have 62 columns and prefix
"Annual average …"; 2024 has 113 and drops the prefix).

## Fleet distributions (container, n ≈ 13.5k)

| | p10 | p25 | median | p75 | p90 |
|---|---:|---:|---:|---:|---:|
| operating speed (kn) | 9.8 | 11.9 | **13.7** | 15.3 | 16.5 |
| operating useful power (kW, prop+hotel) | 4,600 | 6,300 | **12,100** | 20,000 | 25,400 |
| energy intensity (kWh-fuel/km) | 476 | 612 | **1,004** | 1,534 | 1,853 |
| cargo carried (t) | 4,900 | 9,700 | **27,800** | 70,500 | 115,000 |
| technical efficiency (gCO₂/t·nm) | 7.8 | 10.7 | **16.4** | 21.2 | 28.1 |

## Per-value grounding

### Propulsion — `resistance.p_ref_kw` / `v_ref_kn`  (config: 20,000 kW @ 18 kn, propulsion)

The config 3000-TEU ship carries ≈ 28,800 t (3000 × 12 t/TEU × 0.80 load) — the **51st
percentile** of the fleet, a representative mid-size vessel. Ships in that size band
(25–45k t carried, n = 1,903) run a median **13,805 kW @ 14.2 kn**. Extrapolating to the
config's 18 kn on the same cube law the model itself uses (P ∝ v³) gives **≈ 28,200 kW**
(prop+hotel); a fixed-speed regression across the whole fleet independently lands at
**≈ 30,000 kW @ 18 kn** at 28,800 t. Net of ~1.5 MW hotel, that is **≈ 26–28 MW of
propulsion at 18 kn**.

> **Finding.** `p_ref_kw = 20,000` @ 18 kn looks **~30–40 % low** for this size class —
> it implies an unusually slippery hull or a lower true design speed. Two consistent fixes:
> raise `p_ref_kw` to ≈ **26,000–28,000**, *or* keep 20 MW and drop `v_ref_kn` to ≈ **16 kn**.
> Either way the (p_ref, v_ref) *pair* should land on the fleet curve, not just one of them.
> Caveat: the 14→18 kn cube-law extrapolation roughly doubles power, so the absolute number is
> sensitive — but the model uses the same cube law, so it's internally consistent.

### Speed axes — `route.design_v_kn` / the `op_v_kn` optimize axis  (config: 18 kn; axis 5–22)

Operating speed is a tight bell at **13.7 kn median**, p10–p90 **9.8–16.5**, essentially
nothing above ~18 kn (slow-steaming is universal). `design_v_kn = 18` is fine as a *design*
speed (top of the operating envelope).

> **Finding.** The `op_v_kn` sweep upper bound of **22 kn is unreachable** for a container
> ship — no fleet vessel operates there, so the optimizer wastes its grid (and could pick a
> physically absurd optimum if economics ever favoured it). Tighten the upper bound to
> ≈ **18–19 kn**. The lower bound of 5 kn sits below the observed p10 (9.8) but is harmless as
> a search floor.

### Ship size — `capacity.gross` / `deadweight_t` / `unit_mass_t`  (config: 3000 TEU / 41,000 t / 12 t/TEU)

The derived carried mass (≈ 28,800 t) matching the fleet median (27,800 t) **validates the
size class** — the modeled ship is a credible mid-fleet vessel. MRV grounds the *product*
`gross × unit_mass × load_factor` (what's carried); it does **not** separately pin the split
between `gross`, `unit_mass_t`, and `load_factor`, nor nameplate `deadweight_t` (no DWT column).

> **Finding.** No change indicated for the size class. Treat the gross/unit-mass/load split as
> still hand-set; only their product is fleet-anchored.

## The ship scale factor (design)

**Idea.** One dimensionless `size_scale` `s` (s = 1 → the current 3000-TEU base) multiplies a
*group* of parameters along empirically-fitted exponents, so every sampled ship stays near the
real-ship manifold. MRV gives the exponents that tie size to power and speed.

**Fitted exponents** (log-log, cargo-carried as the size proxy):

| relation | exponent | r | reading |
|---|---:|---:|---|
| operating power vs size (as-run) | **0.49** | 0.89 | strong — bigger ships draw more power *and* steam faster |
| power vs size **at fixed 18 kn** | **0.22** | 0.55 | the pure hull-size effect alone; looser (the size proxy is noisy) |
| operating speed vs size | **0.09** | 0.50 | bigger ships are only marginally faster |

**Proposed co-variation** (s = `size_scale`):

| parameter | scales as | grounded by |
|---|---|---|
| `capacity.gross` (TEU) | `s¹` | definition of s |
| `capacity.deadweight_t` | `s¹` | size ∝ cargo (approx.) |
| `resistance.p_ref_kw` | `s^0.4` | between the fixed-speed 0.22 and as-run 0.49 fits |
| `resistance.v_ref_kn` | `s^0.09` | speed-vs-size fit (nearly flat) |
| `capex.hull_usd` | `s^0.6–0.7` | **not MRV** — cost economics, needs a separate dataset |
| `operations.crew_count` | `s^0` (flat) | crew barely varies with size |

**Narrow band, not the whole fleet.** The fleet's own p25–p75 of carried mass spans
s ≈ 0.34–2.47 (feeders to neo-panamax) — far too wide for "avoid unrealistic ships." For
perturbing *around* the modeled vessel, a tight band such as **s ∈ [0.7, 1.4]** (≈ ±35 %,
all adjacent real size classes) keeps the ship realistic while still exercising the coupling.

**What MRV grounds vs not.** It grounds size ↔ power ↔ speed. It does **not** ground how hull
capex, crew, or O&M scale with size — those exponents need a techno-economic cost dataset (see
the "Tech-data library" item in `TODO.md`) and are flagged above as economics, not fleet data.

## Reproduce

```sh
uv run scripts/mrv_fleet.py            # pools every data/*.xlsx, prints anchors + fits, writes the plot
uv run scripts/mrv_fleet.py --no-plot  # numbers only
```
