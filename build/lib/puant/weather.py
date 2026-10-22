"""Weather that drives Turkish electricity, aggregated the way the grid feels it.

* Temperature weighted by population: demand follows where people live, not the country's centre.
* Wind at 100 m (hub height) and solar irradiance weighted by installed capacity at the wind and
  solar farms mapped in OpenStreetMap (``data/sites.json``, built by ``scripts/build_sites.py``).

Source: Open-Meteo (no key). Models are trained on reanalysis (the weather that happened), but
backtests use the forecasts as they stood the day before (Open-Meteo's previous-runs archive,
complete from mid-2024): a day-ahead forecast cannot know tomorrow's weather, only its forecast.
"""

from __future__ import annotations

import json
import time
from datetime import date
from importlib import resources

import numpy as np
import pandas as pd
import requests

TZ = "Europe/Istanbul"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"  # reanalysis (what happened)
FORECAST = "https://api.open-meteo.com/v1/forecast"
PREVIOUS = "https://previous-runs-api.open-meteo.com/v1/forecast"  # forecasts made N days earlier
VARS = ["temperature_2m", "wind_speed_100m", "shortwave_radiation", "cloud_cover"]

# provinces with ~70 % of Turkey's population; TÜİK 2024, millions, rounded
PROVINCES = {
    "İstanbul": (41.01, 28.97, 15.7),
    "Ankara": (39.93, 32.86, 5.9),
    "İzmir": (38.42, 27.14, 4.5),
    "Bursa": (40.19, 29.06, 3.2),
    "Antalya": (36.89, 30.71, 2.7),
    "Konya": (37.87, 32.48, 2.3),
    "Adana": (37.00, 35.32, 2.3),
    "Şanlıurfa": (37.16, 38.79, 2.2),
    "Gaziantep": (37.07, 37.38, 2.2),
    "Kocaeli": (40.77, 29.94, 2.1),
    "Mersin": (36.80, 34.63, 1.9),
    "Diyarbakır": (37.91, 40.24, 1.8),
    "Hatay": (36.20, 36.16, 1.5),
    "Manisa": (38.61, 27.43, 1.5),
    "Kayseri": (38.73, 35.48, 1.4),
    "Samsun": (41.29, 36.33, 1.4),
    "Balıkesir": (39.65, 27.88, 1.3),
    "Aydın": (37.85, 27.84, 1.2),
    "Tekirdağ": (40.98, 27.51, 1.2),
    "Sakarya": (40.78, 30.40, 1.1),
    "Kahramanmaraş": (37.58, 36.93, 1.1),
    "Van": (38.50, 43.38, 1.1),
    "Denizli": (37.78, 29.09, 1.1),
    "Muğla": (37.22, 28.36, 1.0),
    "Eskişehir": (39.78, 30.52, 0.9),
    "Trabzon": (41.00, 39.72, 0.8),
}


class QuotaExceeded(RuntimeError):
    """Open-Meteo's daily request allowance is used up; retry tomorrow."""


def load_sites() -> dict:
    return json.loads(resources.files("puant").joinpath("data/sites.json").read_text())


def _get(
    url: str,
    lat,
    lon,
    start: date | None,
    end: date | None,
    extra: dict,
    session=None,
    suffix: str = "",
) -> list[dict]:
    params = {
        "latitude": ",".join(f"{x:.3f}" for x in lat),
        "longitude": ",".join(f"{x:.3f}" for x in lon),
        "hourly": ",".join(v + suffix for v in VARS),
        "timezone": TZ,
        "wind_speed_unit": "ms",
        **extra,
    }
    if start:
        params |= {"start_date": start.isoformat(), "end_date": end.isoformat()}
    for attempt in range(6):
        try:
            r = (session or requests).get(url, params=params, timeout=120)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 5:
                raise
            time.sleep(30)
            continue
        if r.status_code != 429:
            break
        # the free tier allows 600 weighted calls a minute, 5,000 an hour and 10,000 a day
        reason = r.json().get("reason", "") if r.content else ""
        if "Daily" in reason:
            raise QuotaExceeded(reason)
        time.sleep(65 if "Minutely" in reason else 600)
    r.raise_for_status()
    d = r.json()
    d = d if isinstance(d, list) else [d]
    if suffix:
        for p in d:
            p["hourly"] = {k.removesuffix(suffix): v for k, v in p["hourly"].items()}
    return d


def frames(payload: list[dict]) -> list[pd.DataFrame]:
    out = []
    for p in payload:
        h = p["hourly"]
        idx = pd.DatetimeIndex(pd.to_datetime(h["time"])).tz_localize(
            TZ, ambiguous="NaT", nonexistent="NaT"
        )
        out.append(pd.DataFrame({v: h[v] for v in VARS}, index=idx))
    return out


def weighted(dfs: list[pd.DataFrame], weights: np.ndarray, col: str) -> pd.Series:
    w = np.asarray(weights, float) / np.sum(weights)
    m = np.column_stack([d[col].to_numpy(float) for d in dfs])
    ok = np.isfinite(m)
    return pd.Series(
        (np.where(ok, m, 0) * w).sum(1) / np.maximum((ok * w).sum(1), 1e-9), index=dfs[0].index
    )


def wind_power_index(speed: np.ndarray) -> np.ndarray:
    """Share of rated output of a generic modern turbine at a hub-height wind speed (m/s):
    cut-in 3, rated 12, cut-out 25, cubic in between. Averaging this over farms tracks
    wind generation far better than averaging wind speed."""
    s = np.asarray(speed, float)
    p = np.clip((s**3 - 27) / (12**3 - 27), 0, 1)
    return np.where(s >= 25, 0.0, p)


def fetch(
    kind: str, start: date | None = None, end: date | None = None, days: int = 3, session=None
) -> pd.DataFrame:
    """Hourly drivers: population-weighted temperature (+ heating/cooling degrees),
    capacity-weighted wind power index and irradiance.

    kind: "archive" (reanalysis), "dayahead" (the forecast issued the day before, for backtests)
    or "forecast" (the next ``days``, live)."""
    url = {"archive": ARCHIVE, "dayahead": PREVIOUS, "forecast": FORECAST}[kind]
    extra = {"forecast_days": days} if kind == "forecast" else {}
    suffix = "_previous_day1" if kind == "dayahead" else ""
    sites = load_sites()
    prov = list(PROVINCES.values())
    pf = frames(
        _get(url, [p[0] for p in prov], [p[1] for p in prov], start, end, extra, session, suffix)
    )
    wf = frames(
        _get(
            url,
            [s["lat"] for s in sites["wind"]],
            [s["lon"] for s in sites["wind"]],
            start,
            end,
            extra,
            session,
            suffix,
        )
    )
    sf = frames(
        _get(
            url,
            [s["lat"] for s in sites["solar"]],
            [s["lon"] for s in sites["solar"]],
            start,
            end,
            extra,
            session,
            suffix,
        )
    )
    out = pd.DataFrame(index=pf[0].index)
    out["temp"] = weighted(pf, [p[2] for p in prov], "temperature_2m")
    out["hdd"] = np.maximum(18 - out["temp"], 0)
    out["cdd"] = np.maximum(out["temp"] - 22, 0)
    out["cloud"] = weighted(pf, [p[2] for p in prov], "cloud_cover")
    for d in wf:
        d["wpi"] = wind_power_index(d["wind_speed_100m"])
    out["wind_speed"] = weighted(wf, [s["mw"] for s in sites["wind"]], "wind_speed_100m")
    out["wind_index"] = weighted(wf, [s["mw"] for s in sites["wind"]], "wpi")
    out["solar_ghi"] = weighted(sf, [s["mw"] for s in sites["solar"]], "shortwave_radiation")
    return out[out.index.notna()]
