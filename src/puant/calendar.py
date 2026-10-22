"""Turkish calendar features: official and religious holidays, their eves, bridge days.

Demand drops by a fifth or more on Ramazan and Kurban Bayramı, and the afternoon before each
(arife) is an official half day. The ``holidays`` package supplies the dates, including the
lunar ones; the half days are added here.
"""

from __future__ import annotations

from datetime import date, timedelta

import holidays
import numpy as np
import pandas as pd

HALF_DAYS_FIXED = {(10, 28)}  # the afternoon before Republic Day


def holiday_table(years: range) -> pd.DataFrame:
    """One row per day: holiday, religious holiday, half day (afternoon off), bridge day."""
    tr = holidays.Turkey(years=list(years))
    days = pd.date_range(date(years.start, 1, 1), date(years.stop - 1, 12, 31), freq="D")
    rows = []
    for d in days:
        name = tr.get(d.date(), "")
        religious = "Bayram" in name and ("Ramazan" in name or "Kurban" in name)
        rows.append({"day": d.date(), "holiday": bool(name), "religious": religious, "name": name})
    df = pd.DataFrame(rows).set_index("day")
    nxt = df["religious"].shift(-1, fill_value=False) & ~df["religious"]
    df["half_day"] = nxt | np.array([(d.month, d.day) in HALF_DAYS_FIXED for d in df.index])
    # a lone working day between a holiday and a weekend is often taken off
    wd = np.array([d.weekday() for d in df.index])
    off = df["holiday"].to_numpy() | (wd >= 5)
    prev_off = np.roll(off, 1)
    next_off = np.roll(off, -1)
    df["bridge"] = ~off & prev_off & next_off
    return df


def features(index: pd.DatetimeIndex, table: pd.DataFrame) -> pd.DataFrame:
    """Hourly calendar features for a Turkish-time index."""
    day = pd.Index(index.date)
    t = table.reindex(day)
    hour = index.hour
    f = pd.DataFrame(index=index)
    f["hour"] = hour
    f["dow"] = index.dayofweek
    f["month"] = index.month
    f["doy_sin"] = np.sin(2 * np.pi * index.dayofyear / 365.25)
    f["doy_cos"] = np.cos(2 * np.pi * index.dayofyear / 365.25)
    f["holiday"] = t["holiday"].to_numpy(bool)
    f["religious"] = t["religious"].to_numpy(bool)
    f["half_day_pm"] = t["half_day"].to_numpy(bool) & (hour >= 13)
    f["bridge"] = t["bridge"].to_numpy(bool)
    # days into a religious holiday matter: the middle days are the quietest
    rel = table["religious"]
    run = rel.groupby((~rel).cumsum()).cumsum()
    f["religious_day"] = run.reindex(day).to_numpy()
    return f


def days_to(table: pd.DataFrame, day: date, col: str = "religious") -> int:
    """Days until the next day flagged ``col`` (for the dashboard's calendar note)."""
    for k in range(400):
        d = day + timedelta(days=k)
        if d in table.index and table.at[d, col]:
            return k
    return -1
