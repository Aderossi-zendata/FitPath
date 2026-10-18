"""Headless agent-based simulation of the FitPath store over N days.

Each day:
  * buyers come from that day's real H&M store baskets (their real basket is the purchase intent),
    scaled to one store; browsers (no intent) are added on top
  * every visitor has a persona that drives zones, dwell, try-on, patience and walking speed
  * visitors walk the 2,000 m² store (sim/store_plan.py) on a navigation grid, use the fitting rooms and tills with queues;
    in-store effects (position, path length, adjacent departments, crowding) follow sim/behavior.py
  * the tracking layer records anonymous tracks (with noise and occasional ID switches) and
    matches every receipt to the track on the payment spot

Everything runs in simulated seconds (store time, 10:00-20:00). Outputs go to data/out/sim/<run_id>/.

Usage: python -m fitpath.sim.engine --start 2019-11-18 --days 7
"""
import argparse
import json
import math
import random
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from fitpath.catalog import ZONES
from fitpath.config import OUT
from fitpath.sim.demand import Demand
from fitpath.sim.sizing import SIZES
from fitpath.sim import strategy as strat
from fitpath import elasticity as elast_mod
from fitpath.sim.behavior import Behaviour
from fitpath.sim.layout import BASE_LAYOUT, NavGrid, block_of, covered
from fitpath.sim.store_plan import block_area

OPEN_S, CLOSE_S = 10 * 3600, 20 * 3600
# share of arrivals per opening hour (10..19); assumption, Saturday flatter and busier at midday
HOURLY = {"weekday": [5, 7, 9, 10, 8, 8, 9, 11, 12, 10], "saturday": [6, 9, 11, 12, 12, 12, 12, 11, 9, 6], "sunday": [4, 8, 10, 11, 11, 11, 10, 9, 7, 4]}
SAMPLE_EVERY = 2          # seconds between track samples
TRACK_NOISE = .18         # metres of tracking jitter
ID_SWITCH_P = .015        # chance that two tracks closer than 0.35 m swap identity at a sample


@dataclass
class SimConfig:
    start: date
    days: int = 7
    store_size: str = "medium"      # preset in sim/sizing.py: visitors and conversion range on an average day
    layout: dict = field(default_factory=lambda: BASE_LAYOUT)
    seed: int = 42
    record_tracks: bool = True
    run_id: str | None = None
    strategy: dict | None = None   # levers applied by the twin, see sim/strategy.py


class Agent:
    __slots__ = ("vid", "persona", "cid", "intent", "plan", "x", "z", "path", "state", "wait", "speed", "carried",
                 "zone_visits", "cur_zone", "enter_t", "fit", "till", "q_join", "patience", "track", "beh", "kind", "bought",
                 "lost", "outcome", "exit_t", "receipt", "tried", "rng")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, None)
        for k, v in kw.items():
            setattr(self, k, v)


class Simulation:
    def __init__(self, cfg: SimConfig, personas: dict, demand: Demand | None = None):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.personas = {p["id"]: p for p in personas["personas"]}
        self.persona_weights = [(p["id"], p["share_store_visits"]) for p in personas["personas"]]
        self.grid = NavGrid(cfg.layout)
        self.demand = demand or Demand()
        lay = cfg.layout
        self.cabins = [{"x": x, "open": i < lay["cabins_open"], "occ": None} for i, x in enumerate(lay["cabins"])]
        self.tills = [{"x": x, "n": i + 1, "open": i < lay["tills_open"], "queue": [], "serving": None, "done_t": 0} for i, x in enumerate(lay["tills"])]
        self.visits, self.zone_rows, self.receipts, self.tracks, self.events = [], [], [], [], []
        self.hourly = []
        self.next_vid, self.next_track, self.next_receipt = 1, 1, 30001
        self.day_plan = []
        self.strategy = cfg.strategy or {}
        # in-store behaviour: parameters drawn per run from the literature ranges (sim/behavior.py)
        self.bm = Behaviour(cfg.seed)
        self.vis = self.bm.zone_attraction(lay)        # where a department stands changes how much it attracts
        self.neigh = self.bm.neighbours(lay)           # adjacent departments often bought together
        self.area = {z: block_area(b) for z, b in lay["assign"].items()}
        self.in_zone = {z: 0 for z in ZONES}
        self.ent, self.fitp, self.pay_z = lay["entrance"], lay["fitting"], lay["pay_z"]
        self.attendant = False
        # price elasticity: measured where the data allows it, otherwise sampled in the reference range per run
        er = random.Random(cfg.seed * 7 + 1)
        el = elast_mod.load()["by_zone"]
        self.elasticity = {z: (v["elasticity"] if v["identified"] else er.uniform(*v["range"])) for z, v in el.items()}
        self.elasticity.setdefault("new_in", self.elasticity.get("ladies", -1.2))
        self.day_disc = {}
        self.base_tills = lay["tills_open"]
        # data used by the promotion levers
        real_path = OUT / "analytics_real_store.json"
        real = json.loads(real_path.read_text(encoding="utf-8")) if real_path.exists() else {}
        self.season_lift = real.get("season_lift_full", {})
        self.pair_lift = {(r["a"], r["b"]): r["lift"] for r in real.get("basket_types", {}).get("top_lift", [])}
        self.mb_take = er.uniform(*strat.MULTIBUY_TAKE)
        self.bundle_take = er.uniform(*strat.BUNDLE_TAKE)
        self.feat_vis = er.uniform(*strat.FEATURE_VISIBILITY)
        self.day_clear, self.day_mb, self.day_feat = {}, None, None

    # ---------- setup ----------
    def _pick_persona(self, rng=None):
        r, acc = (rng or self.rng).random() * sum(w for _, w in self.persona_weights), 0
        for pid, w in self.persona_weights:
            acc += w
            if r <= acc:
                return pid
        return self.persona_weights[-1][0]

    def _arrival_second(self, d: date, rng) -> int:
        prof = HOURLY["saturday" if d.weekday() == 5 else "sunday" if d.weekday() == 6 else "weekday"]
        h = rng.choices(range(len(prof)), weights=prof)[0]
        return OPEN_S + h * 3600 + rng.randrange(3600)

    def _plan(self, beh: dict, intent_zones: list, rng, min_zones: int = 1) -> list:
        zw = beh["derived"]["zone_weights"]
        extra = min(4, int(rng.expovariate(1 / max(.2, beh["assumed"]["extra_zones"]))))
        extra = max(extra, min_zones - len(set(intent_zones)))
        zones = list(dict.fromkeys(intent_zones))
        for _ in range(extra):
            cand = [z for z in ZONES if z not in zones]
            if not cand:
                break
            zones.append(rng.choices(cand, weights=[(zw.get(z, .05) + .03) * self.vis.get(z, 1.0) for z in cand])[0])
        # visit order: mostly the nearest zone next, sometimes not (real paths are far from the shortest tour)
        pos, order = (self.ent["x"], self.ent["z_in"]), []
        while zones:
            if len(zones) > 1 and rng.random() < self.bm.detour:
                z = rng.choice(zones)
            else:
                z = min(zones, key=lambda q: min((math.hypot(pos[0] - x, pos[1] - y) for x, y in self.grid.stand[q][::7]), default=99))
            order.append(z)
            zones.remove(z)
            pos = self.grid.stand[z][0]
        return order

    def _rng(self, d: date, *key) -> random.Random:
        """Deterministic stream per (run seed, day, key): baseline and strategy runs with the same seed see the
        same visitors and the same individual decisions (common random numbers), so differences come from the strategy."""
        return random.Random("-".join(map(str, (self.cfg.seed, d.toordinal(), *key))))

    def _visitors(self, d: date, baskets: list) -> list:
        # store size gives an average-day range; the real H&M index of the date scales it
        size = SIZES[self.cfg.store_size]
        vol = self.demand.volume_index(d)
        r = self._rng(d, "day")
        visitors = max(20, round(r.uniform(*size["visitors"]) * vol))
        conv = r.uniform(*size["conversion"])
        n_buy = min(len(baskets), max(5, round(visitors * conv)))
        chosen = r.sample(baskets, n_buy)
        self.day_plan.append({"date": d.isoformat(), "index": round(vol, 3), "visitors": visitors, "intent_conversion": round(conv, 3), "buyers": n_buy})
        out = []
        for b in chosen:
            ra = self._rng(d, "buyer", b["cid"])
            pid = b["persona"] if b["persona"] is not None else self._pick_persona(ra)
            out.append((self._arrival_second(d, ra), "buyer", pid, b["cid"], b["items"], ("buyer", b["cid"])))
        n_browse = max(0, visitors - n_buy)
        # a discount brings extra buyers for that zone (price elasticity), converted from would-be browsers
        extra = []
        re_ = self._rng(d, "discount")
        for z, (pct, uplift) in self.day_disc.items():
            with_z = [b for b in chosen if any(it["zone"] == z for it in b["items"])]
            k = round(len(with_z) * (uplift - 1))
            taken = {id(b) for b in chosen + extra}
            pool = [b for b in baskets if id(b) not in taken and any(it["zone"] == z for it in b["items"])]
            extra += re_.sample(pool, max(0, min(k, len(pool), n_browse - len(extra))))
        for g, (pct, uplift) in self.day_clear.items():
            with_g = [b for b in chosen if any(it["garment"] == g for it in b["items"])]
            k = round(len(with_g) * (uplift - 1))
            taken = {id(b) for b in chosen + extra}
            pool = [b for b in baskets if id(b) not in taken and any(it["garment"] == g for it in b["items"])]
            extra += re_.sample(pool, max(0, min(k, len(pool), n_browse - len(extra))))
        for b in extra:
            ra = self._rng(d, "buyer", b["cid"])
            pid = b["persona"] if b["persona"] is not None else self._pick_persona(ra)
            out.append((self._arrival_second(d, ra), "buyer", pid, b["cid"], b["items"], ("buyer", b["cid"])))
        self.day_plan[-1]["buyers"] = n_buy + len(extra)
        for i in range(n_browse - len(extra)):   # the converted browsers are the last ones, the others are unchanged
            ra = self._rng(d, "browser", i)
            out.append((self._arrival_second(d, ra), "browser", self._pick_persona(ra), None, [], ("browser", i)))
        return out

    # ---------- per-agent behaviour ----------
    def _spawn(self, kind, pid, cid, items, t, day_catalog, d, key):
        beh = self.personas[pid]["behaviour"]
        rng = self._rng(d, "agent", *key)
        intent = []
        for it in items:
            it = dict(it)
            if it["new"] and rng.random() < .6:
                it["zone"] = "new_in"  # new articles are displayed in the New-in area
            intent.append(it)
        top = self.cfg.layout["store"]["z0"] + self.cfg.layout["store"]["d"]
        ex = self.ent["x"] + rng.uniform(-1.5, 1.5)
        a = Agent(vid=self.next_vid, persona=pid, cid=cid, intent=intent, kind=kind, x=ex, z=top - .3, rng=rng,
                  path=[(ex, self.ent["z_in"])], state="enter", wait=0, speed=beh["assumed"]["walk_speed"] * rng.uniform(.85, 1.15),
                  carried=[], zone_visits=[], enter_t=t, beh=beh, bought=[], lost=[], tried=False,
                  patience=beh["assumed"]["queue_patience_s"] * rng.uniform(.6, 1.4), track=self.next_track)
        a.plan = self._plan(beh, [it["zone"] for it in intent], rng, min_zones=2 if kind == "browser" else 1)
        if self.day_feat and self.by_garment.get(self.day_feat):
            p_feat = strat.FEATURE_BASE * self.cur_lift.get(self.day_feat, 1.0) * self.feat_vis / 1.6
            if rng.random() < p_feat:   # the window display catches the visitor at the entrance
                a.carried.append(dict(rng.choice(self.by_garment[self.day_feat]), impulse=True, featured=True))
        self.next_vid += 1
        self.next_track += 1
        return a

    def _go_zone(self, a):
        z = a.plan.pop(0)
        a.cur_zone = z
        a.path = self.grid.path((a.x, a.z), a.rng.choice(self.grid.stand[z]))
        a.state = "walk"
        self._pass_by(a, z)

    def _pass_by(self, a, target):
        """Departments walked past on the way: a small chance of an unplanned pick, larger for attractive spots."""
        pts, (px, pz), seen = [], (a.x, a.z), {target}
        for qx, qz in a.path:
            n = max(1, int(math.hypot(qx - px, qz - pz) / 1.5))
            pts += [(px + (qx - px) * s / n, pz + (qz - pz) * s / n) for s in range(1, n + 1)]
            px, pz = qx, qz
        for x, z in pts:
            zone = block_of(self.cfg.layout, x, z, pad=1.5)
            if zone and zone not in seen:
                seen.add(zone)
                cat = self._zone_cat.get(zone)
                if cat and zone not in a.plan and a.rng.random() < self.bm.pass_impulse * self.vis.get(zone, 1.0):
                    a.carried.append(dict(a.rng.choice(cat), impulse=True, passby=True))

    def _arrive_zone(self, a, t, day_catalog):
        z, beh = a.cur_zone, a.beh
        has_intent = any(it["zone"] == z for it in a.intent)
        dwell = (a.rng.uniform(150, 420) if has_intent else a.rng.uniform(110, 330)) * (1 + beh["derived"]["novelty_attraction"] * (z == "new_in"))
        crowd = self.bm.crowd_factor(self.in_zone[z], self.area[z])   # busy departments: people pick less and leave sooner
        dwell *= .7 + .3 * crowd
        picked = [it for it in a.intent if it["zone"] == z]
        uplift = self.day_disc.get(z, (0, 1.0))[1]
        if not has_intent and day_catalog.get(z) and a.rng.random() < .07 * (1 + 2 * beh["derived"]["novelty_attraction"]) * uplift * self.vis.get(z, 1.0) * crowd:
            imp = dict(a.rng.choice(day_catalog[z]), impulse=True)
            picked.append(imp)
        if self.day_clear:
            cands = [it for it in day_catalog.get(z, []) if it["garment"] in self.day_clear]
            if cands and a.rng.random() < .05 * max(u for _, u in self.day_clear.values()):
                picked.append(dict(a.rng.choice(cands), impulse=True, clearance=True))
        for cm in self.strategy.get("cross_merch") or []:
            if cm["from"] == z and day_catalog.get(cm["to"]) and a.rng.random() < strat.CROSS_MERCH_P * self.day_disc.get(cm["to"], (0, 1.0))[1]:
                picked.append(dict(a.rng.choice(day_catalog[cm["to"]]), impulse=True, cross=True))
        for b, extra in self.neigh.get(z, []):
            if day_catalog.get(b) and a.rng.random() < self.bm.adjacency * extra * crowd:
                picked.append(dict(a.rng.choice(day_catalog[b]), impulse=True, adjacent=True))
        a.carried += picked
        self.in_zone[z] += 1
        a.zone_visits.append({"zone": z, "t": t, "dwell": round(dwell), "picked": len(picked)})
        a.state, a.wait = "dwell", dwell

    def _after_shopping(self, a, t):
        tryable = [it for it in a.carried if it["tryon"]]
        if tryable and a.fit is None and a.rng.random() < a.beh["assumed"]["fitting_prob"]:
            a.fit = {"join": None, "start": None, "end": None, "cabin": None, "abandoned": False, "items": len(tryable)}
            a.state = "to_fit"
            q = self.fitp["queue"]
            a.path = self.grid.path((a.x, a.z), (q["x"], q["z"]))
            return
        self._decide(a, t)

    def _decide(self, a, t):
        for it in a.carried:
            if it.get("impulse"):
                p = .7
            elif not it["tryon"]:
                p = .96
            elif a.fit and a.fit["abandoned"]:
                p = .4
            elif a.fit and a.fit["end"]:
                p = .85 * (1 + self.bm.attendant_conv if self.attendant else 1)
            else:
                p = .75
            pct = max(self.day_disc.get(it["zone"], (0, 1.0))[0], self.day_clear.get(it["garment"], (0, 1.0))[0])
            item = dict(it, reason="not convinced" if it["tryon"] else "changed mind", full=it["eur"], eur=round(it["eur"] * (1 - pct / 100), 2))
            (a.bought if a.rng.random() < p else a.lost).append(item)
        if a.fit and a.fit["abandoned"]:
            for it in a.lost:
                it["reason"] = "fitting-room queue"
        self._promos(a)
        if a.bought:
            till = min((x for x in self.tills if x["open"]), key=lambda x: len(x["queue"]))
            till["queue"].append(a)
            a.till, a.q_join, a.state = till, None, "to_till"
            a.path = self.grid.path((a.x, a.z), (till["x"], self.pay_z - .7 * (len(till["queue"]) - 1)))
        else:
            self._leave(a, "no purchase")

    def _promos(self, a):
        """Multi-buy and bundle promotions at the moment of deciding what to buy."""
        mb = self.day_mb
        if mb and a.bought:
            z = mb["zone"]
            mine = [it for it in a.bought if it["zone"] == z]
            target = 3 if mb["type"] == "3x2" else 2
            if 0 < len(mine) < target and self.by_zone_cat(z) and a.rng.random() < self.mb_take:
                for _ in range(target - len(mine)):
                    it = a.rng.choice(self.by_zone_cat(z))
                    a.bought.append(dict(it, full=it["eur"], impulse=True, promo=True))
            mine = sorted([it for it in a.bought if it["zone"] == z], key=lambda i: i["eur"])
            free = len(mine) // 3 if mb["type"] == "3x2" else len(mine) // 2
            for it in mine[:free]:
                it["eur"] = 0.0 if mb["type"] == "3x2" else round(it["eur"] * .5, 2)
        for bu in self.strategy.get("bundle") or []:
            for x, y in ((bu["a"], bu["b"]), (bu["b"], bu["a"])):
                has_x = any(it["ptype"] == x for it in a.bought)
                has_y = any(it["ptype"] == y for it in a.bought)
                if has_x and not has_y and self.by_ptype.get(y):
                    lift = self.pair_lift.get((x, y)) or self.pair_lift.get((y, x)) or 3.0
                    if a.rng.random() < self.bundle_take * min(1.5, lift / 10):
                        it = a.rng.choice(self.by_ptype[y])
                        a.bought.append(dict(it, full=it["eur"], eur=round(it["eur"] * (1 - bu["pct"] / 100), 2), impulse=True, promo=True))

    def by_zone_cat(self, z):
        return self._zone_cat.get(z, [])

    def _leave(self, a, outcome):
        a.outcome = a.outcome or outcome
        a.state = "exit"
        e = self.ent
        a.path = self.grid.path((a.x, a.z), (e["exit_x"], e["z_in"])) + [(e["exit_x"], e["z_out"])]

    def _move(self, a, dt=1.0):
        if not a.path:
            return True
        tx, tz = a.path[0]
        dx, dz = tx - a.x, tz - a.z
        d = math.hypot(dx, dz)
        s = a.speed * dt
        if d <= s:
            a.x, a.z = tx, tz
            a.path.pop(0)
            return not a.path
        a.x += dx / d * s
        a.z += dz / d * s
        return False

    # ---------- main loop ----------
    def run_day(self, d: date, baskets: list):
        rng = self.rng
        day_catalog = {}
        for b in baskets:
            for it in b["items"]:
                day_catalog.setdefault(it["zone"], []).append(it)
        self.day_disc = {}
        for dr in self.strategy.get("discount") or []:
            if strat.active(dr, d):
                self.day_disc[dr["zone"]] = (dr["pct"], (1 - dr["pct"] / 100) ** self.elasticity.get(dr["zone"], -1.2))
        season = ("Winter", "Spring", "Summer", "Autumn")[(d.month % 12) // 3]
        self.cur_lift = self.season_lift.get(season, {})
        cl = self.strategy.get("clearance")
        self.day_clear = {}
        if cl and strat.active(cl, d):
            el = self.elasticity.get("ladies", -1.2)
            self.day_clear = {g: (cl["pct"], (1 - cl["pct"] / 100) ** el) for g, lift in self.cur_lift.items() if lift < strat.CLEARANCE_LIFT}
        mb = self.strategy.get("multibuy")
        self.day_mb = mb if mb and strat.active(mb, d) else None
        fe = self.strategy.get("feature")
        self.day_feat = fe["garment"] if fe and strat.active(fe, d) else None
        self.by_garment, self.by_ptype = {}, {}
        for b in baskets:
            for it in b["items"]:
                self.by_garment.setdefault(it["garment"], []).append(it)
                self.by_ptype.setdefault(it["ptype"], []).append(it)
        attendant = False
        self.in_zone = {z: 0 for z in ZONES}
        self._zone_cat = day_catalog
        arrivals = sorted(self._visitors(d, baskets), key=lambda v: v[0])
        ai, active, fit_q = 0, [], []
        track_owner = {}
        t = OPEN_S
        while t < CLOSE_S + 3 * 3600 and (ai < len(arrivals) or active):
            while ai < len(arrivals) and arrivals[ai][0] <= t and t < CLOSE_S:
                _, kind, pid, cid, items, key = arrivals[ai]
                a = self._spawn(kind, pid, cid, items, t, day_catalog, d, key)
                track_owner[a.track] = a
                active.append(a)
                ai += 1
            if ai < len(arrivals) and t >= CLOSE_S:
                ai = len(arrivals)  # the door closes

            if t % 60 == 0:
                hour = t / 3600
                n_open = self.base_tills + (1 if strat.active(self.strategy.get("extra_till"), d, hour) else 0)
                for i, till in enumerate(self.tills):
                    till["open"] = i < n_open   # a closing till still serves the people already in its queue
                attendant = self.attendant = strat.active(self.strategy.get("fitting_attendant"), d, hour)
            # fitting rooms: free cabins take the head of the queue
            for i, c in enumerate(self.cabins):
                if c["open"] and c["occ"] is None and fit_q and fit_q[0].state == "fit_q":
                    a = fit_q.pop(0)
                    c["occ"] = a
                    a.fit.update(start=t, cabin=i + 1)
                    a.state, a.path = "to_cabin", [(c["x"], self.fitp["door_z"]), (c["x"], self.fitp["in_z"])]
            for a in list(fit_q):
                if a.state == "fit_q" and t - a.fit["join"] > a.patience * .6 * (strat.ATTENDANT_PATIENCE_FACTOR if attendant else 1):
                    fit_q.remove(a)
                    a.fit["abandoned"] = True
                    self._decide(a, t)
            # tills: serve the head of each queue
            for till in self.tills:
                if till["serving"] is None and till["queue"]:
                    a = till["queue"][0]
                    if a.state == "till_q" and math.hypot(a.x - till["x"], a.z - self.pay_z) < .3:
                        till["serving"] = a
                        a.state, a.wait = "serve", 60 + 15 * len(a.bought)
                        a.receipt = {"start": t}
                for a in list(till["queue"][1:]):
                    if a.state == "till_q" and a.q_join is not None and t - a.q_join > a.patience and a.rng.random() < .002:
                        till["queue"].remove(a)
                        for it in a.bought:
                            a.lost.append(dict(it, reason="till queue"))
                        a.bought = []
                        self._leave(a, "left the till queue")

            done = []
            for a in active:
                if a.wait > 0:
                    a.wait -= 1
                    if a.wait > 0:
                        continue
                    if a.state == "dwell":
                        a.state = "shop"
                        self.in_zone[a.cur_zone] -= 1
                    elif a.state == "in_cabin":
                        c = self.cabins[a.fit["cabin"] - 1]
                        c["occ"] = None
                        a.fit["end"] = t
                        a.x, a.z = c["x"], self.fitp["door_z"]
                        a.state = "shop"
                        a.plan = []
                        self._decide(a, t)
                        continue
                    elif a.state == "serve":
                        self._close_receipt(a, t, track_owner)
                        continue
                st = a.state
                if st in ("enter", "shop"):
                    if a.path and not self._move(a):
                        continue
                    if a.plan:
                        self._go_zone(a)
                    elif st == "shop":
                        self._after_shopping(a, t)
                    else:
                        a.state = "shop"
                elif st == "walk":
                    if self._move(a):
                        self._arrive_zone(a, t, day_catalog)
                elif st == "to_fit":
                    if self._move(a):
                        a.state, a.fit["join"] = "fit_q", t
                        fit_q.append(a)
                elif st == "fit_q":
                    k = fit_q.index(a) if a in fit_q else 0
                    q = self.fitp["queue"]
                    a.path = [(q["x"] + q["dx"] * k, q["z"])]
                    self._move(a)
                elif st == "to_cabin":
                    if self._move(a):
                        a.state, a.wait = "in_cabin", min(900, 200 + 110 * a.fit["items"]) * (strat.ATTENDANT_CABIN_FACTOR if attendant else 1)
                elif st in ("to_till", "till_q"):
                    if self._move(a) or st == "till_q":
                        if a.q_join is None:
                            a.q_join = t
                        a.state = "till_q"
                        k = a.till["queue"].index(a)
                        a.path = [(a.till["x"], self.pay_z - .7 * k)]
                        self._move(a)
                elif st == "exit":
                    if self._move(a):
                        done.append(a)
            for a in done:
                active.remove(a)
                self._finish(a, d, t)

            if self.cfg.record_tracks and t % SAMPLE_EVERY == 0:
                self._sample(d, t, active, track_owner)
            if t % 900 == 0 and t < CLOSE_S + 3600:
                self.hourly.append({"date": d.isoformat(), "t": t, "in_store": len(active), "fit_queue": len(fit_q),
                                    "till_queue": sum(len(x["queue"]) for x in self.tills),
                                    "cabins_busy": sum(c["occ"] is not None for c in self.cabins)})
            t += 1

    def _sample(self, d, t, active, track_owner):
        vis = [a for a in active if a.state != "in_cabin"]
        # occasional identity switches between tracks that pass very close (a real tracker failure mode);
        # neighbours are found through a coarse spatial hash so busy days stay fast
        cells = {}
        for a in vis:
            cells.setdefault((int(a.x // .5), int(a.z // .5)), []).append(a)
        for a in vis:
            cx, cz = int(a.x // .5), int(a.z // .5)
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for b in cells.get((cx + dx, cz + dz), ()):
                        if a.vid < b.vid and abs(a.x - b.x) < .35 and abs(a.z - b.z) < .35 and self.rng.random() < ID_SWITCH_P:
                            a.track, b.track = b.track, a.track
                            track_owner[a.track], track_owner[b.track] = a, b
                            self.events.append({"date": d.isoformat(), "t": t, "type": "id_switch", "tracks": [a.track, b.track]})
        ds = d.isoformat()
        for a in vis:
            x, z = a.x + self.rng.gauss(0, TRACK_NOISE), a.z + self.rng.gauss(0, TRACK_NOISE)
            self.tracks.append((ds, t, a.track, a.vid, round(x, 2), round(z, 2), covered(x, z)))

    def _close_receipt(self, a, t, track_owner):
        till = a.till
        till["queue"].remove(a)
        till["serving"] = None
        no = self.next_receipt
        self.next_receipt += 1
        total = round(sum(it["eur"] for it in a.bought), 2)
        a.receipt.update(no=no, t=t, till=till["n"], total=total, items=len(a.bought), track=a.track)
        self._leave(a, "purchase")

    def _finish(self, a, d, t):
        a.exit_t = t
        ds = d.isoformat()
        fit = a.fit or {}
        self.visits.append({
            "date": ds, "visit_id": a.vid, "persona": a.persona, "kind": a.kind, "enter_t": a.enter_t, "exit_t": t,
            "minutes": round((t - a.enter_t) / 60, 1), "zones": len(a.zone_visits), "outcome": a.outcome,
            "intent_items": len(a.intent), "intent_eur": round(sum(i["eur"] for i in a.intent), 2),
            "bought_items": len(a.bought), "bought_eur": round(sum(i["eur"] for i in a.bought), 2),
            "margin_eur": round(sum(i["eur"] - (1 - strat.GROSS_MARGIN) * i.get("full", i["eur"]) for i in a.bought), 2),
            "lost_eur": round(sum(i["eur"] for i in a.lost), 2),
            "lost_fit_eur": round(sum(i["eur"] for i in a.lost if i["reason"] == "fitting-room queue"), 2),
            "lost_till_eur": round(sum(i["eur"] for i in a.lost if i["reason"] == "till queue"), 2),
            "used_fitting": bool(fit.get("end")), "fit_abandoned": bool(fit.get("abandoned")),
            "fit_wait_s": (fit["start"] - fit["join"]) if fit.get("start") and fit.get("join") else (t - fit["join"] if fit.get("abandoned") and fit.get("join") else None),
            "till_wait_s": (a.receipt["start"] - a.q_join) if a.receipt and a.q_join else None,
            "receipt_no": a.receipt["no"] if a.receipt and "no" in a.receipt else None,
            "track_at_till": a.receipt["track"] if a.receipt and "track" in a.receipt else None,
        })
        for zv in a.zone_visits:
            bought_here = sum(1 for i in a.bought if i["zone"] == zv["zone"])
            self.zone_rows.append({"date": ds, "visit_id": a.vid, "persona": a.persona, "zone": zv["zone"], "t": zv["t"],
                                   "dwell_s": zv["dwell"], "picked": zv["picked"], "bought": bought_here,
                                   "eur": round(sum(i["eur"] for i in a.bought if i["zone"] == zv["zone"]), 2)})
        if a.receipt and "no" in a.receipt:
            for i in a.bought:
                self.receipts.append({"date": ds, "receipt_no": a.receipt["no"], "till": a.receipt["till"], "t": a.receipt["t"],
                                      "visit_id": a.vid, "persona": a.persona, "article_id": i["article_id"], "name": i["name"],
                                      "ptype": i["ptype"], "zone": i["zone"], "eur": i["eur"], "impulse": bool(i.get("impulse")),
                                      "passby": bool(i.get("passby")), "adjacent": bool(i.get("adjacent"))})

    def simulate(self, on_progress=None, quiet: bool = False):
        all_baskets = self.demand.baskets(self.cfg.start, self.cfg.days)
        for k in range(self.cfg.days):
            d = self.cfg.start + timedelta(days=k)
            if all_baskets.get(d):
                td = time.time()
                self.run_day(d, all_baskets[d])
                if not quiet:
                    print(f"  {d}  visits {sum(v['date'] == d.isoformat() for v in self.visits):4d}  {time.time() - td:4.1f}s")
            if on_progress:
                on_progress(k + 1, self.cfg.days)

    def run(self, on_progress=None) -> str:
        t0 = time.time()
        self.simulate(on_progress)
        from fitpath.sim.report import write_run
        run_id = self.cfg.run_id or f"{self.cfg.start}_{self.cfg.days}d_{self.cfg.layout['name'].lower().replace(' ', '-')}_s{self.cfg.seed}"
        write_run(self, run_id)
        print(f"run {run_id} in {time.time() - t0:.0f}s")
        return run_id


def load_personas() -> dict:
    return json.loads((OUT / "personas.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-11-18")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--size", choices=list(SIZES), default="medium")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    cfg = SimConfig(start=date.fromisoformat(args.start), days=args.days, store_size=args.size, seed=args.seed)
    Simulation(cfg, load_personas()).run()
