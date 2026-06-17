"""units.py — every unit-conversion factor, and only here; nothing else hard-codes one.

Base units: energy kWh, power kW, time h, distance km, speed kn, mass kg, money US$.
Naming: `B_PER_A` multiplies an A-quantity to give B (and divides B back to A), so usage
reads as dimensional cancellation, e.g. `v_kn * KMH_PER_KNOT -> km/h`.
"""

KMH_PER_KNOT = 1.852          # knot -> km/h (exact)
KM_PER_NM = 1.852             # nautical mile -> km (a distance; distinct from KMH_PER_KNOT though equal)
KG_PER_TONNE = 1000.0
KWH_PER_MWH = 1000.0
WH_PER_KWH = 1000.0
HOURS_PER_YEAR = 8760.0       # 365 d x 24 h
CENTS_PER_USD = 100.0
PERCENT_PER_FRACTION = 100.0
