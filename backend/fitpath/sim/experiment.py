"""Test strategies in the twin: baseline vs strategy over the same days, with replicas.

Each replica uses the same random seed for baseline and strategy (common random numbers), so the
difference mostly reflects the strategy, not luck. Replicas run in parallel processes. The result is the
mean difference of each KPI with a 90% confidence interval, and the incremental profit:
  margin difference - extra staff cost.

Periods:
  mode "future":  days after the data ends (or after a chosen cutoff): forecast volume + analog baskets
  mode "history": real days from the data (what would have happened with this strategy)
"""
import json
import math
import os
import statistics
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta

from fitpath.config import OUT
from fitpath.sim import strategy as strat
from fitpath.sim.layout import BASE_LAYOUT

EXP_DIR = OUT / "experiments"
EXP_DIR.mkdir(parents=True, exist_ok=True)
T90 = {1: 6.31, 2: 2.92, 3: 2.35, 4: 2.13, 5: 2.02, 6: 1.94, 7: 1.89, 8: 1.86, 9: 1.83, 10: 1.81}
METRICS = ["revenue", "margin", "receipts", "visits", "conversion", "lost_eur", "lost_fit", "lost_till", "till_wait_s", "fit_wait_s", "fit_abandon"]

_cache = {}


def _demand(mode: str, start: date, days: int, cutoff: date | None):
    """One demand source per worker process and period (loading baskets is the slow part)."""
    key = (mode, start, days, cutoff)
    if key not in _cache:
        from fitpath.sim.demand import Demand
        if mode == "future":
            from fitpath.forecast import ForecastDemand
            _cache[key] = ForecastDemand(cutoff, start, days, base=_cache.setdefault("base", Demand()))
        else:
            _cache[key] = _cache.setdefault("base", Demand())
    return _cache[key]


def run_one(task: dict) -> dict:
    """Simulate one scenario (no tracks, no files) and return its KPIs. Runs in a worker process."""
    from fitpath.sim.engine import SimConfig, Simulation, load_personas
    start, cutoff = date.fromisoformat(task["start"]), (date.fromisoformat(task["cutoff"]) if task.get("cutoff") else None)
    st = strat.normalize(task["strategy"]) if task["strategy"] else {}
    cfg = SimConfig(start=start, days=task["days"], store_size=task["size"], seed=task["seed"], record_tracks=False,
                    layout=strat.apply_layout(BASE_LAYOUT, st, st.get("name")), strategy=st)
    cfg.layout["tills_open"] = task.get("tills_open", BASE_LAYOUT["tills_open"])
    cfg.layout["cabins_open"] = task.get("cabins_open", BASE_LAYOUT["cabins_open"])
    sim = Simulation(cfg, load_personas(), _demand(task["mode"], start, task["days"], cutoff))
    sim.simulate(quiet=True)
    v = sim.visits
    n = max(1, len(v))
    rec = [x for x in v if x["receipt_no"]]
    fit = [x for x in v if x["fit_wait_s"] is not None]
    dates = [start + timedelta(days=k) for k in range(task["days"])]
    return {
        "seed": task["seed"], "is_base": not task["strategy"],
        "revenue": sum(x["bought_eur"] for x in v), "margin": sum(x["margin_eur"] for x in v),
        "receipts": len(rec), "visits": len(v), "conversion": len(rec) / n,
        "lost_eur": sum(x["lost_eur"] for x in v), "lost_fit": sum(x["lost_fit_eur"] for x in v), "lost_till": sum(x["lost_till_eur"] for x in v),
        "till_wait_s": statistics.fmean([x["till_wait_s"] for x in rec if x["till_wait_s"] is not None] or [0]),
        "fit_wait_s": statistics.fmean([x["fit_wait_s"] for x in fit] or [0]),
        "fit_abandon": (sum(x["fit_abandoned"] for x in fit) / len(fit)) if fit else 0,
        "staff_hours": strat.staff_hours(st, dates) if st else 0,
        "day_index": [round(p["index"], 3) for p in sim.day_plan],
    }


def _summary(base: list[dict], runs: list[dict]) -> dict:
    by_seed = {b["seed"]: b for b in base}
    out = {}
    for m in METRICS + ["profit"]:
        if m == "profit":
            diffs = [r["margin"] - by_seed[r["seed"]]["margin"] - r["staff_hours"] * strat.STAFF_COST_EUR_H for r in runs]
            ref = statistics.fmean(b["margin"] for b in base)
        else:
            diffs = [r[m] - by_seed[r["seed"]][m] for r in runs]
            ref = statistics.fmean(b[m] for b in base)
        mean = statistics.fmean(diffs)
        half = T90.get(len(diffs) - 1, 1.65) * statistics.stdev(diffs) / math.sqrt(len(diffs)) if len(diffs) > 1 else None
        out[m] = {"base": round(ref, 2), "delta": round(mean, 2), "ci90": [round(mean - half, 2), round(mean + half, 2)] if half is not None else None,
                  "delta_pct": round(mean / ref, 4) if ref else None}
    return out


def verdict(s: dict) -> str:
    p = s["profit"]
    lo, hi = p["ci90"] or [p["delta"], p["delta"]]
    if lo > 0:
        return "worth it"
    if hi < 0:
        return "not worth it"
    return "uncertain"


def run_experiment(strategy: dict, start: date, days: int = 3, size: str = "medium", reps: int = 4, mode: str = "future",
                   cutoff: date | None = None, tills_open: int = 3, on_progress=None, label: str | None = None, workers: int | None = None,
                   cabins_open: int = 8) -> dict:
    """Baseline vs strategy over `reps` replicas; returns and stores the result."""
    st = strat.normalize(strategy)
    t0 = time.time()
    common = {"start": start.isoformat(), "days": days, "size": size, "mode": mode, "cutoff": cutoff.isoformat() if cutoff else None, "tills_open": tills_open, "cabins_open": cabins_open}
    seeds = [1000 + k for k in range(reps)]
    tasks = [{**common, "seed": s, "strategy": None} for s in seeds] + [{**common, "seed": s, "strategy": st} for s in seeds]
    results = []
    with ProcessPoolExecutor(max_workers=workers or min(len(tasks), max(1, (os.cpu_count() or 2) - 1))) as ex:
        for i, r in enumerate(ex.map(run_one, tasks)):
            results.append(r)
            if on_progress:
                on_progress(i + 1, len(tasks))
    base = [r for r in results if r["is_base"]]
    runs = [r for r in results if not r["is_base"]]
    summ = _summary(base, runs)
    exp = {"id": f"exp-{uuid.uuid4().hex[:8]}", "label": label or st.get("name"), "strategy": st, "levers": strat.describe(st),
           "settings": {**common, "reps": reps}, "summary": summ, "verdict": verdict(summ),
           "day_index": base[0]["day_index"] if base else [], "seconds": round(time.time() - t0, 1), "created": time.time(),
           "assumptions": {"staff_cost_eur_h": strat.STAFF_COST_EUR_H, "gross_margin": strat.GROSS_MARGIN,
                           "attendant_cabin_factor": strat.ATTENDANT_CABIN_FACTOR, "cross_merch_p": strat.CROSS_MERCH_P}}
    (EXP_DIR / f"{exp['id']}.json").write_text(json.dumps(exp, ensure_ascii=False, indent=1), encoding="utf-8")
    return exp


def showcase(strategy: dict | None, start: date, days: int, size: str, mode: str, cutoff: date | None,
             tills_open: int = 3, cabins_open: int = 8, label: str | None = None) -> str:
    """Simulate the busiest day of the period with the strategy and record the tracks, for the 3D replay."""
    from fitpath.sim.demand import Demand
    from fitpath.sim.engine import SimConfig, Simulation, load_personas
    st = strat.normalize(strategy) if strategy else {}
    demand = _demand(mode, start, days, cutoff)
    dates = [start + timedelta(days=k) for k in range(days)]
    peak = max(dates, key=demand.volume_index)
    if mode == "future":
        from fitpath.forecast import ForecastDemand
        demand = ForecastDemand(cutoff, peak, 1, base=_cache.setdefault("base", Demand()))
    lay = strat.apply_layout(BASE_LAYOUT, st, label or st.get("name") or "Current store")
    lay["tills_open"], lay["cabins_open"] = tills_open, cabins_open
    run_id = f"show-{uuid.uuid4().hex[:8]}"
    cfg = SimConfig(start=peak, days=1, store_size=size, seed=1000, layout=lay, strategy=st, run_id=run_id)
    Simulation(cfg, load_personas(), demand).run()
    return run_id


def list_experiments() -> list[dict]:
    out = [json.loads(p.read_text(encoding="utf-8")) for p in EXP_DIR.glob("exp-*.json")]
    return sorted(out, key=lambda e: e["created"], reverse=True)


if __name__ == "__main__":
    demo = {"name": "Extra till on weekend afternoons", "extra_till": {"from": 15, "to": 20, "days": "weekend"}}
    e = run_experiment(demo, date(2020, 9, 25), days=3, size="medium", reps=4, mode="future", cutoff=date(2020, 9, 23))
    print(e["verdict"], e["seconds"], "s")
    for k in ("revenue", "margin", "profit", "conversion", "till_wait_s", "lost_till"):
        print(f"  {k:12s}", e["summary"][k])
