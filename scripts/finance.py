"""
finance.py — financial primitives used to annualize capital costs.
"""


def crf(rate: float, years: float) -> float:
    """Capital recovery factor (annuity): the annual payment that amortizes a
    unit of CAPEX over `years` at discount `rate`."""
    years = max(years, 1e-6)
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)
