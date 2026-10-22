"""Day-ahead market simulator on EPİAŞ's published supply and demand curves.

For every hour EPİAŞ publishes the aggregate curves of the auction: how much would be offered
(supply) and bid (demand) at each price. Their crossing is the clearing price. Shifting a curve
answers market questions directly from real bids: 2,000 MW less wind moves the supply curve
left by 2,000 MW at every price; a heatwave moves the demand curve right.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from .epias import Epias


def curves(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split EPİAŞ chart rows into supply (price ↑, amount ↑) and demand (price ↑, amount ↓)."""
    s = raw.dropna(subset=["supplyPrice"])[["supplyPrice", "amount"]]
    d = raw.dropna(subset=["demandPrice"])[["demandPrice", "amount"]]
    s = s.set_axis(["price", "mw"], axis=1).sort_values("price").drop_duplicates("price")
    d = d.set_axis(["price", "mw"], axis=1).sort_values("price").drop_duplicates("price")
    return s.reset_index(drop=True), d.reset_index(drop=True)


def clear(supply: pd.DataFrame, demand: pd.DataFrame, supply_shift: float = 0.0,
          demand_shift: float = 0.0) -> tuple[float, float]:  # fmt: skip
    """Clearing (price, MW) after shifting the curves sideways by the given MW."""
    grid = np.union1d(supply["price"], demand["price"])
    s = np.interp(grid, supply["price"], supply["mw"]) + supply_shift
    d = np.interp(grid, demand["price"], demand["mw"]) + demand_shift
    gap = d - s  # positive while demand exceeds supply
    if not (gap <= 0).any():  # demand exceeds supply even at the cap: the cap clears
        return float(grid[-1]), float(s[-1])
    k = int(np.argmax(gap <= 0))
    if k == 0 or gap[k] == gap[k - 1]:
        return float(grid[k]), float(s[k])
    # linear crossing between the two grid prices
    t = gap[k - 1] / (gap[k - 1] - gap[k])
    p = grid[k - 1] + t * (grid[k] - grid[k - 1])
    return float(p), float(s[k - 1] + t * (s[k] - s[k - 1]))


def sensitivity(supply: pd.DataFrame, demand: pd.DataFrame,
                shifts=(-3000, -2000, -1000, 0, 1000, 2000, 3000)) -> pd.DataFrame:  # fmt: skip
    """Clearing price when supply (e.g. wind) or demand shifts by each amount."""
    rows = []
    for x in shifts:
        rows.append({
            "shift_mw": x,
            "price_supply_shift": clear(supply, demand, supply_shift=x)[0],
            "price_demand_shift": clear(supply, demand, demand_shift=x)[0],
        })  # fmt: skip
    return pd.DataFrame(rows)


def fetch_hour(e: Epias, when: datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    return curves(e.supply_demand_curve(when))
