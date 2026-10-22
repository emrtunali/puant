"""Local store of EPİAŞ and weather history as parquet files, updated incrementally.

    python -m puant.data            # fetch everything missing up to today

Each series lives in ``data/raw/<name>.parquet`` with an hourly Turkish-time index. A rerun only
asks for the days after the last stored hour, so the daily job stays small.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import weather
from .epias import Epias, EpiasError

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
START = date(2019, 1, 1)
# weather costs Open-Meteo quota per location and fortnight; 2021 on is plenty for training
WEATHER_START = date(2021, 1, 1)
DAYAHEAD_START = date(2024, 6, 1)  # previous-runs forecasts are complete from here


def series(e: Epias) -> dict[str, tuple[Callable, int]]:
    """name -> (fetch(start, end), days the series runs ahead of today)."""
    return {
        "price": (e.mcp, 1),  # tomorrow's prices are published around 14:00
        "consumption": (e.consumption, 0),
        "load_plan": (e.load_plan, 1),
        "generation": (e.generation, 0),
        "wind": (e.wind_forecast, 1),
        "gas": (e.gas_price, 1),
    }


def path(name: str) -> Path:
    return RAW / f"{name}.parquet"


def load(name: str) -> pd.DataFrame:
    return pd.read_parquet(path(name))


def _last_day(name: str) -> date | None:
    p = path(name)
    if not p.exists():
        return None
    df = pd.read_parquet(p, columns=[])
    return df.index.max().date() if len(df) else None


def update(names: list[str] | None = None, until: date | None = None, log=print) -> dict:
    """Fetch what is missing for each series; returns rows added per series."""
    RAW.mkdir(parents=True, exist_ok=True)
    e = Epias()
    today = until or date.today()
    added = {}
    for name, (fetch, ahead) in series(e).items():
        if names and name not in names:
            continue
        last = _last_day(name)
        # refetch the last stored day: its final hours may have been missing or provisional
        start = last if last else START
        end = today + timedelta(days=ahead)
        if start > end:
            added[name] = 0
            continue
        t0 = time.time()
        try:
            new = fetch(start, end)
        except EpiasError as err:  # tomorrow not published yet (SEF1124): stop at today
            if "SEF1124" not in str(err) or end <= today:
                raise
            end = today
            new = fetch(start, end)
        old = load(name) if last else None
        df = new if old is None else pd.concat([old[old.index.date < start], new]).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df.to_parquet(path(name))
        added[name] = len(df) - (0 if old is None else len(old))
        took = time.time() - t0
        log(f"  {name}: {start}..{end}, {len(new):,} fetched, {len(df):,} stored ({took:.0f} s)")
    return added


def update_weather(
    kind: str = "archive",
    start: date | None = None,
    end: date | None = None,
    chunk_days: int = 90,
    log=print,
) -> None:
    """Reanalysis weather for training (``archive``) or day-ahead forecasts for backtests
    (``dayahead``), stored as ``weather_<kind>.parquet``."""
    name = f"weather_{kind}"
    last = _last_day(name)
    start = (last + timedelta(days=1)) if last else (start or WEATHER_START)
    end = end or (date.today() - timedelta(days=6 if kind == "archive" else 0))
    s = start
    while s <= end:
        e = min(s + timedelta(days=chunk_days - 1), end)
        try:
            new = weather.fetch(kind, s, e)
        except weather.QuotaExceeded:
            log(f"  {name}: daily weather quota used up at {s}; rerun tomorrow to continue")
            return
        # save every chunk, so a run cut short by the quota resumes where it stopped
        df = pd.concat([load(name), new]) if path(name).exists() else new
        df.sort_index()[~df.sort_index().index.duplicated(keep="last")].to_parquet(path(name))
        log(f"  {name}: {s}..{e}")
        s = e + timedelta(days=1)
        time.sleep(30)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--weather", action="store_true")
    a = ap.parse_args()
    if a.only != []:
        update(a.only)
    if a.weather:  # day-ahead first: the honest backtest needs it most
        update_weather("dayahead", start=DAYAHEAD_START)
        update_weather("archive")
