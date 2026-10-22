"""Why tomorrow's price is what it is, and what if the weather or gas were different.

``attribute`` compares tomorrow with a *normal* day (the average of the last 28 days at each hour)
and splits the gap in forecast price among groups of drivers with exact Shapley values: every
group is swapped between normal and tomorrow in all 2^n combinations, so the parts add up to
the whole gap and no group gets credit for another's effect.

``scenarios`` reruns the price model with the weather and gas shifted: the counterfactual
"how much cheaper would tomorrow be with 2 °C warmer weather / 30 % more wind / 20 % cheaper gas".
"""

from __future__ import annotations

from itertools import combinations
from math import factorial

import numpy as np
import pandas as pd

from .features import H
from .models import Model

GROUPS = {
    "wind": ["wind_fc_official", "wind_fc_ratio", "wind_speed", "wind_index", "wind_index_day"],
    "demand": ["lep", "lep_vs_d7", "cons_d2", "cons_d7", "temp", "hdd", "cdd", "temp_day"],
    "solar": ["solar_ghi", "cloud"],
    "gas": ["gas_price"],
    "hydro": ["hydro_week", "river_week"],
    "calendar": ["dow", "holiday", "religious", "half_day_pm", "bridge", "religious_day"],
    "recent prices": ["price_d1", "price_d2", "price_d7", "price_d1_mean", "price_d1_max",
                      "price_d1_min"],
}  # fmt: skip
LABELS = {
    "wind": "Rüzgâr",
    "demand": "Talep ve sıcaklık",
    "solar": "Güneş",
    "gas": "Doğalgaz fiyatı",
    "hydro": "Hidro",
    "calendar": "Takvim",
    "recent prices": "Son günlerin fiyatı",
}


def normal_day(f: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """Each feature's mean at the same hour over the 28 days before ``day``."""
    past = f[(f.index < day) & (f.index >= day - pd.Timedelta(days=28))]
    mean = past.groupby(past.index.hour).mean(numeric_only=True)
    tomorrow = f[(f.index >= day) & (f.index < day + pd.Timedelta(days=1))]
    out = mean.reindex(tomorrow.index.hour)
    out.index = tomorrow.index
    return out


def attribute(model: Model, f_day: pd.DataFrame, normal: pd.DataFrame) -> pd.DataFrame:
    """Hourly contribution of each group (in model units, before scaling back), plus the
    ``normal`` and ``forecast`` levels. Rows: hours; columns: groups."""
    groups = [g for g in GROUPS if any(c in model.cols for c in GROUPS[g])]
    n = len(groups)
    # one prediction per subset of groups taken from tomorrow (the rest from normal)
    subsets = [s for r in range(n + 1) for s in combinations(range(n), r)]
    frames = []
    for s in subsets:
        x = normal.copy()
        for i in s:
            cols = [c for c in GROUPS[groups[i]] if c in x]
            x[cols] = f_day[cols].to_numpy()
        # everything not in a group (hour, month, references) is tomorrow's
        rest = [c for c in model.cols if not any(c in GROUPS[g] for g in groups)]
        x[rest] = f_day[rest].to_numpy()
        if "residual_load" in model.cols:
            x["residual_load"] = (
                x["lep"] - x["wind_fc_official"].fillna(0)
                - (f_day["lep"] - f_day["wind_fc_official"].fillna(0) - f_day["residual_load"])
            )  # fmt: skip
        frames.append(x)
    big = pd.concat(frames)
    pred = model.models[0.5].predict(big[model.cols].to_numpy(float)).reshape(len(subsets), -1)
    value = {s: pred[k] for k, s in enumerate(subsets)}
    phi = np.zeros((n, len(f_day)))
    for i in range(n):
        for s in subsets:
            if i in s:
                continue
            w = factorial(len(s)) * factorial(n - len(s) - 1) / factorial(n)
            phi[i] += w * (value[tuple(sorted((*s, i)))] - value[s])
    out = pd.DataFrame(phi.T, index=f_day.index, columns=groups)
    out["normal"] = value[()]
    out["forecast"] = value[tuple(range(n))]
    return out


def scenarios(model: Model, cons: Model, f_day: pd.DataFrame, temps=(-4, -2, 0, 2, 4),
              winds=(0.5, 0.75, 1.0, 1.25, 1.5), gas=(0.8, 1.0, 1.2)) -> pd.DataFrame:  # fmt: skip
    """Hourly median price forecast (model units) on a grid of temperature shifts (°C), wind
    multipliers and gas multipliers. A temperature shift also moves the load plan by what the
    consumption model expects the weather to add or remove."""
    base_cons = cons.models[0.5].predict(f_day[cons.cols].to_numpy(float))
    rows, frames = [], []
    for dt in temps:
        x0 = f_day.copy()
        x0["temp"] = f_day["temp"] + dt
        x0["temp_day"] = f_day["temp_day"] + dt
        x0["hdd"] = np.maximum(18 - x0["temp"], 0)
        x0["cdd"] = np.maximum(x0["temp"] - 22, 0)
        extra = cons.models[0.5].predict(x0[cons.cols].to_numpy(float)) - base_cons
        x0["lep"] = f_day["lep"] + extra
        x0["lep_vs_d7"] = x0["lep"] / f_day["cons_d7"]
        x0["residual_load"] = f_day["residual_load"] + extra
        for wm in winds:
            for gm in gas:
                x = x0.copy()
                for c in ("wind_fc_official", "wind_fc_ratio", "wind_index", "wind_index_day"):
                    x[c] = f_day[c] * wm
                x["wind_speed"] = f_day["wind_speed"] * wm ** (1 / 3)
                x["residual_load"] = x0["residual_load"] - f_day["wind_fc_official"] * (wm - 1)
                x["gas_price"] = f_day["gas_price"] * gm
                frames.append(x)
                rows.append({"temp_shift": dt, "wind_mult": wm, "gas_mult": gm})
    big = pd.concat(frames)
    pred = model.models[0.5].predict(big[model.cols].to_numpy(float)).reshape(len(rows), H)
    out = pd.DataFrame(rows)
    out["hourly"] = list(pred)
    return out
