"""Quantile gradient-boosting models and an honest rolling backtest.

One ``HistGradientBoostingRegressor`` per target and quantile (10 %, 50 %, 90 %). The backtest
retrains on the first of every month with all data before it, then forecasts each day of that
month with the weather *forecast* issued the day before; the models never see a later day.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .features import ACTUAL, SCALE, TARGETS, H, columns

QUANTILES = (0.1, 0.5, 0.9)
PARAMS = {
    "max_iter": 400,
    "learning_rate": 0.05,
    "max_leaf_nodes": 48,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
}


@dataclass
class Model:
    target: str
    cols: list[str]
    models: dict = field(default_factory=dict)

    def fit(self, f: pd.DataFrame, y: pd.Series) -> Model:
        ok = y.notna() & f[self.cols].notna().sum(1).gt(len(self.cols) // 2)
        X, yy = f.loc[ok, self.cols].to_numpy(float), y[ok].to_numpy(float)
        for q in QUANTILES:
            self.models[q] = HistGradientBoostingRegressor(
                loss="quantile", quantile=q, random_state=0, **PARAMS
            ).fit(X, yy)
        return self

    def predict(self, f: pd.DataFrame) -> pd.DataFrame:
        X = f[self.cols].to_numpy(float)
        out = pd.DataFrame({q: self.models[q].predict(X) for q in QUANTILES}, index=f.index)
        # quantile models are fitted separately and can cross; sort each row
        out[:] = np.sort(out.to_numpy(), axis=1)
        return out


def scale_back(target: str, pred: pd.DataFrame, f: pd.DataFrame) -> pd.DataFrame:
    out = pred.mul(f[SCALE[target]], axis=0) if target in SCALE else pred.copy()
    if target == "price":
        out = out.clip(upper=(f["cap_proxy"] * f["ref"]).to_numpy()[:, None])
    return out.clip(lower=0.0)


def calibrate(p: pd.DataFrame, past: pd.DataFrame | None, level: float = 0.8) -> pd.DataFrame:
    """Stretch or shrink the band around the median so that it would have held ``level`` of
    the recent out-of-sample hours in ``past`` (split conformal, using only earlier months)."""
    if past is None or len(past) < 30 * H:
        return p
    lo, hi = past["q50"] - past["q10"], past["q90"] - past["q50"]
    best = min(
        np.arange(0.5, 4.01, 0.05),
        key=lambda s: abs(
            ((past["actual"] >= past["q50"] - s * lo) & (past["actual"] <= past["q50"] + s * hi))
            .mean() - level
        ),
    )  # fmt: skip
    out = p.copy()
    out["q10"] = (p["q50"] - best * (p["q50"] - p["q10"])).clip(lower=0)
    out["q90"] = p["q50"] + best * (p["q90"] - p["q50"])
    return out


def fit_all(
    m: pd.DataFrame, f: pd.DataFrame, until: pd.Timestamp, targets=TARGETS
) -> dict[str, Model]:
    train = f.index < until
    return {t: Model(t, columns(t, f)).fit(f[train], TARGETS[t](m, f)[train]) for t in targets}


def backtest(
    m: pd.DataFrame,
    f_train: pd.DataFrame,
    f_test: pd.DataFrame,
    start: str,
    end: str | None = None,
    log=print,
) -> pd.DataFrame:
    """Monthly-retrained forecasts for every hour from ``start``; ``f_test`` carries the
    day-ahead weather. Returns long rows: time, target, q10/q50/q90, actual."""
    idx = f_test.index[(f_test.index >= pd.Timestamp(start, tz=f_test.index.tz))]
    if end:
        idx = idx[idx < pd.Timestamp(end, tz=idx.tz)]
    months = pd.date_range(idx[0].normalize(), idx[-1], freq="MS", tz=idx.tz)
    if len(months) == 0 or months[0] > idx[0]:
        months = months.insert(0, idx[0].normalize())
    raw: dict[str, list[pd.DataFrame]] = {t: [] for t in TARGETS}
    rows = []
    for k, mstart in enumerate(months):
        mend = months[k + 1] if k + 1 < len(months) else idx[-1] + pd.Timedelta(hours=1)
        models = fit_all(m, f_train, mstart)
        sel = (f_test.index >= mstart) & (f_test.index < mend)
        for t, mod in models.items():
            p = scale_back(t, mod.predict(f_test[sel]), f_test[sel])
            p.columns = ["q10", "q50", "q90"]
            p["actual"] = m.loc[sel, ACTUAL[t]].to_numpy()
            # bands are calibrated on the raw forecasts of the last 90 days
            past = pd.concat(raw[t]).iloc[-90 * H :].dropna() if raw[t] else None
            raw[t].append(p)
            rows.append(calibrate(p, past).assign(target=t))
        log(f"  backtest {mstart.date()}: {sel.sum()} hours")
    out = pd.concat(rows)
    out.index.name = "time"
    return out.reset_index()


def metrics(df: pd.DataFrame) -> pd.Series:
    e = df["q50"] - df["actual"]
    inside = (df["actual"] >= df["q10"]) & (df["actual"] <= df["q90"])
    pin = (
        sum(
            np.mean(np.maximum(q * (df["actual"] - df[c]), (q - 1) * (df["actual"] - df[c])))
            for q, c in ((0.1, "q10"), (0.5, "q50"), (0.9, "q90"))
        )
        / 3
    )
    return pd.Series(
        {
            "mae": float(np.mean(np.abs(e))),
            "rmse": float(np.sqrt(np.mean(e**2))),
            # total error over total actual: ordinary MAPE explodes on hours priced near zero
            "wape": float(np.abs(e).sum() / df["actual"].abs().sum()) * 100,
            "coverage_80": float(inside.mean()),
            "pinball": float(pin),
            "n_hours": int(len(df)),
        }
    )


def baselines(m: pd.DataFrame, f: pd.DataFrame, target: str) -> dict[str, pd.Series]:
    """Benchmarks a forecast should beat."""
    if target == "price":
        return {
            "same hour today": m["price"].shift(H),
            "same hour last week": m["price"].shift(7 * H),
        }
    if target == "consumption":
        return {"TEİAŞ load plan": m["lep"], "same hour last week": m["consumption"].shift(7 * H)}
    if target == "wind":
        return {
            "EPİAŞ wind forecast": m["wind_fc_official"],
            "same hour 2 days ago": m["wind_gen"].shift(2 * H),
        }
    return {"same hour last week": m["sun_gen"].shift(7 * H)}
