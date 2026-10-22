"""Run the honest backtest and write a scoreboard.

    python -m puant.backtest [--start 2024-07-01]

Every row uses the weather forecast issued the day before (Open-Meteo previous runs, from mid
2024; reanalysis before), so the scores are what the live system would have achieved, and the
models learn how far a weather forecast can be off.
Writes ``data/backtest.parquet`` (hourly forecasts) and ``data/scoreboard.json``.
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from . import calendar as cal
from . import data
from .features import build, master
from .models import backtest, baselines, metrics

SERIES = ["price", "consumption", "load_plan", "generation", "wind", "gas"]


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = {n: data.load(n) for n in SERIES}
    # the day-ahead forecast where it exists, reanalysis before that
    wx = data.load("weather_dayahead").combine_first(data.load("weather_archive"))
    hol = cal.holiday_table(range(data.START.year, pd.Timestamp.now().year + 2))
    m = master(raw, wx)
    return m, build(m, hol)


def scoreboard(bt: pd.DataFrame, m: pd.DataFrame, f: pd.DataFrame) -> dict:
    out = {}
    for t, df in bt.groupby("target"):
        df = df.dropna(subset=["actual"]).set_index("time")
        row = {"puant": metrics(df.reset_index()).to_dict()}
        for name, s in baselines(m, f, t).items():
            b = df.assign(q50=s.reindex(df.index).to_numpy()).dropna(subset=["q50"])
            e = (b["q50"] - b["actual"]).abs()
            row[name] = {"mae": float(e.mean()), "n_hours": len(b)}
        out[t] = row
    return out


def main(start: str, end: str | None) -> None:
    m, f = load_inputs()
    bt = backtest(m, f, f, start, end)
    bt.to_parquet(data.ROOT / "data" / "backtest.parquet")
    board = scoreboard(bt, m, f)
    (data.ROOT / "data" / "scoreboard.json").write_text(json.dumps(board, indent=1))
    for t, row in board.items():
        p = row["puant"]
        print(f"\n{t}: MAE {p['mae']:.1f}  WAPE {p['wape']:.1f} %  80 % band holds "
              f"{p['coverage_80']:.0%} of hours")  # fmt: skip
        for name, b in row.items():
            if name != "puant":
                print(f"   vs {name}: MAE {b['mae']:.1f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--end")
    a = ap.parse_args()
    main(a.start, a.end)
