"""Carbon intensity of Turkish electricity from the hourly generation mix.

Direct combustion factors in tCO2 per MWh generated (IPCC 2006 fuel factors over typical plant
efficiencies). Turkish geothermal releases CO2 dissolved in the reservoir, far more than the
global average for the technology, hence its non-zero factor. Imports are not counted.
"""

from __future__ import annotations

import pandas as pd

FACTORS = {
    "lignite": 1.10,
    "asphaltiteCoal": 1.05,
    "importCoal": 0.92,
    "blackCoal": 0.95,
    "fueloil": 0.75,
    "naturalGas": 0.40,
    "geothermal": 0.38,
    # hydro, wind, solar, biomass (treated as neutral): 0
}


def intensity(gen: pd.DataFrame) -> pd.Series:
    """Hourly grams of CO2 per kWh generated."""
    emitted = sum(gen[c].fillna(0) * f for c, f in FACTORS.items() if c in gen)
    return (emitted / gen["total"].where(gen["total"] > 0)) * 1000


def best_hours(price: pd.Series, carbon: pd.Series, n: int = 4) -> dict[str, list[int]]:
    """Cheapest, cleanest, and best combined hours of a day (ranks averaged)."""
    rank = price.rank() + carbon.rank()
    return {
        "cheapest": sorted(price.nsmallest(n).index.hour.tolist()),
        "cleanest": sorted(carbon.nsmallest(n).index.hour.tolist()),
        "both": sorted(rank.nsmallest(n).index.hour.tolist()),
    }
