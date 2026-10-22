"""Client for the EPİAŞ Transparency Platform (Şeffaflık Platformu) electricity services.

Every service needs a ticket-granting ticket (TGT) from EPİAŞ's login server, sent as the ``TGT``
header; a TGT stays valid for two hours. Credentials come from the environment
(``EPIAS_USERNAME``, ``EPIAS_PASSWORD``) and never touch the code or the repository.

Dates are ISO-8601 in Turkish time (``2026-10-20T00:00:00+03:00``); long ranges are split into
chunks so that no single request needs paging.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

import pandas as pd
import requests

BASE = "https://seffaflik.epias.com.tr/electricity-service"
GAS = "https://seffaflik.epias.com.tr/natural-gas-service"
LOGIN = "https://giris.epias.com.tr/cas/v1/tickets"
TZ = "Europe/Istanbul"
TGT_LIFETIME_S = 100 * 60  # documented as 2 h; renew early


class EpiasError(RuntimeError):
    pass


def load_env(path: str = ".env") -> None:
    """Read KEY=VALUE lines from a local .env into the environment (never printed, not in git)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def iso(d: date | datetime, end_of_day: bool = False) -> str:
    """EPİAŞ date string. Turkey has used UTC+3 all year since 2016."""
    if not isinstance(d, datetime):
        d = datetime(d.year, d.month, d.day, 23 if end_of_day else 0)
    return d.strftime("%Y-%m-%dT%H:%M:%S") + "+03:00"


def chunks(start: date, end: date, days: int) -> list[tuple[date, date]]:
    """Inclusive date ranges of at most ``days`` days covering ``start``..``end``."""
    out, s = [], start
    while s <= end:
        e = min(s + timedelta(days=days - 1), end)
        out.append((s, e))
        s = e + timedelta(days=1)
    return out


class Epias:
    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        session=None,
        sleep: Callable[[float], None] = time.sleep,
        pause: float = 2.0,
    ):
        load_env()
        self.username = username or os.environ.get("EPIAS_USERNAME")
        self.password = password or os.environ.get("EPIAS_PASSWORD")
        self.http = session or requests.Session()
        self.sleep = sleep
        self._tgt: str | None = None
        self.pause = pause
        self._last = 0.0
        self._tgt_at = 0.0

    # ------------------------------------------------------------------ auth
    def tgt(self, refresh: bool = False) -> str:
        if self._tgt and not refresh and time.time() - self._tgt_at < TGT_LIFETIME_S:
            return self._tgt
        if not (self.username and self.password):
            raise EpiasError(
                "set EPIAS_USERNAME and EPIAS_PASSWORD (free account at giris.epias.com.tr)"
            )
        r = self.http.post(
            LOGIN,
            data={"username": self.username, "password": self.password},
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/plain"},
            timeout=30,
        )
        if r.status_code != 201 or not r.text.startswith("TGT-"):
            raise EpiasError(f"login failed: HTTP {r.status_code}")
        self._tgt, self._tgt_at = r.text.strip(), time.time()
        return self._tgt

    # ------------------------------------------------------------------ requests
    def post(self, path: str, body: dict, retries: int = 7) -> dict:
        """POST with the TGT header. A 401 renews the ticket; 429 and 5xx back off (honouring
        Retry-After) and retry. Requests are spaced by ``self.pause`` seconds: the platform
        throttles bursts."""
        status, refresh = None, False
        for attempt in range(retries):
            wait = self.pause - (time.time() - self._last)
            if wait > 0:
                self.sleep(wait)
            headers = {"TGT": self.tgt(refresh=refresh), "Accept": "application/json"}
            url = path if path.startswith("http") else BASE + path
            r = self.http.post(url, json=body, headers=headers, timeout=120)
            self._last = time.time()
            status = r.status_code
            if status == 200:
                return r.json()
            if status in (401, 429) or status >= 500:
                refresh = status == 401
                retry_after = r.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else min(5 * 2**attempt, 120)
                self.sleep(delay)
                continue
            raise EpiasError(f"{path}: HTTP {status} {r.text[:300]}")
        raise EpiasError(f"{path}: gave up after {retries} attempts (last HTTP {status})")

    def items(
        self, path: str, start: date, end: date, chunk_days: int = 364, **extra
    ) -> pd.DataFrame:
        """All ``items`` of a date-range service as one frame, chunked to avoid paging."""
        # an end date of 00:00 covers that whole day; a later hour counts as "in the future"
        frames = []
        for s, e in chunks(start, end, chunk_days):
            while True:
                body = {"startDate": iso(s), "endDate": iso(e), **extra}
                try:
                    frames.append(pd.DataFrame(self.post(path, body).get("items", [])))
                    break
                except EpiasError as err:  # SEF1124: the last day is not published yet
                    if "SEF1124" not in str(err) or e <= s:
                        raise
                    e -= timedelta(days=1)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return to_hourly_index(df)

    # ------------------------------------------------------------------ services
    def mcp(self, start: date, end: date) -> pd.DataFrame:
        """Day-ahead market clearing price (PTF), TRY/MWh, hourly."""
        return self.items("/v1/markets/dam/data/mcp", start, end)[["price", "priceUsd", "priceEur"]]

    def consumption(self, start: date, end: date) -> pd.DataFrame:
        """Real-time consumption (MWh per hour)."""
        return self.items("/v1/consumption/data/realtime-consumption", start, end)[["consumption"]]

    def load_plan(self, start: date, end: date) -> pd.DataFrame:
        """TEİAŞ's own day-ahead load forecast (yük tahmin planı): a benchmark to beat."""
        return self.items("/v1/consumption/data/load-estimation-plan", start, end)[["lep"]]

    def generation(self, start: date, end: date) -> pd.DataFrame:
        """Real-time generation by source (MWh per hour)."""
        df = self.items("/v1/generation/data/realtime-generation", start, end, 90)
        return df.drop(columns=[c for c in ("hour", "time") if c in df], errors="ignore")

    def wind_forecast(self, start: date, end: date) -> pd.DataFrame:
        """Wind generation and the official wind forecast (RES üretim ve tahmin)."""
        return self.items("/v1/renewables/data/res-generation-and-forecast", start, end, 90)[
            ["generation", "forecast"]
        ]

    def gas_price(self, start: date, end: date) -> pd.DataFrame:
        """Natural gas spot reference price (GRF, TRY/MWh), daily: gas plants usually set the
        electricity price, so this is the fuel-cost anchor."""
        return self.items(GAS + "/v1/markets/sgp/data/daily-reference-price", start, end)[
            ["grfTl", "grfUsd"]
        ]

    def supply_demand_curve(self, when: datetime) -> pd.DataFrame:
        """The day-ahead market's aggregate supply and demand curves for one hour."""
        d = self.post("/v1/markets/dam/data/supply-demand-chart-data", {"date": iso(when)})
        return pd.DataFrame(d.get("items", []))


def to_hourly_index(df: pd.DataFrame) -> pd.DataFrame:
    """Index EPİAŞ rows by their timestamp (``date``, Turkish time), sorted, numeric columns."""
    if df.empty:
        return df
    ts = pd.to_datetime(df["date"], utc=True).dt.tz_convert(TZ)
    out = df.drop(columns=["date"]).set_index(pd.DatetimeIndex(ts, name="time")).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    for c in out.columns:
        if out[c].dtype == object:
            converted = pd.to_numeric(out[c], errors="coerce")
            if converted.notna().any():
                out[c] = converted
    return out
