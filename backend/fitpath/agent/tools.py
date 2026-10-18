"""Tools the FitPath agent can call. Each tool: JSON schema for the model, an approval rule, a handler.

Handlers receive (args, ctx) where ctx.progress(done, total, phase) updates the tool block in the chat.
They return a dict: what the model reads ("result"), an optional short text for the UI ("summary"), and
optional UI instructions ("ui": show a run in the 3D, preview a layout).
"""
import json
from datetime import date, timedelta

from fitpath.agent import store
from fitpath.config import OUT
from fitpath.catalog import ZONES
from fitpath.sim import store_plan
from fitpath.sim import strategy as strat

DATA_END = date(2020, 9, 22)     # last day of the H&M data = "today" in the demo
CUTOFF = DATA_END + timedelta(days=1)
DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DAYS_ENUM = {"type": "string", "enum": list(strat.DAYS), "description": "all, weekend (Sat-Sun), weekdays (Mon-Fri), month_end (last 7 days of the month)"}
ZONE_ENUM = {"type": "string", "enum": list(ZONES)}
WINDOW = {"type": "object", "properties": {"from": {"type": "integer", "description": "hour 10-19"}, "to": {"type": "integer", "description": "hour 11-20"}, "days": DAYS_ENUM},
          "required": ["from", "to"]}
STRATEGY = {
    "type": "object", "description": "Strategy: a name plus one or more levers",
    "properties": {
        "name": {"type": "string"},
        "feature": {"type": "object", "description": "garment category displayed in the entrance window",
                    "properties": {"garment": {"type": "string", "enum": list(strat.GARMENT_LABEL)}, "days": DAYS_ENUM}, "required": ["garment"]},
        "clearance": {"type": "object", "description": "end-of-season sale on the categories that are out of season now",
                      "properties": {"pct": {"type": "integer", "description": "discount 10-60"}, "days": DAYS_ENUM}, "required": ["pct"]},
        "multibuy": {"type": "object", "description": "3-for-2 (3x2) or second item half price (2x50) on a department",
                     "properties": {"zone": ZONE_ENUM, "type": {"type": "string", "enum": ["3x2", "2x50"]}, "days": DAYS_ENUM}, "required": ["zone", "type"]},
        "bundle": {"type": "array", "description": "pairs of product types bought together in real baskets, discount on the second",
                   "items": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}, "pct": {"type": "integer"}}, "required": ["a", "b"]}},
        "discount": {"type": "array", "description": "price cut on a department (not together with clearance)",
                     "items": {"type": "object", "properties": {"zone": ZONE_ENUM, "pct": {"type": "integer", "description": "5-50"}, "days": DAYS_ENUM}, "required": ["zone", "pct"]}},
        "cross_merch": {"type": "array", "description": "products of department 'to' also displayed in department 'from'",
                        "items": {"type": "object", "properties": {"from": ZONE_ENUM, "to": ZONE_ENUM}, "required": ["from", "to"]}},
        "layout": {"type": "object", "description": "swap two departments (swap) or move departments to blocks (assign: department -> block). "
                   + "Blocks: " + "; ".join(f"{k} = {b['name']}" for k, b in store_plan.BLOCKS.items())
                   + ". Rules: New in stays in a front block, Ladies stays next to the fitting rooms, space fits the department's sales.", "properties": {
            "swap": {"type": "array", "items": ZONE_ENUM},
            "assign": {"type": "object", "properties": {z: {"type": "string", "enum": list(store_plan.BLOCKS)} for z in ZONES}}}},
        "extra_till": {**WINDOW, "description": "one more till open in this window"},
        "fitting_attendant": {**WINDOW, "description": "a fitting-room attendant in this window"},
    },
    "required": ["name"],
}
SIZE = {"type": "string", "enum": ["low", "medium", "high"], "description": "store traffic (default medium)"}


def _real() -> dict:
    return json.loads((OUT / "analytics_real_store.json").read_text(encoding="utf-8"))


def _period(start: str, days: int):
    d0 = date.fromisoformat(start)
    days = max(1, min(31, int(days)))
    if d0 <= DATA_END:
        days = min(days, (DATA_END - d0).days + 1)
        return d0, days, "history", None
    return d0, days, "future", CUTOFF


def _d(x: dict) -> dict:
    return {"mean": round(x["delta"], 2), "interval_90": x["ci90"]}


def _pts(x: dict) -> dict:
    """A share difference as percentage points."""
    return _d({**x, "delta": x["delta"] * 100, "ci90": [v * 100 for v in x["ci90"]] if x["ci90"] else None})


def _eur(v: float, sign: bool = False) -> str:
    return f"{'+' if sign and v >= 0 else '−' if v < 0 else ''}€{abs(v):,.0f}"


# ---------- handlers ----------
def sales_data(a, ctx):
    r, topic = _real(), a.get("topic") or "best_sellers"
    season = a.get("season") or ("Winter", "Spring", "Summer", "Autumn")[(DATA_END.month % 12) // 3]
    if topic == "overview":
        res = {**r["overview"], "price_note": r.get("price_note")}
    elif topic == "seasonality":
        lift = r["season_lift_full"].get(season, {})
        res = {"season": season, "category_lift": dict(sorted(lift.items(), key=lambda kv: -kv[1]))}
    elif topic == "weekdays":
        res = {"receipts_per_weekday": {DOW[d["dow"] - 1]: d["baskets"] for d in r["weekday"]["days"]}}
    elif topic == "payday":
        res = {"revenue_index_by_day_of_month_avg_100": {d["dom"]: d["index"] for d in r["month_day"]}}
    elif topic == "markdowns":
        res = {"weeks_with_most_discount": [(w["week"], w["avg_discount"]) for w in sorted(r["markdowns"], key=lambda w: -w["avg_discount"])[:8]]}
    elif topic == "bought_together":
        res = {"product_type_pairs": [(p["a"], p["b"], round(p["lift"], 1), p["baskets"]) for p in r["basket_types"]["top_lift"][:15]]}
    elif topic == "trends":
        res = {"rising": [(t["ptype"], round(t["share_change"], 2)) for t in r["trends"]["rising"][:8]],
               "falling": [(t["ptype"], round(t["share_change"], 2)) for t in r["trends"]["falling"][:8]]}
    else:
        res = {"best_sellers": [(x["name"], x["ptype"], x["units"]) for x in r["top_by_season"].get(season, [])[:10]], "season": season}
    return {"result": res, "summary": f"Real data: {topic.replace('_', ' ')}"}


def forecast(a, ctx):
    from fitpath.forecast import Forecaster, backtest
    from fitpath.sim.sizing import SIZES
    start, days = date.fromisoformat(a["start"]), max(1, min(62, int(a.get("days", 7))))
    size = SIZES[a.get("size") or "medium"]
    pr = Forecaster(CUTOFF if start > DATA_END else start).predict(start, days)
    bt_path = OUT / "backtest_2020-06-29_56.json"
    bt = json.loads(bt_path.read_text()) if bt_path.exists() else backtest(date(2020, 6, 29), 56)
    rows = [{"date": r.d.date().isoformat(), "weekday": DOW[r.d.dayofweek], "index": round(float(r.index), 2),
             "expected_visitors": [round(size["visitors"][0] * r.index), round(size["visitors"][1] * r.index)]} for r in pr.itertuples()]
    return {"result": {"days": rows, "mean_daily_error": bt["wape_daily"], "method": "same period a year earlier, corrected for the recent trend"},
            "summary": f"Forecast for {days} days from {start:%d %b %Y}"}


def analyze_period(a, ctx):
    from fitpath.api import _get_demand, diagnosis
    from fitpath.forecast import ForecastDemand
    from fitpath.sim.engine import SimConfig, Simulation, load_personas
    from fitpath.sim.layout import variant
    start, days, mode, cutoff = _period(a["start"], a.get("days", 7))
    c = store.store_constraints()
    lay = variant(name=a.get("title") or f"Analysis {start:%d %b}", tills_open=c["tills_open"], cabins_open=c["cabins_open"])
    demand = ForecastDemand(cutoff, start, days, base=_get_demand()) if mode == "future" else _get_demand()
    cfg = SimConfig(start=start, days=days, store_size=a.get("size") or "medium", layout=lay, seed=42, run_id=store.new_id("run"))
    Simulation(cfg, load_personas(), demand).run(on_progress=lambda k, n: ctx.progress(k, n, "days simulated"))
    d = diagnosis(cfg.run_id)
    t = d["totals"]
    res = {"run_id": cfg.run_id, "period": f"{start} for {days} days ({'forecast' if mode == 'future' else 'real'})",
           "visits": t["visits"], "conversion": t["conversion"], "revenue": t["revenue"], "lost_sales": t["lost_eur"],
           "problems": [{"title": f["title"], "lost_eur": f["loss_eur"], "detail": f["text"]} for f in d["findings"]], "constraints_from_notes": c}
    return {"result": res, "summary": f"{t['visits']:,} visits · conversion {t['conversion']:.0%} · revenue {_eur(t['revenue'])}",
            "ui": {"show_run": cfg.run_id, "label": lay["name"]}}


def diagnosis_tool(a, ctx):
    from fitpath.api import diagnosis, runs
    rid = a.get("run_id") or (runs()[0]["run_id"] if runs() else None)
    if not rid:
        return {"result": {"error": "no analysis available yet: use analyze_period"}}
    d = diagnosis(rid)
    return {"result": {"run_id": rid, "problems": [{"title": f["title"], "lost_eur": f["loss_eur"], "detail": f["text"]} for f in d["findings"]], "totals": d["totals"]},
            "summary": f"Diagnosis of {rid}"}


def test_strategy(a, ctx):
    from fitpath.sim.experiment import run_experiment, showcase
    st = strat.normalize(a["strategy"])
    if len(st) <= 1:
        return {"result": {"error": "the strategy has no valid lever"}}
    start, days, mode, cutoff = _period(a["start"], a.get("days", 3))
    size, reps = a.get("size") or "medium", max(2, min(8, int(a.get("reps", 4))))
    c = store.store_constraints()
    e = run_experiment(st, start, days, size, reps, mode, cutoff, tills_open=c["tills_open"], cabins_open=c["cabins_open"],
                       on_progress=lambda k, n: ctx.progress(k, n, "simulations"), label=st["name"])
    ctx.progress(0, 1, "peak day for the 3D")
    show = showcase(st, start, days, size, mode, cutoff, c["tills_open"], c["cabins_open"], st["name"])
    s = e["summary"]
    store.link_experiment(st["name"], e["id"], e["verdict"], s["profit"]["delta"])
    res = {"experiment_id": e["id"], "verdict": e["verdict"], "levers": e["levers"], "period": f"{start} for {days} days ({mode})", "replicas": reps,
           "net_profit_eur": _d(s["profit"]), "revenue_eur": _d(s["revenue"]), "conversion_points": _pts(s["conversion"]),
           "till_wait_s": _d(s["till_wait_s"]), "fitting_abandon_points": _pts(s["fit_abandon"]),
           "lost_at_tills_eur": _d(s["lost_till"]), "baseline": {"revenue": s["revenue"]["base"], "conversion": s["conversion"]["base"]}}
    return {"result": res, "summary": f"{e['verdict'].upper()} · net profit {_eur(s['profit']['delta'], True)}",
            "ui": {"show_run": show, "label": st["name"], "experiment_id": e["id"]}}


def optimize(a, ctx):
    from fitpath.sim.experiment import showcase
    from fitpath.sim.search import search
    start, days, mode, cutoff = _period(a["start"], a.get("days", 3))
    size = a.get("size") or "medium"
    r = search(start, days, size, mode, cutoff, n_explore=16, on_progress=lambda ph, k, n: ctx.progress(k, n, ph))
    best = max(r["verified"], key=lambda v: v["summary"]["profit"]["delta"])
    c = store.store_constraints()
    show = showcase(best["strategy"], start, days, size, mode, cutoff, c["tills_open"], c["cabins_open"], "Best found")
    res = {"combinations_evaluated": r["settings"]["n_candidates"], "simulated": r["settings"]["n_explore"],
           "verified": [{"experiment_id": v["experiment_id"], "levers": v["label"], "verdict": v["verdict"], "net_profit_eur": _d(v["summary"]["profit"])} for v in r["verified"]]}
    return {"result": res, "summary": f"{r['settings']['n_candidates']:,} combinations · best: {best['verdict']}",
            "ui": {"show_run": show, "label": "Best strategy found by the optimizer", "experiment_id": best["experiment_id"]}}


def optimize_layout(a, ctx):
    from fitpath.sim.experiment import showcase
    from fitpath.sim.layout_opt import optimize as optimize_blocks
    start, days, mode, cutoff = _period(a["start"], a.get("days", 2))
    size = a.get("size") or "medium"
    c = store.store_constraints()
    r = optimize_blocks(start, days, size, mode, cutoff, c["tills_open"], c["cabins_open"], n_explore=14,
                        on_progress=lambda ph, k, n: ctx.progress(k, n, ph))
    best = max(r["verified"], key=lambda v: v["summary"]["profit"]["delta"])
    st = {"name": "Optimized layout", "layout": {"assign": best["assign"]}}
    ctx.progress(0, 1, "peak day for the 3D")
    show = showcase(st, start, days, size, mode, cutoff, c["tills_open"], c["cabins_open"], "Optimized layout")
    res = {"feasible_layouts": r["settings"]["n_feasible"], "simulated": r["settings"]["n_explore"], "visited_by_tabu_search": r["tabu"]["visited"],
           "current_store_adjacency": r["base"]["adjacency"],
           "verified": [{"experiment_id": v["experiment_id"], "moves": v["moved"], "verdict": v["verdict"], "adjacency": v["adjacency"],
                         "net_profit_eur": _d(v["summary"]["profit"]), "revenue_eur": _d(v["summary"]["revenue"])} for v in r["verified"]],
           "rules": r["rules"], "ranker_loo_correlation": r["ranker"]["loo_corr"]}
    return {"result": res, "summary": f"{r['settings']['n_feasible']:,} feasible layouts · best: {best['verdict']}",
            "ui": {"show_run": show, "label": "Optimized layout", "experiment_id": best["experiment_id"]}}


def show_in_twin(a, ctx):
    from fitpath.sim.layout import BASE_LAYOUT
    st = strat.normalize(a["strategy"])
    lay = strat.apply_layout(BASE_LAYOUT, st, st["name"])
    return {"result": {"shown": True, "levers": strat.describe(st)}, "summary": "Preview in the 3D",
            "ui": {"preview": {"layout": lay, "strategy": st, "levers": strat.describe(st)}}}


def propose_suggestions(a, ctx):
    items = []
    for s in a.get("suggestions", [])[:8]:
        st = strat.normalize(s.get("strategy") or {})
        if len(st) > 1:
            items.append({"title": s.get("title") or st["name"], "why": s.get("reason", ""), "source": s.get("source", "real"),
                          "strategy": st, "levers": strat.describe(st)})
    store.add_suggestions(items, ctx.session_id)
    return {"result": {"added": len(items)}, "summary": f"{len(items)} suggestion{'s' if len(items) != 1 else ''} in the panel"}


def add_to_plan(a, ctx):
    from fitpath.api import PlanItem, add_plan
    items = add_plan(PlanItem(experiment_id=a["experiment_id"], note=a.get("note")))
    return {"result": {"strategies_in_plan": len(items)}, "summary": "Added to the plan"}


def save_note(a, ctx):
    eff = {k: a[k] for k in ("fitting_rooms_out_of_order", "tills_available") if a.get(k) is not None}
    store.add_note(a["text"], eff)
    return {"result": {"saved": True, "current_constraints": store.store_constraints()},
            "summary": f"Note saved{' · constraint for the simulations' if eff else ''}"}


def read_context(a, ctx):
    from fitpath.api import plan
    from fitpath.sim import behavior
    from fitpath.sim.experiment import list_experiments
    exps = [{"id": e["id"], "name": e["label"], "verdict": e["verdict"], "levers": e["levers"], "start": e["settings"]["start"],
             "net_profit": e["summary"]["profit"]["delta"]} for e in list_experiments()[:10]]
    model = [{"effect": p["name"], "what": p["what"], "sources": [behavior.SOURCES[s]["label"] for s in p["sources"]]} for p in behavior.PARAMS]
    store_ = {"area_m2": store_plan.STORE["w"] * store_plan.STORE["d"], "departments": {z: store_plan.BLOCKS[b]["name"] for z, b in store_plan.DEFAULT_ASSIGN.items()},
              "fitting_rooms": len(store_plan.CABINS), "tills": len(store_plan.TILLS)}
    return {"result": {"notes": store.notes(), "plan": plan(), "recent_tests": exps, "store": store_, "behaviour_model": model},
            "summary": "Notes, plan, tests, store and model assumptions"}


def _approve_days(n):
    return lambda a: int(a.get("days", 0) or 0) > n


PERIOD = {"start": {"type": "string", "description": "YYYY-MM-DD"}, "days": {"type": "integer"}, "size": SIZE}

TOOLS = {
    "sales_data": {"fn": sales_data, "title": "Reads the real sales data", "ask": lambda a: False,
                   "desc": "Insights from real H&M sales: overview (period, receipts, customers, items, revenue, average receipt, items per receipt), seasonality by category, weekdays, payday effect, markdowns, products bought together, trends, best sellers.",
                   "params": {"type": "object", "properties": {"topic": {"type": "string", "enum": ["overview", "seasonality", "weekdays", "payday", "markdowns", "bought_together", "trends", "best_sellers"]},
                                                               "season": {"type": "string", "enum": ["Winter", "Spring", "Summer", "Autumn"]}}, "required": ["topic"]}},
    "forecast": {"fn": forecast, "title": "Forecasts the visits", "ask": lambda a: False,
                 "desc": "Day-by-day forecast of store visits for a period (also in the future), with the method's mean error.",
                 "params": {"type": "object", "properties": PERIOD, "required": ["start", "days"]}},
    "analyze_period": {"fn": analyze_period, "title": "Analyzes the store", "ask": _approve_days(3),
                       "desc": "Simulates the current store over a period (real if past, forecast if future) and returns the diagnosis: where sales are lost. The result shows in the 3D.",
                       "params": {"type": "object", "properties": {**PERIOD, "title": {"type": "string"}}, "required": ["start", "days"]}},
    "diagnosis": {"fn": diagnosis_tool, "title": "Reads the diagnosis", "ask": lambda a: False,
                  "desc": "Diagnosis of an analysis already run (the latest one if run_id is missing).",
                  "params": {"type": "object", "properties": {"run_id": {"type": "string"}}}},
    "test_strategy": {"fn": test_strategy, "title": "Tests a strategy in the twin", "ask": lambda a: int(a.get("days", 0) or 0) > 3 or int(a.get("reps", 4) or 4) > 4,
                      "desc": "Compares the current store with the strategy in the digital twin, on the same shoppers, with several replicas. Returns the differences with a 90% interval and a verdict. The peak day with the strategy shows in the 3D.",
                      "params": {"type": "object", "properties": {"strategy": STRATEGY, **PERIOD, "reps": {"type": "integer", "description": "replicas, default 4"}}, "required": ["strategy", "start", "days"]}},
    "optimize": {"fn": optimize, "title": "Searches for the best strategy", "ask": lambda a: True,
                 "desc": "Automatic optimizer: generates thousands of lever combinations, simulates a sample, a neural network scores the rest and the twin verifies the best 3. About 2-3 minutes.",
                 "params": {"type": "object", "properties": PERIOD, "required": ["start", "days"]}},
    "optimize_layout": {"fn": optimize_layout, "title": "Optimizes the department layout", "ask": lambda a: True,
                        "desc": "Finds the best placement of the departments in the store's blocks (block layout, after Ozgormus): considers every layout allowed by the rules, simulates a sample, a neural network guides a tabu search over swaps and the twin verifies the best 3. About 3-5 minutes. The best one shows in the 3D.",
                        "params": {"type": "object", "properties": PERIOD, "required": ["start", "days"]}},
    "show_in_twin": {"fn": show_in_twin, "title": "Shows it in the 3D", "ask": lambda a: False,
                     "desc": "Immediately shows in the 3D how the store would change with a strategy (moved departments, window display, promotions), without simulating.",
                     "params": {"type": "object", "properties": {"strategy": STRATEGY}, "required": ["strategy"]}},
    "propose_suggestions": {"fn": propose_suggestions, "title": "Adds suggestions to the panel", "ask": lambda a: False,
                            "desc": "Puts the strategies you recommend in the user's Suggestions panel. Use it every time you propose concrete strategies.",
                            "params": {"type": "object", "properties": {"suggestions": {"type": "array", "items": {"type": "object", "properties": {
                                "title": {"type": "string"}, "reason": {"type": "string"}, "source": {"type": "string", "enum": ["real", "simulated", "user"]}, "strategy": STRATEGY},
                                "required": ["title", "reason", "strategy"]}}}, "required": ["suggestions"]}},
    "add_to_plan": {"fn": add_to_plan, "title": "Adds it to the plan", "ask": lambda a: False,
                    "desc": "Adds a strategy already tested to the plan (needs experiment_id). Only when the user asks for it.",
                    "params": {"type": "object", "properties": {"experiment_id": {"type": "string"}, "note": {"type": "string"}}, "required": ["experiment_id"]}},
    "save_note": {"fn": save_note, "title": "Saves a note about the store", "ask": lambda a: False,
                  "desc": "Saves something the user noticed in the real store. If it is about fitting rooms out of order or tills available, give the number: the simulations will use it.",
                  "params": {"type": "object", "properties": {"text": {"type": "string"}, "fitting_rooms_out_of_order": {"type": "integer"}, "tills_available": {"type": "integer"}}, "required": ["text"]}},
    "read_context": {"fn": read_context, "title": "Reads notes, plan and tests", "ask": lambda a: False,
                     "desc": "Saved store notes, strategies in the plan, latest tests, the store floor plan and the behaviour model's assumptions with their sources.",
                     "params": {"type": "object", "properties": {}}},
}
