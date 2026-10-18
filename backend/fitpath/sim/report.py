"""Aggregate a finished simulation run into tables and a summary.json for the dashboard."""
import json
from dataclasses import asdict

import pandas as pd

from fitpath.catalog import ZONE_NAMES
from fitpath.config import OUT
from fitpath.sim.sizing import SIZES
from fitpath.sim.strategy import describe as describe_strategy

FIT_LONG_WAIT_S = 120


def _r(x, n=3):
    return None if x is None or pd.isna(x) else round(float(x), n)


def write_run(sim, run_id: str) -> dict:
    out = OUT / "sim" / run_id
    out.mkdir(parents=True, exist_ok=True)
    visits = pd.DataFrame(sim.visits)
    zones = pd.DataFrame(sim.zone_rows)
    rec = pd.DataFrame(sim.receipts)
    hourly = pd.DataFrame(sim.hourly)
    tracks = pd.DataFrame(sim.tracks, columns=["date", "t", "track", "visit_id", "x", "z", "covered"])
    names = {pid: p["name"] for pid, p in sim.personas.items()}

    # receipt -> track matching quality: a matched track is "clean" if >= 90% of its samples belong to the payer
    match = {"receipts": 0}
    if len(visits) and len(tracks):
        paid = visits.dropna(subset=["receipt_no"])
        purity = tracks.groupby(["date", "track"]).apply(lambda g: g.visit_id.value_counts(normalize=True).to_dict(), include_groups=False)
        rows = []
        for v in paid.itertuples():
            dist = purity.get((v.date, int(v.track_at_till)), {})
            p = dist.get(v.visit_id, 0.0)
            rows.append({"receipt_no": int(v.receipt_no), "visit_id": v.visit_id, "track": int(v.track_at_till), "purity": p, "correct": p >= .9})
        m = pd.DataFrame(rows)
        visits = visits.merge(m[["visit_id", "purity", "correct"]], on="visit_id", how="left")
        match = {"receipts": len(m), "correct_share": _r(m.correct.mean()), "avg_purity": _r(m.purity.mean()),
                 "id_switches": sum(e["type"] == "id_switch" for e in sim.events), "camera_coverage": _r(tracks.covered.mean()),
                 "tracks": int(tracks.groupby("date").track.nunique().sum())}

    buyers = visits[visits.receipt_no.notna()]
    daily = visits.groupby("date").agg(visits=("visit_id", "count"), receipts=("receipt_no", "count"), revenue=("bought_eur", "sum"),
                                        lost_fit=("lost_fit_eur", "sum"), lost_till=("lost_till_eur", "sum"), minutes=("minutes", "mean")).reset_index()
    daily["conversion"] = daily.receipts / daily.visits
    daily["volume_index"] = [round(sim.demand.volume_index(pd.Timestamp(d).date()), 3) for d in daily.date]

    z = zones.groupby("zone").agg(stops=("visit_id", "count"), dwell_s=("dwell_s", "mean"), picked=("picked", "sum"),
                                  bought=("bought", "sum"), revenue=("eur", "sum"), buy_stops=("bought", lambda s: (s > 0).sum())).reset_index()
    z["reach"] = z.stops / len(visits)
    z["conversion"] = z.buy_stops / z.stops
    z["name"] = z.zone.map(ZONE_NAMES)

    p = visits.groupby("persona").agg(visits=("visit_id", "count"), receipts=("receipt_no", "count"), revenue=("bought_eur", "sum"),
                                      minutes=("minutes", "mean"), fitting=("used_fitting", "mean"), lost=("lost_eur", "sum")).reset_index()
    p["conversion"] = p.receipts / p.visits
    p["avg_ticket"] = p.revenue / p.receipts.clip(lower=1)
    p["name"] = p.persona.map(names)

    tried = visits[visits.used_fitting | visits.fit_abandoned]
    short, long_ = tried[tried.fit_wait_s.fillna(0) <= FIT_LONG_WAIT_S], tried[tried.fit_wait_s.fillna(0) > FIT_LONG_WAIT_S]
    fitting = {"users": len(tried), "abandon_share": _r(tried.fit_abandoned.mean()), "avg_wait_s": _r(tried.fit_wait_s.mean(), 0),
               "p90_wait_s": _r(tried.fit_wait_s.quantile(.9), 0), "conv_short_wait": _r(short.receipt_no.notna().mean()),
               "conv_long_wait": _r(long_.receipt_no.notna().mean()) if len(long_) else None, "lost_eur": _r(visits.lost_fit_eur.sum(), 0),
               "long_wait_threshold_s": FIT_LONG_WAIT_S, "cabins_open": sim.cfg.layout["cabins_open"]}
    tills = {"avg_wait_s": _r(buyers.till_wait_s.mean(), 0), "p90_wait_s": _r(buyers.till_wait_s.quantile(.9), 0),
             "abandons": int((visits.outcome == "left the till queue").sum()), "lost_eur": _r(visits.lost_till_eur.sum(), 0),
             "tills_open": sim.cfg.layout["tills_open"]}

    h = pd.DataFrame()
    if len(hourly):
        hourly["slot"] = (hourly.t // 900) * 900
        dow = pd.to_datetime(hourly.date).dt.dayofweek
        hourly["daytype"] = dow.map(lambda d: "saturday" if d == 5 else "sunday" if d == 6 else "weekday")
        h = hourly.groupby(["daytype", "slot"])[["in_store", "fit_queue", "till_queue", "cabins_busy"]].mean().reset_index()
        h["time"] = h.slot.map(lambda s: f"{s // 3600:02d}:{(s % 3600) // 60:02d}")

    top = rec.groupby(["article_id", "name", "ptype", "zone"]).agg(units=("eur", "count"), revenue=("eur", "sum")).reset_index().sort_values("units", ascending=False).head(15) if len(rec) else pd.DataFrame()
    impulse = rec[rec.impulse].groupby("zone").eur.agg(["count", "sum"]).reset_index().rename(columns={"count": "units", "sum": "revenue"}) if len(rec) else pd.DataFrame()

    summary = {
        "run_id": run_id,
        "config": {**{k: v for k, v in asdict(sim.cfg).items() if k != "layout"}, "start": sim.cfg.start.isoformat(), "layout": sim.cfg.layout,
                   "strategy_levers": describe_strategy(sim.cfg.strategy) if sim.cfg.strategy else []},
        "totals": {"visits": len(visits), "receipts": int(buyers.shape[0]), "conversion": _r(buyers.shape[0] / max(1, len(visits))),
                   "revenue": _r(visits.bought_eur.sum(), 0), "intent_eur": _r(visits.intent_eur.sum(), 0), "lost_eur": _r(visits.lost_eur.sum(), 0),
                   "avg_ticket": _r(buyers.bought_eur.mean(), 2), "avg_minutes": _r(visits.minutes.mean(), 1),
                   "browsers": int((visits.kind == "browser").sum()), "browser_conversion": _r(visits[visits.kind == "browser"].receipt_no.notna().mean())},
        "store_size": {"key": sim.cfg.store_size, **SIZES[sim.cfg.store_size]},
        "day_plan": sim.day_plan,
        "daily": json.loads(daily.round(3).to_json(orient="records")),
        "zones": json.loads(z.round(3).to_json(orient="records")),
        "personas": json.loads(p.round(3).to_json(orient="records")),
        "fitting": fitting, "tills": tills, "matching": match,
        "hourly": json.loads(h.round(2).to_json(orient="records")) if len(h) else [],
        "top_articles": json.loads(top.round(2).to_json(orient="records")) if len(top) else [],
        "impulse_by_zone": json.loads(impulse.round(2).to_json(orient="records")) if len(impulse) else [],
        "behaviour": sim.bm.values(),
        "layout_effects": {"passby": int(rec.passby.sum()) if "passby" in rec else 0, "adjacent": int(rec.adjacent.sum()) if "adjacent" in rec else 0},
        "note": "In-store behaviour simulated from real H&M receipts and customer profiles; stops, fitting-room use and queue patience are model assumptions.",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    visits.to_parquet(out / "visits.parquet")
    zones.to_parquet(out / "zone_visits.parquet")
    if len(rec):
        rec.to_parquet(out / "receipts.parquet")
    tracks.to_parquet(out / "tracks.parquet")
    if len(hourly):
        hourly.drop(columns=["slot", "daytype"], errors="ignore").to_parquet(out / "hourly.parquet")
    return summary
