"""Strategy search: constrained generation + learned (neural) ranking + verification in the twin.

1. Generation: enumerate every combination of levers that respects the store's constraints
   (at most 8 extra staff hours per day, one discount at a time, feasible department swaps, ...).
2. Exploration: simulate a random sample of candidates (plus the LLM's proposals) with short replicas.
3. Ranking: an ensemble of small neural networks (MLP) learns "levers -> incremental profit" from those
   runs and scores all candidates in milliseconds; the spread of the ensemble is its uncertainty.
4. Verification: the best candidates are re-tested in the twin with full replicas (run_experiment).

Usage: python -m fitpath.sim.search --start 2020-09-25 --days 3
"""
import argparse
import itertools
import json
import os
import random
import statistics
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta

import numpy as np
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler

from fitpath.sim import strategy as strat
from fitpath.sim.experiment import EXP_DIR, run_experiment, run_one

MAX_STAFF_HOURS_PER_DAY = 8
TILL_WINDOWS = [(12, 14), (15, 20), (17, 20), (10, 20)]
ATT_WINDOWS = [(12, 15), (15, 20), (10, 20)]
LAYOUTS = [{}, {"swap": ["denim", "accessories"]}, {"swap": ["divided", "accessories"]}, {"swap": ["men", "denim"]}]
DISC_ZONES = ["ladies", "divided", "men", "kids", "denim", "basics", "accessories"]
DISC_PCT = [10, 20, 30]
DISC_DAYS = ["all", "weekend", "month_end"]
CROSS = [None, {"from": "denim", "to": "accessories"}, {"from": "ladies", "to": "accessories"}, {"from": "basics", "to": "accessories"}]
SEARCH_DIR = EXP_DIR.parent / "searches"
SEARCH_DIR.mkdir(parents=True, exist_ok=True)


MB_ZONES = ["basics", "accessories", "ladies", "denim"]
CLEAR_PCT = [20, 30, 40]
N_CANDIDATES = 20000


def _season_info(dates: list[date]):
    import json as _j
    from fitpath.config import OUT
    real = _j.loads((OUT / "analytics_real_store.json").read_text(encoding="utf-8"))
    season = ("Winter", "Spring", "Summer", "Autumn")[(dates[0].month % 12) // 3]
    lift = real["season_lift_full"].get(season, {})
    garments = [g for g, _ in sorted(lift.items(), key=lambda kv: -kv[1]) if g in strat.GARMENT_LABEL][:5]
    pairs = [(r["a"], r["b"]) for r in real["basket_types"]["top_lift"][:4]]
    return garments, pairs


def candidates(dates: list[date]) -> list[dict]:
    """Random valid lever combinations (the full product is too large now), deduplicated by effect."""
    garments, pairs = _season_info(dates)
    tills = [None] + [{"from": f, "to": t, "days": d} for (f, t) in TILL_WINDOWS for d in ("all", "weekend")]
    atts = [None] + [{"from": f, "to": t, "days": d} for (f, t) in ATT_WINDOWS for d in ("all", "weekend")]
    discs = [None] + [{"zone": z, "pct": p, "days": d} for z in DISC_ZONES for p in DISC_PCT for d in DISC_DAYS]
    mbs = [None] + [{"zone": z, "type": t, "days": d} for z in MB_ZONES for t in ("3x2", "2x50") for d in ("all", "weekend")]
    clears = [None] + [{"pct": p, "days": "all"} for p in CLEAR_PCT]
    bundles = [None] + [{"a": a, "b": b, "pct": 15} for a, b in pairs]
    feats = [None] + [{"garment": g, "days": "all"} for g in garments]
    rnd = random.Random(7)
    options = {"extra_till": tills[1:], "fitting_attendant": atts[1:], "layout": LAYOUTS[1:], "discount": discs[1:], "cross_merch": CROSS[1:],
               "multibuy": mbs[1:], "clearance": clears[1:], "bundle": bundles[1:], "feature": feats[1:]}
    as_list = {"discount", "cross_merch", "bundle"}
    out, seen = [], set()
    for _ in range(N_CANDIDATES * 4):
        if len(out) >= N_CANDIDATES:
            break
        s = {"name": "Candidate"}
        for lever in rnd.sample(list(options), rnd.choice((1, 2, 2, 3, 3, 4))):   # 1 to 4 levers keeps strategies readable
            if options[lever]:
                v = rnd.choice(options[lever])
                s[lever] = [v] if lever in as_list else v
        ti, at, di, cl = s.get("extra_till"), s.get("fitting_attendant"), (s.get("discount") or [None])[0], s.get("clearance")
        if any(sum(r["to"] - r["from"] for r in (ti, at) if r and strat.active(r, d)) > MAX_STAFF_HOURS_PER_DAY for d in dates):
            continue
        if di and not any(strat.active(di, d) for d in dates):
            continue
        if di and cl:                           # one price promotion at a time
            continue
        key = tuple(features(s, dates, garments, pairs))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def features(s: dict, dates: list[date], garments=None, pairs=None) -> list[float]:
    if garments is None or pairs is None:
        garments, pairs = _season_info(dates)
    n = len(dates)
    frac = lambda r: sum(strat.active(r, d) for d in dates) / n if r else 0.0
    ti, at = s.get("extra_till"), s.get("fitting_attendant")
    di = (s.get("discount") or [None])[0]
    cm = (s.get("cross_merch") or [None])[0]
    mb, cl, fe = s.get("multibuy"), s.get("clearance"), s.get("feature")
    bu = (s.get("bundle") or [None])[0]
    return [
        (ti["to"] - ti["from"]) * frac(ti) if ti else 0, ti["from"] if ti else 0, (ti["to"] if ti else 0),
        (at["to"] - at["from"]) * frac(at) if at else 0, at["from"] if at else 0,
        *[float(strat.layout_assign(s) == strat.layout_assign({"layout": L})) for L in LAYOUTS[1:]],
        *[(di["pct"] * frac(di) if di and di["zone"] == z else 0.0) for z in DISC_ZONES],
        *[float(cm == c) for c in CROSS[1:]],
        *[(frac(mb) * (1.0 if mb["type"] == "3x2" else .5) if mb and mb["zone"] == z else 0.0) for z in MB_ZONES],
        cl["pct"] * frac(cl) if cl else 0.0,
        *[float(bool(bu) and (bu["a"], bu["b"]) == p) for p in pairs],
        *[(frac(fe) if fe and fe["garment"] == g else 0.0) for g in garments],
    ]


def label(s: dict) -> str:
    return " · ".join(strat.describe(s))


class Ranker:
    """Ensemble of small MLPs on bootstrap samples: mean = expected gain, std = model uncertainty."""

    def __init__(self, n_models: int = 7, seed: int = 0):
        self.n, self.seed = n_models, seed

    def fit(self, X: np.ndarray, y: np.ndarray):
        self.sx = StandardScaler().fit(X)
        self.ym, self.ys = float(y.mean()), float(y.std() or 1.0)
        Xs, ys = self.sx.transform(X), (y - self.ym) / self.ys
        rng = np.random.default_rng(self.seed)
        self.models = []
        for k in range(self.n):
            idx = rng.integers(0, len(X), len(X))
            m = MLPRegressor(hidden_layer_sizes=(32, 16), alpha=1e-2, max_iter=3000, learning_rate_init=3e-3, random_state=self.seed + k)
            self.models.append(m.fit(Xs[idx], ys[idx]))
        return self

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        P = np.stack([m.predict(self.sx.transform(X)) for m in self.models]) * self.ys + self.ym
        return P.mean(0), P.std(0)


def _profit(base: dict, run: dict) -> float:
    return run["margin"] - base["margin"] - run["staff_hours"] * strat.STAFF_COST_EUR_H


def search(start: date, days: int = 3, size: str = "medium", mode: str = "future", cutoff: date | None = None,
           n_explore: int = 16, explore_reps: int = 2, top_k: int = 3, verify_reps: int = 4, seeds_from: int = 1000,
           proposals: list[dict] | None = None, on_progress=None) -> dict:
    t0 = time.time()
    dates = [start + timedelta(days=k) for k in range(days)]
    pool = candidates(dates)
    rnd = random.Random(42)
    explore = [strat.normalize(p) for p in (proposals or [])] + rnd.sample(pool, min(n_explore, len(pool)))
    common = {"start": start.isoformat(), "days": days, "size": size, "mode": mode, "cutoff": cutoff.isoformat() if cutoff else None, "tills_open": 3}
    seeds = [seeds_from + k for k in range(explore_reps)]
    tasks = [{**common, "seed": s, "strategy": None} for s in seeds] + [{**common, "seed": s, "strategy": c, "_i": i} for i, c in enumerate(explore) for s in seeds]
    step = lambda phase, k, n: on_progress and on_progress(phase, k, n)
    results = []
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        for k, r in enumerate(ex.map(run_one, [{kk: v for kk, v in t.items() if kk != "_i"} for t in tasks])):
            results.append(r)
            step("exploring", k + 1, len(tasks))
    base = {r["seed"]: r for r in results[:len(seeds)]}
    rows = []
    for t, r in zip(tasks[len(seeds):], results[len(seeds):]):
        rows.append((t["_i"], _profit(base[r["seed"]], r)))
    explored = []
    for i, c in enumerate(explore):
        vals = [v for j, v in rows if j == i]
        explored.append({"strategy": c, "label": label(c), "profit": round(statistics.fmean(vals), 2), "from_llm": i < len(proposals or [])})
    gp = _season_info(dates)
    X = np.array([features(c, dates, *gp) for i, c in enumerate(explore) for _ in seeds])
    y = np.array([v for _, v in sorted(rows, key=lambda r: r[0])])
    ranker = Ranker().fit(X, y)
    # quick quality check: leave-one-out on the explored means
    Xc = np.array([features(c, dates, *gp) for c in explore]); yc = np.array([e["profit"] for e in explored])
    loo = []
    for i in range(len(explore)):
        m = np.ones(len(explore), bool); m[i] = False
        if m.sum() >= 4:
            loo.append(float(Ranker(n_models=3).fit(Xc[m], yc[m]).predict(Xc[i:i + 1])[0][0]))
    corr = float(np.corrcoef(loo, yc[:len(loo)])[0, 1]) if len(loo) > 3 else None
    mu, sd = ranker.predict(np.array([features(c, dates, *gp) for c in pool]))
    order = np.argsort(-(mu + .5 * sd))
    seen = {json.dumps(c, sort_keys=True) for c in explore}
    ranked = []
    for i in order:
        c = pool[i]
        if json.dumps(c, sort_keys=True) in seen:
            continue
        ranked.append({"strategy": c, "label": label(c), "pred_profit": round(float(mu[i]), 2), "pred_std": round(float(sd[i]), 2)})
        if len(ranked) >= 15:
            break
    # verify the best predicted (and the best explored) in the twin with full replicas
    best_explored = max(explored, key=lambda e: e["profit"])
    to_verify = [r["strategy"] for r in ranked[:top_k - 1]] + [best_explored["strategy"]]
    verified = []
    for k, c in enumerate(to_verify):
        c = dict(c, name=f"Variant {k + 1}")
        exp = run_experiment(c, start, days, size, verify_reps, mode, cutoff, label=f"Search · variant {k + 1}",
                             on_progress=lambda a, b, k=k: step(f"verifying {k + 1}/{len(to_verify)}", a, b))
        verified.append({"experiment_id": exp["id"], "label": label(c), "strategy": exp["strategy"], "summary": exp["summary"], "verdict": exp["verdict"]})
    res = {"id": f"search-{uuid.uuid4().hex[:8]}", "created": time.time(), "seconds": round(time.time() - t0, 1),
           "settings": {**common, "n_candidates": len(pool), "n_explore": len(explore), "explore_reps": explore_reps, "verify_reps": verify_reps},
           "constraints": {"max_staff_hours_per_day": MAX_STAFF_HOURS_PER_DAY, "max_discounts": 1},
           "explored": sorted(explored, key=lambda e: -e["profit"]), "ranked": ranked, "verified": verified,
           "ranker": {"type": "ensemble of 7 MLPs (32-16) on bootstrap samples", "loo_corr": round(corr, 3) if corr is not None else None,
                      "trained_on": int(len(y))}}
    (SEARCH_DIR / f"{res['id']}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def list_searches() -> list[dict]:
    out = [json.loads(p.read_text(encoding="utf-8")) for p in SEARCH_DIR.glob("search-*.json")]
    return sorted(out, key=lambda e: e["created"], reverse=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-09-25")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--cutoff", default="2020-09-23")
    a = ap.parse_args()
    r = search(date.fromisoformat(a.start), a.days, cutoff=date.fromisoformat(a.cutoff),
               on_progress=lambda ph, k, n: print(f"\r  {ph} {k}/{n}   ", end="", flush=True))
    print(f"\n{r['settings']['n_candidates']} candidates, {r['seconds']} s, ranker LOO corr {r['ranker']['loo_corr']}")
    for e in r["explored"][:4]:
        print("  explored ", e["profit"], e["label"])
    for e in r["ranked"][:4]:
        print("  predicted", e["pred_profit"], "±", e["pred_std"], e["label"])
    for v in r["verified"]:
        print("  verified ", v["verdict"], v["summary"]["profit"], v["label"])
