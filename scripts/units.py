"""
units.py — single source of truth for unit conversions in the lcox-teukm model.

Every conversion factor between units lives here and ONLY here. If a unit bug
ever appears, it can be found and fixed in this one file. The rest of the
codebase should never hard-code a conversion literal (1.852, 1000, 100, 8760,
...); it imports a named factor from this module instead.

Base units used throughout the model:
    energy    kWh
    power     kW
    time      hours
    distance  km
    speed     knots
    mass      kg
    money     US$

Naming convention: a factor named ``B_PER_A`` converts a quantity expressed in
A into B by MULTIPLYING, and converts B back into A by DIVIDING. Read the usage
as a dimensional cancellation, e.g.::

    km_per_h   = speed_kn * KMH_PER_KNOT          # knots * (km/h)/knot  -> km/h
    usd_per_kg = usd_per_tonne / KG_PER_TONNE     # ($/tonne) / (kg/tonne) -> $/kg
"""

# ---- speed -----------------------------------------------------------------
KMH_PER_KNOT = 1.852        # 1 knot = 1.852 km/h (exact, by definition)

# ---- mass ------------------------------------------------------------------
KG_PER_TONNE = 1000.0       # 1 metric tonne = 1000 kg

# ---- energy ----------------------------------------------------------------
KWH_PER_MWH = 1000.0        # 1 MWh = 1000 kWh
WH_PER_KWH = 1000.0         # 1 kWh = 1000 Wh

# ---- time ------------------------------------------------------------------
HOURS_PER_YEAR = 8760.0     # 365 d * 24 h (calendar year used for utilization)

# ---- money / dimensionless display -----------------------------------------
CENTS_PER_USD = 100.0       # 1 US$ = 100 cents
PERCENT_PER_FRACTION = 100.0  # a fraction (0-1) -> percent (0-100)
