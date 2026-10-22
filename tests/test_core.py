from datetime import date

import numpy as np
import pandas as pd
import pytest

from puant import calendar as cal
from puant.battery import Battery, plan, settle
from puant.epias import chunks, iso
from puant.features import WEATHER, build, master
from puant.market import clear
from puant.models import calibrate

TZ = "Europe/Istanbul"


def test_chunks_cover_range_without_overlap():
    parts = list(chunks(date(2024, 1, 1), date(2024, 12, 31), 90))
    assert parts[0][0] == date(2024, 1, 1) and parts[-1][1] == date(2024, 12, 31)
    for (_, e), (s, _) in zip(parts, parts[1:], strict=False):
        assert (s - e).days == 1
    assert all((e - s).days < 90 for s, e in parts)


def test_iso_is_turkish_time():
    assert iso(date(2025, 3, 1)) == "2025-03-01T00:00:00+03:00"


def test_bayram_and_arife():
    t = cal.holiday_table(range(2025, 2026))
    assert t.loc[date(2025, 3, 30), "religious"]  # Ramazan Bayramı, day 1
    assert t.loc[date(2025, 3, 29), "half_day"]  # arife
    assert not t.loc[date(2025, 4, 2), "holiday"]


def test_battery_buys_low_sells_high():
    prices = np.array([100] * 6 + [50] * 6 + [300] * 6 + [80] * 6, float)
    b = Battery()
    move = plan(prices, b)
    assert abs(move.sum()) < 1e-9  # starts and ends empty
    assert move[6:12].sum() == pytest.approx(10)  # fills up in the cheap block
    assert move[12:18].sum() == pytest.approx(-10)  # empties in the expensive block
    eff = np.sqrt(0.9)
    assert settle(move, prices, b) == pytest.approx(10 * eff * 300 - 10 / eff * 50)


def test_battery_idle_on_flat_prices():
    assert np.allclose(plan(np.full(24, 1000.0)), 0)


def test_market_clearing_and_cap():
    supply = pd.DataFrame({"price": [0, 1000, 2000, 3000], "mw": [10000, 20000, 25000, 26000]})
    demand = pd.DataFrame({"price": [0, 1000, 2000, 3000], "mw": [30000, 26000, 22000, 21000]})
    p, q = clear(supply, demand)
    assert 1000 < p < 2000 and 20000 < q < 25000
    # more wind (supply shifted right) clears cheaper
    assert clear(supply, demand, supply_shift=3000)[0] < p
    # demand above all supply even at the cap: the cap clears
    assert clear(supply, demand, demand_shift=20000)[0] == 3000


def test_calibrate_restores_coverage():
    rng = np.random.default_rng(0)
    actual = rng.normal(0, 2, 24 * 90)
    past = pd.DataFrame({"q10": -1.0, "q50": 0.0, "q90": 1.0, "actual": actual})  # too narrow
    new = pd.DataFrame({"q10": [-1.0], "q50": [0.0], "q90": [1.0]})
    out = calibrate(new, past)
    width = out["q90"].iloc[0]
    assert width == pytest.approx(1.2816 * 2, rel=0.1)  # the true 90th percentile of N(0, 2)


def _toy_raw(days=60):
    idx = pd.date_range("2025-01-01", periods=days * 24, freq="h", tz=TZ)
    rng = np.random.default_rng(1)
    h = idx.hour.to_numpy()
    price = pd.DataFrame(
        {"price": 2000 + 500 * np.sin(h / 24 * 2 * np.pi) + rng.normal(0, 50, len(idx))}, index=idx
    )
    sources = ["wind", "sun", "dammedHydro", "river", "naturalGas", "lignite", "importCoal"]
    gen = pd.DataFrame({c: rng.uniform(100, 5000, len(idx)) for c in sources}, index=idx)
    gen["total"] = gen.sum(axis=1)
    raw = {
        "price": price,
        "consumption": pd.DataFrame(
            {"consumption": rng.uniform(30000, 45000, len(idx))}, index=idx
        ),
        "load_plan": pd.DataFrame({"lep": rng.uniform(30000, 45000, len(idx))}, index=idx),
        "generation": gen,
        "wind": pd.DataFrame({"generation": gen["wind"], "forecast": gen["wind"] * 1.1}, index=idx),
        "gas": pd.DataFrame({"grfTl": 19000.0}, index=idx[::24]),
    }
    wx = pd.DataFrame(rng.uniform(0, 30, (len(idx), len(WEATHER))), index=idx, columns=WEATHER)
    return raw, wx


def test_features_never_see_the_future():
    """The features of a forecast day may not depend on that day's own prices, consumption or
    generation (or anything later): rewrite them all from the start of the day and compare."""
    raw, wx = _toy_raw()
    hol = cal.holiday_table(range(2025, 2026))
    f1 = build(master(raw, wx), hol)
    cut = pd.Timestamp("2025-02-10", tz=TZ)
    raw2 = {k: v.copy() for k, v in raw.items()}
    for k in ("price", "consumption", "generation"):
        num = raw2[k].select_dtypes("number").columns
        raw2[k].loc[raw2[k].index >= cut, num] *= 7
    f2 = build(master(raw2, wx), hol)
    day = (f1.index >= cut) & (f1.index < cut + pd.Timedelta(days=1))
    pd.testing.assert_frame_equal(f1[day], f2[day])
