"""Demand forecast for future (or held-out) days, and the demand source the twin uses for them.

Model: seasonal naive with trend on the store channel's daily basket count, using only days before a
cutoff (see Forecaster). Volume index for the simulation = forecast / mean of the
365 days before the cutoff, so it plugs into the store-size presets like the historical index does.

Future baskets come from "analog days": the same weekday 52 weeks earlier (plus its neighbours one
week before and after), all strictly before the cutoff. Nothing after the cutoff is ever read.

Backtest: pick a cutoff inside the data, forecast the following weeks, compare with what really happened.

Usage: python -m fitpath.forecast --cutoff 2020-06-29 --days 56
"""
import argparse
import json
import math
from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd

from fitpath.config import OUT, STORE_CHANNEL, pq
from fitpath.sim.demand import Demand


def daily_series() -> pd.DataFrame:
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT t_dat AS d, count(DISTINCT cid) AS baskets, sum(price) * 590 AS revenue
        FROM '{pq("transactions")}' WHERE channel = {STORE_CHANNEL} GROUP BY 1 ORDER BY 1""").df()
    df["d"] = pd.to_datetime(df.d)
    full = pd.DataFrame({"d": pd.date_range(df.d.min(), df.d.max())})
    return full.merge(df, on="d", how="left").fillna({"baskets": 0, "revenue": 0})


RECAL_DAYS = 28


class Forecaster:
    """Seasonal naive with trend: same weekday one year earlier (average of the 3 surrounding weeks),
    scaled by how the last 4 weeks compare with the same 4 weeks one year earlier.

    In backtests on two years of data this beat a gradient-boosting model on calendar features
    (too little history for it to learn yearly seasonality), so the simpler method is used.
    """

    def __init__(self, cutoff: date, series: pd.DataFrame | None = None):
        self.cutoff = pd.Timestamp(cutoff)
        s = series if series is not None else daily_series()
        past = s[s.d < self.cutoff]
        self.act = past.set_index("d").baskets
        win = lambda end: self.act[(self.act.index >= end - pd.Timedelta(days=RECAL_DAYS)) & (self.act.index < end)].sum()
        self.trend = float(win(self.cutoff) / max(1.0, win(self.cutoff - pd.Timedelta(days=364))))
        self.base = past[past.d >= self.cutoff - pd.Timedelta(days=365)].baskets.mean()

    def _last_year(self, d: pd.Timestamp) -> float:
        vals = [self.act.get(d - pd.Timedelta(days=364 + k), np.nan) for k in (-7, 0, 7)]
        vals = [v for v in vals if not np.isnan(v) and v > 0]
        return float(np.mean(vals)) if vals else float(self.base)

    def predict(self, start: date, days: int) -> pd.DataFrame:
        dd = pd.Series(pd.date_range(pd.Timestamp(start), periods=days))
        pred = np.array([self._last_year(d) for d in dd]) * self.trend
        return pd.DataFrame({"d": dd, "pred": pred, "index": pred / self.base})


class ForecastDemand:
    """Same interface as sim.demand.Demand, for days at or after the cutoff (future or held out)."""

    def __init__(self, cutoff: date, start: date, days: int, base: Demand | None = None):
        self.cutoff = cutoff
        self.base = base or Demand()
        self.fc = Forecaster(cutoff)
        pr = self.fc.predict(start, days)
        self._idx = {r.d.date(): float(r.index) for r in pr.itertuples()}
        self.first_day, self.last_day = self.base.first_day, self.base.last_day

    def volume_index(self, d: date) -> float:
        return self._idx.get(d, 1.0)

    def analog_dates(self, d: date) -> list[date]:
        cands = [d - timedelta(days=364 + k) for k in (0, 7, -7)]
        return [c for c in cands if c < self.cutoff and c >= self.base.first_day]

    def baskets(self, start: date, days: int) -> dict:
        targets = [start + timedelta(days=k) for k in range(days)]
        analogs = {d: self.analog_dates(d) for d in targets}
        loaded = self.base.baskets(start, days, dates=sorted({a for v in analogs.values() for a in v}))
        return {d: [b for a in analogs[d] for b in loaded.get(a, [])] for d in targets}


def backtest(cutoff: date, days: int = 56) -> dict:
    s = daily_series()
    fc = Forecaster(cutoff, s)
    pr = fc.predict(cutoff, days)
    act = s.set_index("d").baskets
    pr["actual"] = pr.d.map(act).astype(float)
    pr = pr.dropna()
    wape = float(abs(pr.pred - pr.actual).sum() / pr.actual.sum())
    wk = pr.assign(w=pr.d.dt.to_period("W").dt.start_time).groupby("w")[["pred", "actual"]].sum()
    wk_wape = float(abs(wk.pred - wk.actual).sum() / wk.actual.sum())
    naive = pr.d.map(lambda x: act.get(x - pd.Timedelta(days=364), np.nan)).astype(float)
    m = naive.notna()
    naive_wape = float(abs(naive[m] - pr.actual[m]).sum() / pr.actual[m].sum())
    return {
        "cutoff": cutoff.isoformat(), "days": len(pr), "wape_daily": round(wape, 4), "wape_weekly": round(wk_wape, 4),
        "naive_last_year_wape_daily": round(naive_wape, 4), "trend": round(fc.trend, 3),
        "series": [{"date": r.d.date().isoformat(), "pred": round(float(r.pred)), "actual": int(r.actual)} for r in pr.itertuples()],
        "note": "Receipts per day, store channel of the whole H&M chain. The model is trained only on days before the cutoff; WAPE = total absolute error / total actual. Benchmark: same day 52 weeks earlier.",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2020-06-29")
    ap.add_argument("--days", type=int, default=56)
    a = ap.parse_args()
    res = backtest(date.fromisoformat(a.cutoff), a.days)
    (OUT / f"backtest_{a.cutoff}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print({k: v for k, v in res.items() if k != "series"})
