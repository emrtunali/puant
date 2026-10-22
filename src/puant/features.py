"""Features for a day-ahead forecast, built only from what is known at the time.

The forecast for day D+1 is made on the morning of day D (before the market closes at 12:30):

* prices: day D's prices are known (published on D-1 around 14:00), D+1's are what we forecast;
* consumption and generation: known up to the morning of D, so lags start at D-1;
* TEİAŞ's load plan and EPİAŞ's wind forecast for D+1 are published before the market closes;
* weather: the forecast for D+1 (in training, the reanalysis stands in for it);
* gas: the reference price of day D.

Prices in lira have risen several-fold since 2019, so the price target and price features are
expressed relative to the mean price of the 28 days up to D (``ref``); forecasts are scaled back.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import calendar as cal

H = 24
WEATHER = ["temp", "hdd", "cdd", "cloud", "wind_speed", "wind_index", "solar_ghi"]


def master(raw: dict[str, pd.DataFrame], weather: pd.DataFrame) -> pd.DataFrame:
    """One hourly table with every raw series (Turkish time)."""
    idx = raw["price"].index
    m = pd.DataFrame(index=idx)
    m["price"] = raw["price"]["price"]
    m["consumption"] = raw["consumption"]["consumption"].reindex(idx)
    m["lep"] = raw["load_plan"]["lep"].reindex(idx)
    g = raw["generation"].reindex(idx)
    m["wind_gen"] = g.get("wind")
    m["sun_gen"] = g.get("sun")
    m["hydro_gen"] = g.get("dammedHydro")
    m["river_gen"] = g.get("river")
    m["gas_gen"] = g.get("naturalGas")
    m["total_gen"] = g.get("total")
    w = raw["wind"].resample("h").mean().reindex(idx)  # stored at 10-minute resolution
    m["wind_fc_official"] = w.get("forecast")
    gas = raw["gas"]["grfTl"]
    gas_day = pd.Series(gas.to_numpy(), index=pd.Index(gas.index.date))
    m["gas_price"] = pd.Series(idx.date, index=idx).map(gas_day).astype(float)
    wx = weather.reindex(idx)
    for c in WEATHER:
        m[c] = wx[c]
    return m


def _shift_days(s: pd.Series, days: int) -> pd.Series:
    return s.shift(days * H)


def build(m: pd.DataFrame, holidays: pd.DataFrame) -> pd.DataFrame:
    """Feature rows for every hour, as if each day were forecast on the morning before."""
    f = cal.features(m.index, holidays)
    day = pd.Series(m.index.date, index=m.index)
    daily_price = m["price"].groupby(day).mean()
    # reference level: mean price of the 28 days up to and including D (= the day before)
    ref_daily = daily_price.rolling(28, min_periods=7).mean()
    ref_daily_max = m["price"].groupby(day).max().rolling(28, min_periods=7).max()
    prev = pd.Series(m.index.date, index=m.index) - pd.Timedelta(days=1)
    f["ref"] = prev.map(ref_daily).to_numpy()
    f["cap_proxy"] = prev.map(ref_daily_max).to_numpy() / f["ref"]
    for lag in (1, 2, 7):
        f[f"price_d{lag}"] = _shift_days(m["price"], lag) / f["ref"]
    f["price_d1_mean"] = prev.map(daily_price).to_numpy() / f["ref"]
    f["price_d1_max"] = prev.map(m["price"].groupby(day).max()).to_numpy() / f["ref"]
    f["price_d1_min"] = prev.map(m["price"].groupby(day).min()).to_numpy() / f["ref"]
    for lag in (2, 7):
        f[f"cons_d{lag}"] = _shift_days(m["consumption"], lag)
    f["lep"] = m["lep"]
    f["lep_vs_d7"] = m["lep"] / f["cons_d7"]
    f["wind_fc_official"] = m["wind_fc_official"]
    for c in WEATHER:
        f[c] = m[c]
    f["temp_day"] = m["temp"].groupby(day).transform("mean")
    f["wind_index_day"] = m["wind_index"].groupby(day).transform("mean")
    # residual load: what thermal and hydro plants must cover
    solar_proxy = m["solar_ghi"] * (
        _shift_days(m["sun_gen"], 7) / _shift_days(m["solar_ghi"], 7).clip(lower=50)
    ).clip(0, 50)
    f["residual_load"] = m["lep"] - m["wind_fc_official"].fillna(0) - solar_proxy.fillna(0)
    # hydro availability and gas cost
    f["hydro_week"] = _shift_days(m["hydro_gen"].rolling(7 * H, min_periods=H).mean(), 2)
    f["river_week"] = _shift_days(m["river_gen"].rolling(7 * H, min_periods=H).mean(), 2)
    f["gas_price"] = _shift_days(m["gas_price"], 1) / f["ref"]
    # Wind and solar fleets grow every month and trees cannot extrapolate, so those models
    # forecast output per unit of weather; the recent yield (MW per unit, up to D-1) scales back.
    f["solar_k"] = _yield(m["sun_gen"], m["solar_ghi"], 14)
    f["wind_k"] = _yield(m["wind_gen"], m["wind_index"], 30)
    f["wind_fc_ratio"] = m["wind_fc_official"] / f["wind_k"]
    return f


def _yield(gen: pd.Series, driver: pd.Series, days: int) -> pd.Series:
    ok = gen.notna() & driver.notna()
    g = gen.where(ok).rolling(days * H, min_periods=days * H // 2).sum()
    d = driver.where(ok).rolling(days * H, min_periods=days * H // 2).sum()
    return _shift_days(g / d.where(d > 0), 2)


TARGETS = {
    "price": lambda m, f: m["price"] / f["ref"],
    "consumption": lambda m, f: m["consumption"],
    "wind": lambda m, f: m["wind_gen"] / f["wind_k"],
    "solar": lambda m, f: m["sun_gen"] / f["solar_k"],
}
SCALE = {"price": "ref", "wind": "wind_k", "solar": "solar_k"}  # multiply a forecast back
ACTUAL = {"price": "price", "consumption": "consumption", "wind": "wind_gen", "solar": "sun_gen"}

# features each model may use (the price model sees everything)
COLUMNS = {
    "consumption": [
        "hour",
        "dow",
        "month",
        "doy_sin",
        "doy_cos",
        "holiday",
        "religious",
        "half_day_pm",
        "bridge",
        "religious_day",
        "cons_d2",
        "cons_d7",
        "temp",
        "hdd",
        "cdd",
        "cloud",
        "temp_day",
        "solar_ghi",
        "lep",
        "lep_vs_d7",
    ],
    "wind": [
        "hour",
        "month",
        "doy_sin",
        "doy_cos",
        "wind_speed",
        "wind_index",
        "wind_index_day",
        "temp",
        "wind_fc_ratio",
    ],
    "solar": ["hour", "month", "doy_sin", "doy_cos", "solar_ghi", "cloud", "temp"],
}


def columns(target: str, f: pd.DataFrame) -> list[str]:
    if target in COLUMNS:
        return COLUMNS[target]
    return [c for c in f.columns if c not in ("ref", "solar_k", "wind_k")]


def daily_rows(m: pd.DataFrame) -> np.ndarray:
    """Days with all 24 prices (a complete target)."""
    ok = m["price"].groupby(m.index.date).count() == H
    return np.array(ok[ok].index)
