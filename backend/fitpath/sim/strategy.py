"""Strategy levers the digital twin can apply, and helpers to validate, describe and cost them.

A strategy is a JSON object; every field is optional:
{
  "name": "Weekend with an extra till",
  "extra_till":        {"from": 17, "to": 20, "days": "weekend"},      # opens one more till in that window
  "fitting_attendant": {"from": 15, "to": 20, "days": "all"},          # faster cabin turnover, more patient queue
  "layout":            {"swap": ["denim", "accessories"]},                # swap two departments' blocks, or
                       {"assign": {"denim": "C1", "new_in": "R1", ...}},  # a full department -> block assignment (sim/store_plan.py)
  "discount":          [{"zone": "ladies", "pct": 20, "days": "month_end"}],    # price cut on a zone
  "cross_merch":       [{"from": "denim", "to": "accessories"}],               # display B products next to A
  "multibuy":          {"zone": "basics", "type": "3x2", "days": "all"},   # 3x2 or "2x50" (second item half price)
  "clearance":         {"pct": 30, "days": "all"},                       # end-of-season sale on the categories out of season now
  "bundle":            [{"a": "Gloves", "b": "Hat/beanie", "pct": 15}],  # product types bought together in real baskets
  "feature":           {"garment": "Knitwear", "days": "all"}            # category displayed at the entrance / window
}
days: "all" | "weekend" (Sat-Sun) | "weekdays" (Mon-Fri) | "month_end" (last 7 days of the month)

Effect sizes that are not measured from data are documented here as assumptions.
"""
import calendar
from datetime import date

from fitpath.catalog import ZONE_NAMES, ZONES
from fitpath.sim import store_plan

DAYS = ("all", "weekend", "weekdays", "month_end")
GARMENT_LABEL = {"Accessories": "Accessories", "Blouses": "Blouses", "Dressed": "Formal wear", "Dresses Ladies": "Dresses", "Jersey Basic": "Basic jersey",
                 "Jersey Fancy": "Fancy jersey", "Knitwear": "Knitwear", "Outdoor": "Coats & jackets", "Shirts": "Shirts", "Shoes": "Shoes",
                 "Shorts": "Shorts", "Skirts": "Skirts", "Socks and Tights": "Socks & tights", "Swimwear": "Swimwear", "Trousers": "Trousers",
                 "Trousers Denim": "Jeans", "Under-, Nightwear": "Underwear & nightwear"}
LEGACY_DAYS = {"feriali": "weekdays", "fine_mese": "month_end"}   # day values used by older saved strategies
DISCOUNT_ZONES = ("ladies", "divided", "men", "kids", "denim", "basics", "accessories", "new_in")

# assumptions (documented, not measured)
ATTENDANT_CABIN_FACTOR = .75     # an attendant prepares cabins and sizes: 25% faster turnover
ATTENDANT_PATIENCE_FACTOR = 1.4  # people wait longer when someone manages the queue
CROSS_MERCH_P = .06              # chance to pick a product of zone B while at zone A when B is displayed there
STAFF_COST_EUR_H = 20            # cost of one extra staff hour
MULTIBUY_TAKE = (.15, .35)       # share of buyers of the zone who complete the promo bundle (sampled per run)
BUNDLE_TAKE = (.06, .18)         # chance that a buyer of A also takes B when the bundle is on (scaled by the real pair lift)
FEATURE_BASE = .05               # chance a visitor picks the featured category at the entrance, x its seasonal lift
FEATURE_VISIBILITY = (1.2, 2.0)  # extra attraction of the entrance display vs a normal spot (sampled per run)
CLEARANCE_LIFT = .8              # a category is "out of season" when it sells below 80% of its yearly share
GROSS_MARGIN = .6                # margin at full price (cost = 40% of full price)


def active(rule: dict | None, d: date, hour: float | None = None) -> bool:
    if not rule:
        return False
    days = rule.get("days", "all")
    ok = (days == "all" or (days == "weekend" and d.weekday() >= 5) or (days == "weekdays" and d.weekday() < 5)
          or (days == "month_end" and d.day > calendar.monthrange(d.year, d.month)[1] - 7))
    if hour is None or not ok:
        return ok
    return rule.get("from", 0) <= hour < rule.get("to", 24)


def normalize(s: dict) -> dict:
    """Validate and clamp a strategy (e.g. one written by the LLM); unknown fields are dropped."""
    s = _legacy_days(s)
    out = {"name": str(s.get("name") or "Strategy")[:80]}
    for k in ("extra_till", "fitting_attendant"):
        r = s.get(k)
        if isinstance(r, dict):
            f, t = int(max(10, min(19, r.get("from", 10)))), int(max(11, min(20, r.get("to", 20))))
            if t > f:
                out[k] = {"from": f, "to": t, "days": r.get("days") if r.get("days") in DAYS else "all"}
    lay = s.get("layout")
    if isinstance(lay, dict):
        assign = layout_assign({"layout": lay})
        if assign != store_plan.DEFAULT_ASSIGN and store_plan.feasible(assign)[0]:
            out["layout"] = {"assign": assign}
    disc = [d for d in (s.get("discount") or []) if isinstance(d, dict) and d.get("zone") in DISCOUNT_ZONES]
    if disc:
        out["discount"] = [{"zone": d["zone"], "pct": int(max(5, min(50, d.get("pct", 20)))), "days": d.get("days") if d.get("days") in DAYS else "all"} for d in disc[:2]]
    cm = [c for c in (s.get("cross_merch") or []) if isinstance(c, dict) and c.get("from") in ZONE_NAMES and c.get("to") in ZONE_NAMES and c["from"] != c["to"]]
    if cm:
        out["cross_merch"] = [{"from": c["from"], "to": c["to"]} for c in cm[:2]]
    dd = lambda r: r.get("days") if r.get("days") in DAYS else "all"
    mb = s.get("multibuy")
    if isinstance(mb, dict) and mb.get("zone") in DISCOUNT_ZONES:
        out["multibuy"] = {"zone": mb["zone"], "type": mb.get("type") if mb.get("type") in ("3x2", "2x50") else "3x2", "days": dd(mb)}
    cl = s.get("clearance")
    if isinstance(cl, dict):
        out["clearance"] = {"pct": int(max(10, min(60, cl.get("pct", 30)))), "days": dd(cl)}
    bu = [b for b in (s.get("bundle") or []) if isinstance(b, dict) and isinstance(b.get("a"), str) and isinstance(b.get("b"), str) and b["a"] != b["b"]]
    if bu:
        out["bundle"] = [{"a": b["a"][:40], "b": b["b"][:40], "pct": int(max(5, min(40, b.get("pct", 15))))} for b in bu[:2]]
    fe = s.get("feature")
    if isinstance(fe, dict) and fe.get("garment") in GARMENT_LABEL:
        out["feature"] = {"garment": fe["garment"], "days": dd(fe)}
    return out


def _legacy_days(v):
    """Map legacy day values ("feriali", "fine_mese") to the current keys, anywhere in the strategy."""
    if isinstance(v, dict):
        return {k: (LEGACY_DAYS.get(x, x) if k == "days" and isinstance(x, str) else _legacy_days(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_legacy_days(x) for x in v]
    return v


def layout_assign(strategy: dict | None) -> dict:
    """Department -> block assignment a strategy asks for (the current store when it does not touch the layout)."""
    lay = (strategy or {}).get("layout") or {}
    assign = dict(store_plan.DEFAULT_ASSIGN)
    if isinstance(lay.get("assign"), dict):
        cand = {**assign, **{z: b for z, b in lay["assign"].items() if z in ZONES and b in store_plan.BLOCKS}}
        if sorted(cand.values()) == sorted(store_plan.BLOCKS):
            assign = cand
        else:   # a partial assignment: move each named department, swapping with whoever was there
            for z, b in lay["assign"].items():
                if z in ZONES and b in store_plan.BLOCKS:
                    other = next(y for y, bb in assign.items() if bb == b)
                    assign = store_plan.swapped(assign, z, other)
    sw = lay.get("swap")
    if isinstance(sw, list) and len(sw) == 2 and all(z in ZONES for z in sw) and sw[0] != sw[1]:
        assign = store_plan.swapped(assign, *sw)
    return assign


def apply_layout(base: dict, strategy: dict | None, name: str | None = None) -> dict:
    lay = store_plan.build_layout(layout_assign(strategy), name or base.get("name"))
    lay["tills_open"], lay["cabins_open"] = base["tills_open"], base["cabins_open"]
    return lay


def staff_hours(strategy: dict, dates: list[date]) -> float:
    h = 0.0
    for k in ("extra_till", "fitting_attendant"):
        r = strategy.get(k)
        if r:
            h += sum(r["to"] - r["from"] for d in dates if active(r, d))
    return h


def describe(s: dict) -> list[str]:
    zn = lambda z: ZONE_NAMES.get(z, z)
    dl = {"all": "every day", "weekend": "on weekends", "weekdays": "on weekdays", "month_end": "at month end"}
    out = []
    if s.get("extra_till"):
        r = s["extra_till"]; out.append(f"Extra till open {r['from']}-{r['to']} {dl[r['days']]}")
    if s.get("fitting_attendant"):
        r = s["fitting_attendant"]; out.append(f"Fitting-room attendant {r['from']}-{r['to']} {dl[r['days']]}")
    if s.get("layout"):
        moved = store_plan.describe_assign(layout_assign(s))
        out.append("Departments moved: " + "; ".join(moved) if moved else "Layout unchanged")
    for d in s.get("discount") or []:
        out.append(f"{d['pct']}% discount on {zn(d['zone'])} {dl[d['days']]}")
    for c in s.get("cross_merch") or []:
        out.append(f"{zn(c['to'])} products also displayed in {zn(c['from'])}")
    if s.get("multibuy"):
        m = s["multibuy"]; out.append(f"{'3-for-2' if m['type'] == '3x2' else 'Second item half price'} on {zn(m['zone'])} {dl[m['days']]}")
    if s.get("clearance"):
        c = s["clearance"]; out.append(f"End-of-season sale −{c['pct']}% on out-of-season categories {dl[c['days']]}")
    for b in s.get("bundle") or []:
        out.append(f"Bundle {b['a']} + {b['b']}: −{b['pct']}% on the second item")
    if s.get("feature"):
        f = s["feature"]; out.append(f"{GARMENT_LABEL[f['garment']]} in the entrance window {dl[f['days']]}")
    return out or ["No change (baseline)"]
