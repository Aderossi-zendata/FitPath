"""Turn computed statistics into recommendations for the store manager.

The numbers are always computed in Python (analytics + simulation); the model only explains them and
proposes actions. Every recommendation states whether it rests on real H&M data or on the simulation.
Without an LLM configured, a rule-based fallback produces the same structure so the demo still works.
"""
import json
from datetime import date

from fitpath.analytics.real import SEASONS
from fitpath.llm.provider import LLMUnavailable, complete_json

SYSTEM = """You are the analyst of FitPath, a digital twin for fast-fashion clothing stores.
You receive statistics that are already computed: REAL H&M sales data (seasonality, baskets, trends) and the results of a
SIMULATION of the store (paths, fitting rooms, tills, departments). Write concrete advice for the store manager.
Rules:
- use only the numbers provided, quoting them; never invent data
- every recommendation states its source: "real" if it rests on H&M sales, "simulated" if it rests on the simulation
- actions must be doable in a store (layout, staff, assortment, promotions, opening hours)
- plain English, short sentences
Reply with a JSON object: {"headline": str, "recommendations": [{"title": str, "why": str, "action": str,
"impact": str, "source": "real"|"simulated", "priority": "high"|"medium"|"low", "area": str}]} with 5-7 recommendations."""


def season_of(d: date) -> str:
    return SEASONS[(d.month % 12) // 3]


KIDS = {"Dresses/Skirts girls", "Woven/Jersey/Knitted mix Baby"}  # kept out of the adult-fashion advice


def _cats(rows, n):
    return [(r["cat"], round(r["lift"], 2)) for r in rows if r["cat"] not in KIDS][:n]


def build_context(real: dict, sim: dict | None, today: date) -> dict:
    season = season_of(today)
    nxt = SEASONS[(SEASONS.index(season) + 1) % 4]
    ctx = {
        "simulated_date": today.isoformat(), "season": season, "next_season": nxt,
        "real": {
            "strong_categories_this_season": _cats(real["season_lift"][season]["up"], 5),
            "weak_categories_this_season": _cats(real["season_lift"][season]["down"], 4),
            "strong_categories_next_season": _cats(real["season_lift"][nxt]["up"], 5),
            "receipts_by_weekday": {r["dow"]: r["baskets"] for r in real["weekday"]["days"]},
            "day_of_month_index": {r["dom"]: r["index"] for r in real["month_day"] if r["dom"] in (1, 5, 10, 15, 20, 25, 28, 30)},
            "weeks_with_most_discount": [(r["week"], r["avg_discount"]) for r in sorted(real["markdowns"], key=lambda r: -r["avg_discount"])[:4]],
            "category_pairs_bought_together": [(r["a"], r["b"], round(r["lift"], 1)) for r in real["basket_types"]["top_lift"][:8]],
            "rising_types": [(r["ptype"], round(r["share_change"], 2)) for r in real["trends"]["rising"][:5]],
            "falling_types": [(r["ptype"], round(r["share_change"], 2)) for r in real["trends"]["falling"][:5]],
        },
    }
    if sim:
        peak = max(sim["hourly"], key=lambda h: h["fit_queue"] + h["till_queue"]) if sim["hourly"] else {}
        ctx["simulated"] = {
            "days": len(sim["daily"]), "totals": sim["totals"], "fitting_rooms": sim["fitting"], "tills": sim["tills"],
            "departments": [{k: z[k] for k in ("name", "reach", "conversion", "dwell_s", "revenue")} for z in sim["zones"]],
            "profiles": [{k: p[k] for k in ("name", "visits", "conversion", "avg_ticket", "fitting")} for p in sim["personas"]],
            "queue_peak": peak, "receipt_matching": sim["matching"],
        }
    return ctx


def fallback(ctx: dict) -> dict:
    """Rule-based recommendations used when no LLM is configured."""
    recs, r = [], ctx["real"]
    up = ", ".join(c for c, _ in r["strong_categories_next_season"][:3])
    recs.append({"title": f"Get the assortment ready for {ctx['next_season'].lower()}", "source": "real", "priority": "high", "area": "Assortment",
                 "why": f"In the H&M data, these sell far above average in {ctx['next_season'].lower()}: {up}.",
                 "action": f"Bring {up} forward in the window and in New in during the last 3 weeks of the current season.",
                 "impact": "More visibility for the categories about to grow."})
    a, b, lift = r["category_pairs_bought_together"][0]
    recs.append({"title": f"Display {a} and {b} together", "source": "real", "priority": "medium", "area": "Layout",
                 "why": f"They end up in the same receipt {lift} times more often than by chance.", "action": f"Put {b} next to {a} and vice versa.",
                 "impact": "More items per receipt."})
    best = max(r["day_of_month_index"].items(), key=lambda kv: kv[1])
    recs.append({"title": "Focus promotions and staff at month end", "source": "real", "priority": "medium", "area": "Staff",
                 "why": f"Daily sales reach index {best[1]} around day {best[0]} of the month (average = 100).",
                 "action": "Plan more staff at the tills and launch promotions in the last days of the month.", "impact": "Makes the most of the payday peak."})
    s = ctx.get("simulated")
    if s:
        f = s["fitting_rooms"]
        if f.get("conv_long_wait") is not None and f["conv_short_wait"] - f["conv_long_wait"] > .05:
            recs.append({"title": "Cut fitting-room waits at peak hours", "source": "simulated", "priority": "high", "area": "Fitting rooms",
                         "why": f"With waits over {f['long_wait_threshold_s'] // 60} minutes conversion drops from {f['conv_short_wait']:.0%} to {f['conv_long_wait']:.0%}; €{f['lost_eur']:,.0f} lost to people giving up.",
                         "action": "Put a fitting-room attendant on at peak times to turn the cabins around faster.", "impact": f"Up to €{f['lost_eur']:,.0f} recovered in the period."})
        low = min(s["departments"], key=lambda z: z["conversion"])
        recs.append({"title": f"{low['name']}: many stops, few sales", "source": "simulated", "priority": "medium", "area": "Layout",
                     "why": f"Department conversion {low['conversion']:.0%} with an average stop of {low['dwell_s'] / 60:.1f} minutes.",
                     "action": "Review the display, the sizes on hand and the price tags in this department.", "impact": "More purchases from people who already stop."})
    return {"headline": f"{len(recs)} actions for {ctx['season'].lower()}", "recommendations": recs, "generated_by": "rules"}


def advise(real: dict, sim: dict | None, today: date) -> dict:
    ctx = build_context(real, sim, today)
    try:
        res, model = complete_json(SYSTEM, "Statistics:\n" + json.dumps(ctx, ensure_ascii=False, default=str))
        res["generated_by"], res["model"] = "llm", model
    except LLMUnavailable as e:
        res = fallback(ctx)
        res["note"] = f"Advice generated by rules: {e}"
    res["context"] = ctx
    return res


# ---------- strategies the twin can test ----------
STRATEGY_SYSTEM = """You are the head of retail at FitPath, the digital twin of an H&M-style fast-fashion store (2,000 m², 8 departments in blocks,
8 fitting rooms always open, 6 tills of which 3 open + 1 extra). From the statistics provided, propose 6 CONCRETE STRATEGIES, DIFFERENT FROM EACH OTHER,
to test in the digital twin. There are many possible strategies: promotions (3-for-2, second item half price, timed discounts), end-of-season sales,
bundles of products bought together, window display and merchandising, layout, cross-merchandising, staff. Vary them: at most
one strategy about tills or fitting rooms, the others about product, price and merchandising. Combine 1-3 levers per strategy.

Available levers (use ONLY these keys):
- "feature": {"garment": category, "days"}  category displayed in the entrance window; categories: Knitwear, Outdoor, Trousers Denim, Trousers,
   Dresses Ladies, Blouses, Shirts, Skirts, Jersey Basic, Jersey Fancy, Socks and Tights, Under-, Nightwear, Swimwear, Shorts, Shoes, Accessories, Dressed
- "clearance": {"pct": 10-60, "days"}  end-of-season sale on the categories out of season now (listed in the data)
- "multibuy": {"zone": "ladies"|"divided"|"men"|"kids"|"denim"|"basics"|"accessories"|"new_in", "type": "3x2"|"2x50", "days"}
- "bundle": [{"a": product type, "b": product type, "pct": 5-40}]  use the pairs bought together from the data (exact names)
- "discount": [{"zone": department, "pct": 5-50, "days"}]  discount on a department (not together with clearance)
- "cross_merch": [{"from": department, "to": department}]  products of department "to" also displayed in department "from"
- "layout": {"swap": [department, department]}  swaps two departments (New in must stay at the front, Ladies next to the fitting rooms)
- "extra_till": {"from": 10-19, "to": 11-20, "days"}  one more till;  "fitting_attendant": {"from", "to", "days"}  fitting-room attendant
"days": "all" | "weekend" | "weekdays" | "month_end".

Examples: {"name": "Knitwear in the window for autumn", "feature": {"garment": "Knitwear", "days": "all"}};
{"name": "Clear out the swimwear", "clearance": {"pct": 40, "days": "all"}};
{"name": "3-for-2 on basics at weekends", "multibuy": {"zone": "basics", "type": "3x2", "days": "weekend"}};
{"name": "Complete pyjamas", "bundle": [{"a": "Night gown", "b": "Pyjama bottom", "pct": 20}]}.
Rules: justify with the numbers provided; give the source ("real" if from sales data, "simulated" if from the store diagnosis);
do not propose opening or closing fitting rooms.
Reply with JSON: {"strategies": [{"name": str, "rationale": str, "source": "real"|"simulated", "expected": str, ...levers}]}"""

EXPLAIN_SYSTEM = """You are the analyst of FitPath. You receive the result of a test in the digital twin: mean differences between the scenario with
the strategy and the baseline, with a 90% confidence interval, over several replicas. Explain in plain English (3-4 sentences) what happened and why,
saying clearly whether the result is reliable (the interval does not cross zero) or uncertain. Then propose ONE improved variant using the
same available levers. Reply with JSON: {"explanation": str, "next": {"name": str, "rationale": str, ...levers}}"""


def _strategy_fallback(ctx: dict) -> list[dict]:
    out = [
        {"name": "Extra till on weekend afternoons", "source": "real", "extra_till": {"from": 15, "to": 20, "days": "weekend"},
         "rationale": "Saturday is the day with the most receipts in the H&M data: queues at the tills cost sales.", "expected": "Fewer people leaving the till queue"},
        {"name": "Fitting-room attendant at peak hours", "source": "simulated", "fitting_attendant": {"from": 15, "to": 20, "days": "all"},
         "rationale": "People who wait long for a fitting room buy much less.", "expected": "Fewer fitting-room drop-outs"},
        {"name": "Accessories next to Denim", "source": "real", "cross_merch": [{"from": "denim", "to": "accessories"}],
         "rationale": "Accessories and garments often end up in the same receipt.", "expected": "More items per receipt"},
        {"name": "Basics promo at month end", "source": "real", "discount": [{"zone": "basics", "pct": 20, "days": "month_end"}],
         "rationale": "Sales rise in the last days of the month (payday effect).", "expected": "More purchases on peak days"},
        {"name": "Basics near the tills, Divided in the centre", "source": "simulated", "layout": {"swap": ["basics", "divided"]},
         "rationale": "Departments near the entrance and the tills attract more stops.", "expected": "More impulse purchases"},
    ]
    cur = ctx.get("current_season_category_lift") or {}
    top = next((g for g in cur if g not in ("Accessories",)), None)
    if top:
        out.insert(0, {"name": f"{top} in the window", "source": "real", "feature": {"garment": top, "days": "all"},
                       "rationale": f"This season {top} sells {cur[top]:.1f} times its yearly average.", "expected": "More impulse purchases at the entrance"})
    if ctx.get("out_of_season_categories_for_sale"):
        out.insert(1, {"name": "End-of-season sale", "source": "real", "clearance": {"pct": 30, "days": "all"},
                       "rationale": "Out-of-season categories: " + ", ".join(ctx["out_of_season_categories_for_sale"][:4]) + ".", "expected": "Free up space and recover margin"})
    out.append({"name": "3-for-2 on basics at weekends", "source": "real", "multibuy": {"zone": "basics", "type": "3x2", "days": "weekend"},
                "rationale": "Basics are repeat, low-price purchases: a 3-for-2 raises the items per receipt.", "expected": "Higher average receipt"})
    return out


def propose_strategies(real: dict, sim: dict | None, today: date, period: dict, diagnosis: dict | None = None) -> dict:
    from fitpath.sim import strategy as strat
    ctx = build_context(real, sim, today)
    ctx["period_to_simulate"] = period
    season = season_of(today)
    lift = real.get("season_lift_full", {}).get(season, {})
    nxt = SEASONS[(SEASONS.index(season) + 1) % 4]
    ctx["current_season_category_lift"] = {g: v for g, v in sorted(lift.items(), key=lambda kv: -kv[1]) if g not in ("Unknown", "Special Offers")}
    ctx["next_season_category_lift"] = {g: v for g, v in sorted(real.get("season_lift_full", {}).get(nxt, {}).items(), key=lambda kv: -kv[1])[:6]}
    ctx["out_of_season_categories_for_sale"] = [g for g, v in lift.items() if v < .8]
    ctx["product_type_pairs_bought_together"] = [(r["a"], r["b"], round(r["lift"], 1), r["baskets"]) for r in real["basket_types"]["top_lift"][:12]]
    if diagnosis:
        ctx["store_diagnosis"] = [{"problem": f["title"], "lost_eur": f["loss_eur"], "detail": f["text"]} for f in diagnosis["findings"]]
    try:
        res, model = complete_json(STRATEGY_SYSTEM, "Statistics:\n" + json.dumps(ctx, ensure_ascii=False, default=str))
        raw, by = res.get("strategies") or [], {"generated_by": "llm", "model": model}
    except LLMUnavailable as e:
        raw, by = _strategy_fallback(ctx), {"generated_by": "rules", "note": f"Strategies generated by rules: {e}"}
    out = []
    for s in raw:
        n = strat.normalize(s)
        if len(n) > 1:   # at least one valid lever
            src = s.get("source")
            src = {"reale": "real", "simulato": "simulated"}.get(src, src)
            out.append({**n, "rationale": str(s.get("rationale", ""))[:600], "source": src if src in ("real", "simulated") else "real",
                        "expected": str(s.get("expected", ""))[:200], "levers": strat.describe(n)})
    return {**by, "strategies": out, "period": period}


def explain_experiment(exp: dict) -> dict:
    from fitpath.sim import strategy as strat
    payload = {k: exp[k] for k in ("label", "levers", "summary", "verdict", "settings")}
    try:
        res, model = complete_json(EXPLAIN_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str))
        raw = res.get("next") or {}
        if isinstance(raw.get("levers"), dict):   # some models nest the levers
            raw = {**raw, **raw["levers"]}
        nxt = strat.normalize(raw)
        return {"explanation": res.get("explanation", ""), "next": {**nxt, "rationale": (res.get("next") or {}).get("rationale", ""), "levers": strat.describe(nxt)} if len(nxt) > 1 else None,
                "generated_by": "llm", "model": model}
    except LLMUnavailable as e:
        p = exp["summary"]["profit"]
        lo, hi = p["ci90"] or [p["delta"], p["delta"]]
        msg = {"worth it": "the gain is reliable.", "not worth it": "the strategy loses margin.",
               "uncertain": "the effect cannot be told apart from chance: more replicas or a stronger lever are needed."}.get(exp["verdict"], "")
        txt = f"Mean incremental profit €{p['delta']:+,.0f} (90% interval €{lo:+,.0f} / €{hi:+,.0f}): {msg}"
        return {"explanation": txt, "next": None, "generated_by": "rules", "note": str(e)}
