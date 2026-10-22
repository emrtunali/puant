"""Tomorrow's forecast, explained, as JSON for the dashboard.

    python -m puant.live [--no-update]

Run on the morning of day D (before the 12:30 market close): updates the data, trains on every
day up to D, forecasts D+1 with calibrated bands, explains the price, runs the scenario grid
and the market simulator, plans a battery, and writes ``site/data/*.json``. Each forecast is
also kept in ``data/forecasts/`` so that the live scoreboard can grade it once D+1 is over.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import numpy as np
import pandas as pd

from . import calendar as cal
from . import data, weather
from .backtest import SERIES
from .battery import DEFAULT as BATTERY
from .battery import backtest as battery_backtest
from .battery import plan, settle
from .carbon import best_hours
from .epias import TZ, Epias
from .explain import LABELS, attribute, normal_day, scenarios
from .features import ACTUAL, H, build, master
from .market import fetch_hour, sensitivity
from .models import baselines, calibrate, fit_all, scale_back

SITE = data.ROOT / "site" / "data"
LOG = data.ROOT / "data" / "forecasts"


def _r(x, nd=1):
    """Round for JSON; NaN becomes null."""
    a = np.round(np.asarray(x, float), nd)
    return [None if not np.isfinite(v) else float(v) for v in a]


def inputs(today: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    raw = {n: data.load(n) for n in SERIES}
    prices = raw["price"]
    # forecast the first day without published prices (normally tomorrow)
    full = prices["price"].groupby(prices.index.date).count()
    day = pd.Timestamp(max(d for d, c in full.items() if c == H), tz=TZ) + pd.Timedelta(days=1)
    idx = pd.date_range(prices.index[0], day + pd.Timedelta(hours=H - 1), freq="h", tz=TZ)
    raw["price"] = prices.reindex(idx)
    live = weather.fetch("forecast", days=3)
    wx = live.combine_first(data.load("weather_dayahead")).combine_first(
        data.load("weather_archive")
    )
    hol = cal.holiday_table(range(data.START.year, today.year + 2))
    m = master(raw, wx)
    return m, build(m, hol), day


def calibration_history(target: str) -> pd.DataFrame | None:
    """Raw out-of-sample forecasts of the last 90 days: backtest, then logged live runs."""
    p = data.ROOT / "data" / "backtest.parquet"
    if not p.exists():
        return None
    bt = pd.read_parquet(p)
    bt = bt[bt["target"] == target]
    if "q10_raw" not in bt:
        return None
    past = bt[["q50", "actual"]].assign(q10=bt["q10_raw"], q90=bt["q90_raw"])
    return past.dropna().iloc[-90 * H :]


def market_block(e: Epias, day: pd.Timestamp, hour: int) -> dict | None:
    """Supply and demand curves of the latest published auction at ``hour``."""
    # an hour that has not happened yet counts as "future" to EPİAŞ: fall back to past auctions
    for d in (day - pd.Timedelta(days=k) for k in range(4)):
        try:
            s, dm = fetch_hour(e, datetime(d.year, d.month, d.day, hour, tzinfo=d.tzinfo))
        except Exception:  # noqa: BLE001 - the simulator is optional
            continue
        if len(s) and len(dm):
            sens = sensitivity(s, dm)
            # ~120 points per curve, always keeping both ends (the cap matters)
            thin = lambda c: c.iloc[sorted({*range(0, len(c), max(1, len(c) // 120)), len(c) - 1})]  # noqa: E731
            return {
                "date": str(d.date()),
                "hour": hour,
                "supply": {"price": _r(thin(s)["price"]), "mw": _r(thin(s)["mw"], 0)},
                "demand": {"price": _r(thin(dm)["price"]), "mw": _r(thin(dm)["mw"], 0)},
                "shift_mw": sens["shift_mw"].tolist(),
                "price_supply_shift": _r(sens["price_supply_shift"]),
                "price_demand_shift": _r(sens["price_demand_shift"]),
            }
    return None


def forecast(m: pd.DataFrame, f: pd.DataFrame, day: pd.Timestamp) -> dict:
    sel = (f.index >= day) & (f.index < day + pd.Timedelta(days=1))
    f_day = f[sel]
    models = fit_all(m, f, day)
    out: dict = {"day": str(day.date()), "made": datetime.now().astimezone().isoformat()}
    preds = {}
    for t, mod in models.items():
        p = scale_back(t, mod.predict(f_day), f_day)
        p.columns = ["q10", "q50", "q90"]
        p = calibrate(p, calibration_history(t))
        preds[t] = p
        out[t] = {"q10": _r(p["q10"]), "q50": _r(p["q50"]), "q90": _r(p["q90"])}
    # benchmarks for the same day, so the dashboard shows what we are up against
    out["benchmarks"] = {
        t: {name: _r(s.reindex(f_day.index)) for name, s in baselines(m, f, t).items()}
        for t in models
    }
    ref = float(f_day["ref"].iloc[0])
    att = attribute(models["price"], f_day, normal_day(f, day)) * ref
    groups = [c for c in att.columns if c not in ("normal", "forecast")]
    out["explain"] = {
        "normal": round(float(att["normal"].mean()), 1),
        "forecast": round(float(att["forecast"].mean()), 1),
        "ref": round(ref, 1),
        "groups": [
            {
                "key": g,
                "label": LABELS[g],
                "daily": round(float(att[g].mean()), 1),
                "hourly": _r(att[g]),
            }  # fmt: skip
            for g in sorted(groups, key=lambda g: -abs(att[g].mean()))
        ],
    }
    sc = scenarios(models["price"], models["consumption"], f_day)
    out["scenarios"] = [
        {
            "temp": r.temp_shift,
            "wind": r.wind_mult,
            "gas": r.gas_mult,
            "mean": round(float(np.mean(r.hourly) * ref), 1),
            "hourly": _r(r.hourly * ref),
        }
        for r in sc.itertuples()
    ]
    q50 = preds["price"]["q50"].to_numpy()
    move = plan(q50, BATTERY)
    out["battery"] = {
        "energy_mwh": BATTERY.energy_mwh, "power_mw": BATTERY.power_mw,
        "round_trip": BATTERY.round_trip, "move": _r(move, 2),
        "expected_tl": round(settle(move, q50, BATTERY)),
    }  # fmt: skip
    out["best_hours"] = best_hours(
        pd.Series(q50, index=f_day.index), preds["carbon"]["q50"].reset_index(drop=True)
        .set_axis(f_day.index)
    )  # fmt: skip
    out["inputs"] = {
        "temp": _r(f_day["temp"]), "wind_fc_official": _r(f_day["wind_fc_official"], 0),
        "lep": _r(f_day["lep"], 0), "solar_ghi": _r(f_day["solar_ghi"], 0),
        "gas_price": round(float(f_day["gas_price"].iloc[0] * ref), 1),
        "holiday": cal_name(day),
    }  # fmt: skip
    peak = int(np.argmax(q50))
    out["market"] = market_block(Epias(), day, peak)
    return out


def cal_name(day: pd.Timestamp) -> str:
    t = cal.holiday_table(range(day.year, day.year + 1))
    return str(t.at[day.date(), "name"]) if day.date() in t.index else ""


def history(m: pd.DataFrame, f: pd.DataFrame, days: int = 60) -> dict:
    """Recent forecasts against what happened: the backtest, then the live log on top."""
    bt = pd.read_parquet(data.ROOT / "data" / "backtest.parquet")
    for p in sorted(LOG.glob("*.json")):
        j = json.loads(p.read_text())
        idx = pd.date_range(j["day"], periods=H, freq="h", tz=TZ)
        # only forecasts made before the market closed (12:30 the day before) count as live
        if pd.Timestamp(j["made"]).tz_convert(TZ) > idx[0] - pd.Timedelta(hours=11, minutes=30):
            continue
        for t in ACTUAL:
            if t in j:
                live = pd.DataFrame({k: j[t][k] for k in ("q10", "q50", "q90")}, index=idx)
                live = live.assign(target=t, live=True).rename_axis("time").reset_index()
                bt = pd.concat([bt[~((bt["target"] == t) & bt["time"].isin(idx))], live])
    out = {}
    for t, df in bt.groupby("target"):
        df = df.set_index("time").sort_index()
        df["actual"] = m[ACTUAL[t]].reindex(df.index).to_numpy()
        recent = df[df.index >= df.index.max() - pd.Timedelta(days=days)]
        done = df.dropna(subset=["actual"])
        monthly = done.groupby(done.index.tz_localize(None).to_period("M"))
        bench = baselines(m, f, t)
        out[t] = {
            "time": [x.isoformat() for x in recent.index],
            "q10": _r(recent["q10"]), "q50": _r(recent["q50"]), "q90": _r(recent["q90"]),
            "actual": _r(recent["actual"]),
            "live": recent.get("live", pd.Series(False, index=recent.index)).fillna(False)
            .astype(bool).tolist(),
            "monthly": [
                {"month": str(k), "puant": round(float((g["q50"] - g["actual"]).abs().mean()), 1),
                 **{n: round(float((s.reindex(g.index) - g["actual"]).abs().mean()), 1)
                    for n, s in bench.items()}}
                for k, g in monthly
            ],
        }  # fmt: skip
    return out


def main(update: bool = True) -> None:
    today = pd.Timestamp.now(tz=TZ).normalize()
    if update:
        data.update()
        data.update_weather("dayahead", start=data.DAYAHEAD_START)
    m, f, day = inputs(today)
    fc = forecast(m, f, day)
    SITE.mkdir(parents=True, exist_ok=True)
    LOG.mkdir(parents=True, exist_ok=True)
    (SITE / "forecast.json").write_text(json.dumps(fc, ensure_ascii=False))
    (LOG / f"{fc['day']}.json").write_text(
        json.dumps({k: fc[k] for k in ("day", "made", *ACTUAL)}, ensure_ascii=False)
    )
    (SITE / "history.json").write_text(json.dumps(history(m, f), ensure_ascii=False))
    bat = battery_backtest(pd.read_parquet(data.ROOT / "data" / "backtest.parquet"))
    cum = bat.cumsum()
    (SITE / "battery.json").write_text(json.dumps({
        "day": [str(d) for d in cum.index], **{c: _r(cum[c], 0) for c in cum},
        "total": {c: round(float(bat[c].sum())) for c in bat},
    }))  # fmt: skip
    for name in ("scoreboard.json",):
        src = data.ROOT / "data" / name
        if src.exists():
            (SITE / name).write_text(src.read_text())
    e = fc["explain"]
    print(f"{fc['day']}: mean price {np.mean(fc['price']['q50']):.0f} TL/MWh "
          f"(normal {e['normal']:.0f}); biggest driver {e['groups'][0]['label']} "
          f"{e['groups'][0]['daily']:+.0f}")  # fmt: skip


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true")
    main(update=not ap.parse_args().no_update)
