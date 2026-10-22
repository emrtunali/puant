"""What a grid battery would have earned trading on Puant's forecasts.

Each morning the battery plans tomorrow's charging and discharging on the *forecast* prices
(dynamic programming over its state of charge), then is paid the *actual* prices. Comparing
that with the perfect-foresight plan shows how much of the possible value the forecast captures.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Battery:
    energy_mwh: float = 10.0
    power_mw: float = 5.0
    round_trip: float = 0.90
    step_mwh: float = 0.5  # state-of-charge grid
    cycle_cost: float = 0.0  # TRY per MWh discharged (wear); 0 = ignore

    @property
    def levels(self) -> np.ndarray:
        return np.arange(0, self.energy_mwh + 1e-9, self.step_mwh)


DEFAULT = Battery()


def plan(prices: np.ndarray, b: Battery = DEFAULT) -> np.ndarray:
    """Hourly energy moved into the battery (+ charge, − discharge, MWh at the battery) that
    maximises revenue at ``prices``, starting and ending empty."""
    lv = b.levels
    n, k = len(prices), len(lv)
    eff = np.sqrt(b.round_trip)  # losses split between charging and discharging
    delta = lv[None, :] - lv[:, None]  # [from, to] change in stored energy
    feasible = np.abs(delta) <= b.power_mw + 1e-9
    grid = np.where(delta > 0, delta / eff, delta * eff)  # energy bought (+) or sold (−)
    wear = np.where(delta < 0, -delta * eff * b.cycle_cost, 0)
    value = np.full(k, -np.inf)
    value[0] = 0.0
    choice = np.zeros((n, k), int)
    for t in range(n):
        gain = value[:, None] - grid * prices[t] - wear
        gain[~feasible] = -np.inf
        choice[t] = gain.argmax(0)
        value = gain.max(0)
    # walk back from an empty battery
    s, path = 0, [0]
    for t in range(n - 1, -1, -1):
        s = choice[t, s]
        path.append(s)
    soc = lv[path[::-1]]
    return np.diff(soc)


def settle(move: np.ndarray, prices: np.ndarray, b: Battery = DEFAULT) -> float:
    eff = np.sqrt(b.round_trip)
    grid = np.where(move > 0, move / eff, move * eff)
    wear = np.where(move < 0, -move * eff * b.cycle_cost, 0).sum()
    return float(-(grid * prices).sum() - wear)


def backtest(bt: pd.DataFrame, b: Battery = DEFAULT) -> pd.DataFrame:
    """Daily revenue for plans on the median forecast vs perfect foresight vs a fixed
    rule (charge 10–15, discharge 18–22), from the price rows of a backtest frame."""
    p = bt[bt["target"] == "price"].set_index("time").sort_index()
    rows = []
    fixed = np.zeros(24)
    fixed[10:12], fixed[18:20] = b.power_mw, -b.power_mw
    for day, d in p.groupby(p.index.date):
        if len(d) != 24 or d["actual"].isna().any():
            continue
        actual = d["actual"].to_numpy()
        rows.append({
            "day": day,
            "forecast": settle(plan(d["q50"].to_numpy(), b), actual, b),
            "perfect": settle(plan(actual, b), actual, b),
            "fixed_rule": settle(fixed, actual, b),
        })  # fmt: skip
    out = pd.DataFrame(rows).set_index("day")
    return out
