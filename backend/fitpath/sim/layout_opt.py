"""Department layout optimisation: which department goes in which block of the floor plan.

Follows the block-layout approach of Ozgormus (2015, Auburn): candidate layouts respect the store's rules,
they are evaluated by simulation, and the search moves by swapping two departments, guided by two objectives:
  * revenue (here: incremental margin measured in the twin, base layout vs candidate, common random numbers)
  * adjacency of departments that are bought together (lift from the real H&M baskets)

Steps
1. Generation: every feasible department -> block assignment (sim/store_plan.feasible).
2. Exploration: a sample (the most promising by the two objectives + random ones) is simulated with short replicas.
3. Ranking: the MLP ensemble of sim/search.py learns "layout features -> incremental profit".
4. Tabu search over swaps on the ranker's optimistic score (mean + 0.5 std), from the best explored layouts.
5. Verification: the best layouts are re-tested in the twin with full replicas (run_experiment).

Usage: python -m fitpath.sim.layout_opt --start 2020-09-25 --days 2
"""
import argparse
import itertools
import json
import math
import os
import random
import statistics
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta

import numpy as np

from fitpath.catalog import ZONE_NAMES, ZONES
from fitpath.sim import store_plan as sp
from fitpath.sim.behavior import ATTRACTION
from fitpath.sim.experiment import EXP_DIR, run_experiment, run_one
from fitpath.sim.search import Ranker, _profit

LAYOUT_DIR = EXP_DIR.parent / "layouts"
LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
TABU_ITERS, TABU_TENURE = 60, 6


def all_feasible() -> list[dict]:
    blocks = list(sp.BLOCKS)
    out = []
    for perm in itertools.permutations(blocks, len(ZONES)):
        a = dict(zip(ZONES, perm))
        if sp.feasible(a)[0]:
            out.append(a)
    return out


def _centre(bid: str) -> tuple[float, float]:
    b = sp.BLOCKS[bid]
    return (b["x0"] + b["x1"]) / 2, (b["z0"] + b["z1"]) / 2


def _shares() -> dict:
    s = sp.zone_share()
    tot = sum(s.get(z, 0) for z in ZONES)
    return {z: s.get(z, .05) / tot for z in ZONES}


_ATT = {c: sum(r) / 2 for c, r in ATTRACTION.items()}


def adjacency_score(assign: dict) -> float:
    """Sum over adjacent department pairs of how much more than usual they are bought together."""
    lifts = sp.zone_lift()
    mid = statistics.median(lifts.values())
    score = 0.0
    for a, b in itertools.combinations(ZONES, 2):
        if "new_in" in (a, b) or not sp.adjacent(assign[a], assign[b]):
            continue
        score += sp.lift_of(a, b) / mid - 1
    return round(score, 3)


def features(assign: dict) -> list[float]:
    share = _shares()
    ent = (sp.ENTRANCE["x"], sp.ENTRANCE["z_in"])
    fq = (sp.FITTING["queue"]["x"], sp.FITTING["queue"]["z"])
    fit = sp.space_fit(assign)
    exposure = sum(share[z] * _ATT[sp.BLOCKS[assign[z]]["cls"]] for z in ZONES)
    travel = sum(share[z] * math.dist(ent, _centre(assign[z])) for z in ZONES)
    space = sum(share[z] * abs(math.log(fit[z])) for z in ZONES)
    return [exposure, adjacency_score(assign), travel / 10, space,
            math.dist(_centre(assign["ladies"]), fq) / 10, _ATT[sp.BLOCKS[assign["new_in"]]["cls"]],
            _ATT[sp.BLOCKS[assign["basics"]]["cls"]], _ATT[sp.BLOCKS[assign["divided"]]["cls"]]]


def heuristic(assign: dict) -> float:
    """Prior guess used only to pick part of the exploration sample: exposure of big departments + adjacency."""
    f = features(assign)
    return f[0] + .05 * f[1] - .3 * f[3]


def _strategy(assign: dict, name: str) -> dict:
    return {"name": name, "layout": {"assign": assign}}


def _key(a: dict) -> str:
    return json.dumps(a, sort_keys=True)


def tabu(start: dict, score, feasible_keys: set, iters: int = TABU_ITERS, tenure: int = TABU_TENURE):
    """Tabu search over pairwise department swaps; returns every layout visited with its score."""
    cur, visited, tabu_list, trace = start, {_key(start): score(start)}, [], []
    best = (visited[_key(start)], start)
    for it in range(iters):
        moves = []
        for a, b in itertools.combinations(ZONES, 2):
            cand = sp.swapped(cur, a, b)
            k = _key(cand)
            if k not in feasible_keys:
                continue
            v = visited.get(k)
            if v is None:
                v = visited[k] = score(cand)
            if {a, b} in tabu_list and v <= best[0]:     # aspiration: a tabu move is allowed only if it beats the best
                continue
            moves.append((v, a, b, cand))
        if not moves:
            break
        v, a, b, cur = max(moves, key=lambda m: m[0])
        tabu_list = (tabu_list + [{a, b}])[-tenure:]
        trace.append({"iter": it + 1, "swap": [a, b], "score": round(v, 2)})
        if v > best[0]:
            best = (v, cur)
    return visited, trace


def optimize(start: date, days: int = 2, size: str = "medium", mode: str = "future", cutoff: date | None = None,
             tills_open: int = 3, cabins_open: int = 8, n_explore: int = 14, explore_reps: int = 2, top_k: int = 3,
             verify_reps: int = 4, seeds_from: int = 2000, on_progress=None) -> dict:
    t0 = time.time()
    step = lambda phase, k, n: on_progress and on_progress(phase, k, n)
    pool = [a for a in all_feasible() if a != sp.DEFAULT_ASSIGN]
    keys = {_key(a) for a in pool} | {_key(sp.DEFAULT_ASSIGN)}
    rnd = random.Random(11)
    by_h = sorted(pool, key=heuristic, reverse=True)
    explore = by_h[:n_explore // 2]
    rest = [a for a in pool if a not in explore]
    explore += rnd.sample(rest, min(n_explore - len(explore), len(rest)))
    common = {"start": start.isoformat(), "days": days, "size": size, "mode": mode, "cutoff": cutoff.isoformat() if cutoff else None,
              "tills_open": tills_open, "cabins_open": cabins_open}
    seeds = [seeds_from + k for k in range(explore_reps)]
    tasks = [{**common, "seed": s, "strategy": None} for s in seeds]
    tasks += [{**common, "seed": s, "strategy": _strategy(a, f"Layout {i + 1}")} for i, a in enumerate(explore) for s in seeds]
    results = []
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        for k, r in enumerate(ex.map(run_one, tasks)):
            results.append(r)
            step("exploring", k + 1, len(tasks))
    base = {r["seed"]: r for r in results[:len(seeds)]}
    prof = [_profit(base[r["seed"]], r) for r in results[len(seeds):]]
    explored = []
    for i, a in enumerate(explore):
        vals = prof[i * len(seeds):(i + 1) * len(seeds)]
        explored.append({"assign": a, "moved": sp.describe_assign(a), "profit": round(statistics.fmean(vals), 2),
                         "adjacency": adjacency_score(a)})
    X = np.array([features(a) for a in explore for _ in seeds])
    y = np.array(prof)
    ranker = Ranker().fit(X, y)
    Xc = np.array([features(a) for a in explore]); yc = np.array([e["profit"] for e in explored])
    loo = []
    for i in range(len(explore)):
        m = np.ones(len(explore), bool); m[i] = False
        loo.append(float(Ranker(n_models=3).fit(Xc[m], yc[m]).predict(Xc[i:i + 1])[0][0]))
    corr = float(np.corrcoef(loo, yc)[0, 1]) if len(loo) > 3 and np.std(loo) > 0 else None

    def score(a):
        mu, sd = ranker.predict(np.array([features(a)]))
        return float(mu[0] + .5 * sd[0])

    step("tabu search", 0, 1)
    starts = [e["assign"] for e in sorted(explored, key=lambda e: -e["profit"])[:2]]
    visited, traces = {}, []
    for s0 in starts:
        v, tr = tabu(s0, score, keys)
        visited.update(v)
        traces.append(tr)
    step("tabu search", 1, 1)
    explored_keys = {_key(a) for a in explore}
    ranked = []
    for k, v in sorted(visited.items(), key=lambda kv: -kv[1]):
        a = json.loads(k)
        if k in explored_keys or a == sp.DEFAULT_ASSIGN:
            continue
        mu, sd = ranker.predict(np.array([features(a)]))
        ranked.append({"assign": a, "moved": sp.describe_assign(a), "pred_profit": round(float(mu[0]), 2), "pred_std": round(float(sd[0]), 2),
                       "adjacency": adjacency_score(a)})
        if len(ranked) >= 10:
            break
    best_explored = max(explored, key=lambda e: e["profit"])
    to_verify = [r["assign"] for r in ranked[:top_k - 1]] + [best_explored["assign"]]
    verified = []
    for k, a in enumerate(to_verify):
        exp = run_experiment(_strategy(a, f"Optimised layout {k + 1}"), start, days, size, verify_reps, mode, cutoff,
                             tills_open=tills_open, cabins_open=cabins_open, label=f"Layout optimisation · {k + 1}",
                             on_progress=lambda x, n, k=k: step(f"verifying {k + 1}/{len(to_verify)}", x, n))
        verified.append({"experiment_id": exp["id"], "assign": a, "moved": sp.describe_assign(a), "adjacency": adjacency_score(a),
                         "summary": exp["summary"], "verdict": exp["verdict"]})
    res = {"id": f"layout-{uuid.uuid4().hex[:8]}", "created": time.time(), "seconds": round(time.time() - t0, 1),
           "settings": {**common, "n_feasible": len(pool) + 1, "n_explore": len(explore), "explore_reps": explore_reps, "verify_reps": verify_reps},
           "rules": {"new_in": [sp.BLOCKS[b]["name"] for b in sp.FRONT_BLOCKS], "ladies": [sp.BLOCKS[b]["name"] for b in sp.FITTING_BLOCKS],
                     "space": [sp.SPACE_MIN, sp.SPACE_MAX]},
           "base": {"assign": sp.DEFAULT_ASSIGN, "adjacency": adjacency_score(sp.DEFAULT_ASSIGN)},
           "explored": sorted(explored, key=lambda e: -e["profit"]), "ranked": ranked, "verified": verified,
           "tabu": {"iterations": TABU_ITERS, "tenure": TABU_TENURE, "visited": len(visited), "trace": traces[0][:20] if traces else []},
           "ranker": {"type": "ensemble of 7 MLPs (32-16)", "features": ["exposure", "adjacency", "walking distance", "space", "Ladies-fitting rooms",
                                                                      "New in position", "Basics position", "Divided position"],
                      "loo_corr": round(corr, 3) if corr is not None else None, "trained_on": int(len(y))}}
    (LAYOUT_DIR / f"{res['id']}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def list_layouts() -> list[dict]:
    out = [json.loads(p.read_text(encoding="utf-8")) for p in LAYOUT_DIR.glob("layout-*.json")]
    return sorted(out, key=lambda e: e["created"], reverse=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-09-25")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--cutoff", default="2020-09-23")
    ap.add_argument("--explore", type=int, default=14)
    a = ap.parse_args()
    r = optimize(date.fromisoformat(a.start), a.days, cutoff=date.fromisoformat(a.cutoff), n_explore=a.explore,
                 on_progress=lambda ph, k, n: print(f"\r  {ph} {k}/{n}   ", end="", flush=True))
    print(f"\n{r['settings']['n_feasible']} feasible layouts, {r['seconds']} s, ranker LOO corr {r['ranker']['loo_corr']}, tabu visited {r['tabu']['visited']}")
    for e in r["explored"][:4]:
        print("  explored ", e["profit"], "adj.", e["adjacency"], e["moved"])
    for e in r["ranked"][:4]:
        print("  predicted", e["pred_profit"], "±", e["pred_std"], e["moved"])
    for v in r["verified"]:
        print("  verified ", v["verdict"], v["summary"]["profit"], v["moved"])
