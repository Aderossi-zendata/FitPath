"""FitPath HTTP API for the dashboard and the 3D twin.

Run: uvicorn fitpath.api:app --reload --port 8000   (from backend/)
"""
import json
import threading
import time
import uuid
from datetime import date, timedelta

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from fitpath.analytics import real as real_analytics
from fitpath.config import OUT, PARQUET
from fitpath.llm import provider
from fitpath.llm.advisor import advise
from fitpath.sim.demand import Demand
from fitpath.sim.engine import SimConfig, Simulation, load_personas
from fitpath.sim.layout import BASE_LAYOUT, CAMERAS, STORE, variant
from fitpath.sim.sizing import SIZES, SOURCES
from fitpath.sim import behavior as behavior_mod
from fitpath.sim import layout_opt
from fitpath.sim import store_plan

app = FastAPI(title="FitPath API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SIM_DIR = OUT / "sim"
_jobs: dict[str, dict] = {}
_demand: Demand | None = None
_lock = threading.Lock()


def _json(path):
    if not path.exists():
        raise HTTPException(404, f"{path.name} not found: run the pipeline first")
    return json.loads(path.read_text(encoding="utf-8"))


def _get_demand() -> Demand:
    global _demand
    with _lock:
        if _demand is None:
            _demand = Demand()
    return _demand


@app.get("/api/status")
def status():
    have = {n: (PARQUET / f"{n}.parquet").exists() for n in ("transactions", "articles", "customers", "customer_persona")}
    d = _get_demand() if have["transactions"] else None
    return {"data": have, "llm": provider.describe(),
            "date_range": [d.first_day.isoformat(), d.last_day.isoformat()] if d else None,
            "analytics": (OUT / "analytics_real_store.json").exists(), "personas": (OUT / "personas.json").exists()}


@app.get("/api/real")
def real(scope: str = "store"):
    path = OUT / f"analytics_real_{scope}.json"
    if not path.exists():
        res = real_analytics.run_all(scope)
        path.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return _json(path)


@app.get("/api/personas")
def personas():
    return _json(OUT / "personas.json")


@app.get("/api/layout")
def layout():
    return {"layout": BASE_LAYOUT, "store": STORE, "cameras": [{"x": x, "z": z, "r": r, "name": n} for x, z, r, n in CAMERAS],
            "space_fit": store_plan.space_fit(BASE_LAYOUT["assign"]),
            "rules": {"new_in": list(store_plan.FRONT_BLOCKS), "ladies": list(store_plan.FITTING_BLOCKS), "space": [store_plan.SPACE_MIN, store_plan.SPACE_MAX]}}


@app.get("/api/behavior")
def behavior():
    """Assumptions of the in-store behaviour model, with ranges and sources."""
    return behavior_mod.describe()


@app.get("/api/layouts")
def layouts():
    """Results of the department layout optimizer (newest first)."""
    return [{k: r[k] for k in ("id", "created", "seconds", "settings", "base", "verified", "tabu", "ranker")} for r in layout_opt.list_layouts()[:10]]


@app.get("/api/sizes")
def sizes():
    return {"sizes": SIZES, "sources": SOURCES}


@app.get("/api/volume")
def volume(start: date, days: int = 7, size: str = "medium"):
    """Expected visitors per day for a store size: the size's average-day range x the real H&M daily index."""
    if size not in SIZES:
        raise HTTPException(400, "unknown store size")
    d, s = _get_demand(), SIZES[size]
    out = []
    for k in range(min(days, 90)):
        day = start + timedelta(days=k)
        idx = d.volume_index(day)
        out.append({"date": day.isoformat(), "index": round(idx, 3),
                    "visitors": [round(s["visitors"][0] * idx), round(s["visitors"][1] * idx)],
                    "buyers": [round(s["visitors"][0] * idx * s["conversion"][0]), round(s["visitors"][1] * idx * s["conversion"][1])]})
    return out


class SimRequest(BaseModel):
    start: date = date(2019, 11, 18)
    days: int = Field(7, ge=1, le=90)
    store_size: str = Field("medium", pattern="^(low|medium|high)$")
    tills_open: int = Field(3, ge=1, le=5)
    seed: int = 42
    label: str | None = None


def _run_job(job_id: str, req: SimRequest):
    job = _jobs[job_id]
    try:
        lay = variant(name=req.label or "Scenario", tills_open=req.tills_open)
        cfg = SimConfig(start=req.start, days=req.days, store_size=req.store_size,
                        layout=lay, seed=req.seed, run_id=job_id)
        sim = Simulation(cfg, load_personas(), _get_demand())
        sim.run(on_progress=lambda k, n: job.update(done_days=k, days=n, done=k, total=n))
        (SIM_DIR / job_id / "request.json").write_text(req.model_dump_json(), encoding="utf-8")
        job.update(state="done", finished=time.time())
    except Exception as e:  # report the failure to the dashboard instead of losing it in a thread
        job.update(state="error", error=f"{type(e).__name__}: {e}")


@app.post("/api/sim")
def start_sim(req: SimRequest):
    job_id = f"run-{date.today():%Y%m%d}-{uuid.uuid4().hex[:6]}"
    _jobs[job_id] = {"id": job_id, "state": "running", "done_days": 0, "days": req.days, "done": 0, "total": req.days, "started": time.time(), "request": req.model_dump(mode="json")}
    threading.Thread(target=_run_job, args=(job_id, req), daemon=True).start()
    return _jobs[job_id]


@app.get("/api/sim/jobs/{job_id}")
def job(job_id: str):
    if job_id not in _jobs:
        if (SIM_DIR / job_id / "summary.json").exists():
            return {"id": job_id, "state": "done"}
        raise HTTPException(404, "unknown job")
    return _jobs[job_id]


@app.get("/api/sim/runs")
def runs():
    out = []
    for d in sorted(SIM_DIR.glob("*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        s = json.loads(d.read_text(encoding="utf-8"))
        req = d.parent / "request.json"
        out.append({"run_id": s["run_id"], "start": s["config"]["start"], "days": s["config"]["days"], "totals": s["totals"],
                    "label": json.loads(req.read_text()).get("label") if req.exists() else None, "layout": s["config"]["layout"].get("name")})
    return out


@app.get("/api/sim/runs/{run_id}")
def run_summary(run_id: str):
    return _json(SIM_DIR / run_id / "summary.json")


@app.get("/api/sim/runs/{run_id}/replay")
def replay(run_id: str, day: str, start: int = 0, end: int = 24 * 3600, every: int = 4):
    """Tracks of one simulated day, for the 3D twin. Positions every `every` seconds."""
    base = SIM_DIR / run_id
    tr = pd.read_parquet(base / "tracks.parquet", filters=[("date", "==", day)])
    tr = tr[(tr.t >= start) & (tr.t <= end) & (tr.t % every == 0)]
    v = pd.read_parquet(base / "visits.parquet")
    v = v[v.date == day][["visit_id", "persona", "kind", "enter_t", "exit_t", "receipt_no", "bought_eur", "used_fitting", "outcome"]]
    return {"day": day, "tracks": tr[["t", "track", "visit_id", "x", "z"]].values.tolist(),
            "visits": json.loads(v.to_json(orient="records"))}


class AdviceRequest(BaseModel):
    run_id: str | None = None


@app.post("/api/advice")
def advice(req: AdviceRequest):
    real_data = real()
    sim = run_summary(req.run_id) if req.run_id else None
    today = date.fromisoformat(sim["daily"][-1]["date"]) if sim else date.today()
    cache = SIM_DIR / req.run_id / "advice.json" if req.run_id else None
    res = advise(real_data, sim, today)
    if cache:
        cache.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


@app.get("/api/advice/{run_id}")
def cached_advice(run_id: str):
    return _json(SIM_DIR / run_id / "advice.json")


# ---------- strategies: forecast, experiments, search ----------
from fitpath.forecast import backtest as forecast_backtest  # noqa: E402
from fitpath.llm.advisor import explain_experiment, propose_strategies  # noqa: E402
from fitpath.sim import experiment as exp_mod, search as search_mod, strategy as strat_mod  # noqa: E402

PERIODS = [
    {"key": "next-weekend", "label": "First weekend after the data (25-27 Sep 2020)", "start": "2020-09-25", "days": 3, "mode": "future", "cutoff": "2020-09-23"},
    {"key": "next-week", "label": "First week after the data (28 Sep - 4 Oct 2020)", "start": "2020-09-28", "days": 7, "mode": "future", "cutoff": "2020-09-23"},
    {"key": "bf-2020", "label": "Black Friday 2020, forecast (27-29 Nov 2020)", "start": "2020-11-27", "days": 3, "mode": "future", "cutoff": "2020-09-23"},
    {"key": "bf-2019", "label": "History: Black Friday 2019 (29 Nov - 1 Dec 2019)", "start": "2019-11-29", "days": 3, "mode": "history", "cutoff": None},
]
LEGACY_PERIODS = {"weekend-futuro": "next-weekend", "settimana-futura": "next-week"}   # keys sent by older clients


def _period(key: str) -> dict:
    key = LEGACY_PERIODS.get(key, key)
    p = next((p for p in PERIODS if p["key"] == key), None)
    if not p:
        raise HTTPException(400, "unknown period")
    return p


def _job(kind: str, fn, *args) -> dict:
    jid = f"{kind}-{uuid.uuid4().hex[:6]}"
    job = {"id": jid, "kind": kind, "state": "running", "phase": "starting", "done": 0, "total": 1, "started": time.time()}
    _jobs[jid] = job

    def progress(*a):
        if len(a) == 3:
            job.update(phase=a[0], done=a[1], total=a[2])
        else:
            job.update(done=a[0], total=a[1])

    def run():
        try:
            res = fn(*args, progress)
            job.update(state="done", result_id=res["id"], finished=time.time())
        except Exception as e:  # surface the failure to the dashboard
            job.update(state="error", error=f"{type(e).__name__}: {e}")
    threading.Thread(target=run, daemon=True).start()
    return job


@app.get("/api/periods")
def periods():
    return {"periods": PERIODS, "sizes": SIZES}


@app.get("/api/forecast/backtest")
def backtest(cutoff: date = date(2020, 6, 29), days: int = 56):
    path = OUT / f"backtest_{cutoff}_{days}.json"
    if not path.exists():
        path.write_text(json.dumps(forecast_backtest(cutoff, days)), encoding="utf-8")
    return _json(path)


class ProposeRequest(BaseModel):
    period: str = "next-weekend"
    run_id: str | None = None


@app.post("/api/strategies/propose")
def propose(req: ProposeRequest):
    p = _period(req.period)
    sim = run_summary(req.run_id) if req.run_id else None
    diag = diagnosis(req.run_id) if req.run_id else None
    res = propose_strategies(real(), sim, date.fromisoformat(p["start"]), p, diag)
    (OUT / "strategies_last.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


@app.get("/api/strategies/last")
def last_strategies():
    return _json(OUT / "strategies_last.json")


class ExperimentRequest(BaseModel):
    strategy: dict
    period: str = "next-weekend"
    size: str = Field("medium", pattern="^(low|medium|high)$")
    reps: int = Field(4, ge=2, le=10)
    label: str | None = None


@app.post("/api/experiments")
def start_experiment(req: ExperimentRequest):
    p = _period(req.period)
    cutoff = date.fromisoformat(p["cutoff"]) if p["cutoff"] else None

    def fn(progress):
        return exp_mod.run_experiment(req.strategy, date.fromisoformat(p["start"]), p["days"], req.size, req.reps, p["mode"], cutoff,
                                      on_progress=progress, label=req.label)
    return _job("exp", fn)


@app.get("/api/experiments")
def experiments():
    return [{k: e[k] for k in ("id", "label", "levers", "verdict", "settings", "created")} | {"profit": e["summary"]["profit"]} for e in exp_mod.list_experiments()]


@app.get("/api/experiments/{exp_id}")
def experiment(exp_id: str):
    return _json(exp_mod.EXP_DIR / f"{exp_id}.json")


@app.post("/api/experiments/{exp_id}/explain")
def explain(exp_id: str):
    e = experiment(exp_id)
    res = explain_experiment(e)
    e["explain"] = res
    (exp_mod.EXP_DIR / f"{exp_id}.json").write_text(json.dumps(e, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


class SearchRequest(BaseModel):
    period: str = "next-weekend"
    size: str = Field("medium", pattern="^(low|medium|high)$")
    include_llm: bool = True
    n_explore: int = Field(16, ge=6, le=40)


@app.post("/api/search")
def start_search(req: SearchRequest):
    p = _period(req.period)
    cutoff = date.fromisoformat(p["cutoff"]) if p["cutoff"] else None
    props = []
    if req.include_llm and (OUT / "strategies_last.json").exists():
        last = json.loads((OUT / "strategies_last.json").read_text(encoding="utf-8"))
        props = [{k: v for k, v in s.items() if k not in ("rationale", "source", "expected", "levers")} for s in last.get("strategies", [])]

    def fn(progress):
        return search_mod.search(date.fromisoformat(p["start"]), p["days"], req.size, p["mode"], cutoff, n_explore=req.n_explore,
                                 proposals=props, on_progress=progress)
    return _job("search", fn)


@app.get("/api/searches")
def searches():
    return [{k: s[k] for k in ("id", "created", "settings", "seconds")} for s in search_mod.list_searches()]


@app.get("/api/searches/{search_id}")
def search_result(search_id: str):
    return _json(search_mod.SEARCH_DIR / f"{search_id}.json")


@app.get("/api/levers")
def levers():
    return {"blocks": {k: {"name": b["name"], "cls": b["cls"]} for k, b in store_plan.BLOCKS.items()}, "default_assign": store_plan.DEFAULT_ASSIGN,
            "discount_zones": strat_mod.DISCOUNT_ZONES, "days": strat_mod.DAYS,
            "assumptions": {"attendant_cabin_factor": strat_mod.ATTENDANT_CABIN_FACTOR, "attendant_patience_factor": strat_mod.ATTENDANT_PATIENCE_FACTOR,
                            "cross_merch_p": strat_mod.CROSS_MERCH_P, "staff_cost_eur_h": strat_mod.STAFF_COST_EUR_H, "gross_margin": strat_mod.GROSS_MARGIN}}


# ---------- store diagnosis and plan ----------
def _fmt_t(t: int) -> str:
    return f"{t // 3600:02d}:{(t % 3600) // 60:02d}"


@app.get("/api/sim/runs/{run_id}/diagnosis")
def diagnosis(run_id: str):
    """Where the simulated store loses sales, in euro, with the moments to look at in the 3D view."""
    base = SIM_DIR / run_id
    s = run_summary(run_id)
    v = pd.read_parquet(base / "visits.parquet")
    lost_fit, lost_till = float(v.lost_fit_eur.sum()), float(v.lost_till_eur.sum())
    lost_other = float(v.lost_eur.sum()) - lost_fit - lost_till
    browsers = v[v.kind == "browser"]
    findings = []
    hourly = pd.read_parquet(base / "hourly.parquet") if (base / "hourly.parquet").exists() else None

    def peak(col):
        if hourly is None or not len(hourly):
            return None
        r = hourly.sort_values(col, ascending=False).iloc[0]
        return {"day": r.date, "t": int(r.t), "value": float(r[col]), "label": f"{pd.Timestamp(r.date).strftime('%d/%m')} at {_fmt_t(int(r.t))}"}

    pt, pf = peak("till_queue"), peak("fit_queue")
    if lost_till > 0 or (pt and pt["value"] >= 4):
        findings.append({"key": "tills", "title": "Till queues", "loss_eur": round(lost_till), "area": "Staff",
                         "text": f"{int((v.outcome == 'left the till queue').sum()):,} customers left their items in the queue; average wait {round(s['tills']['avg_wait_s'] or 0)} s, 90th percentile {round(s['tills']['p90_wait_s'] or 0)} s.",
                         "focus": pt, "focus_text": f"Longest queue: {int(pt['value'])} people, {pt['label']}" if pt else None})
    if lost_fit > 0 or (pf and pf["value"] >= 3):
        f = s["fitting"]
        findings.append({"key": "fitting", "title": "Fitting-room waits", "loss_eur": round(lost_fit), "area": "Fitting rooms",
                         "text": f"{round((f['abandon_share'] or 0) * 100)}% of fitting-room users give up; those who wait more than {f['long_wait_threshold_s'] // 60} min buy {round((f['conv_long_wait'] or 0) * 100)}% of the time, against {round((f['conv_short_wait'] or 0) * 100)}% after a short wait.",
                         "focus": pf, "focus_text": f"Longest queue: {int(pf['value'])} people, {pf['label']}" if pf else None})
    low = min(s["zones"], key=lambda z: z["conversion"]) if s["zones"] else None
    if low:
        findings.append({"key": "zone", "title": f"{low['name']}: people stop but don't buy", "loss_eur": None, "area": "Layout & assortment",
                         "text": f"{round(low['reach'] * 100)}% of visitors stop here, for {round(low['dwell_s'] / 60, 1)} min on average, but only {round(low['conversion'] * 100)}% of those who stop buy.",
                         "focus": None, "zone": low["zone"]})
    findings.append({"key": "undecided", "title": "Items picked up and left", "loss_eur": round(lost_other), "area": "Product",
                     "text": "Items carried around or tried on and then not bought (size, price, change of mind): this is the largest loss, and it depends on the product, not on queues.", "focus": None})
    findings.append({"key": "browsers", "title": "Visitors leaving without buying", "loss_eur": None, "area": "Conversion",
                     "text": f"{len(browsers):,} visits with no purchase intent; {round(browsers.receipt_no.notna().mean() * 100) if len(browsers) else 0}% of them convert through impulse purchases.", "focus": None})
    pk = peak("in_store")
    if pk:
        r = hourly[(hourly.date == pk["day"]) & (hourly.t == pk["t"])].iloc[0]
        findings.append({"key": "peak", "title": "Busiest moment", "loss_eur": None, "area": "Footfall",
                         "text": f"{int(pk['value'])} people in the store on {pk['label']}; at that moment {int(r.till_queue)} were queuing at the tills and {int(r.fit_queue)} at the fitting rooms.",
                         "focus": pk, "focus_text": pk["label"]})
    findings.sort(key=lambda f: -(f["loss_eur"] or 0))
    return {"run_id": run_id, "totals": s["totals"], "lost": {"fitting": round(lost_fit), "tills": round(lost_till), "other": round(lost_other)},
            "findings": findings, "store_size": s.get("store_size"), "config": s["config"], "days": [d["date"] for d in s["daily"]]}


PLAN = OUT / "plan.json"


def _plan() -> list:
    return json.loads(PLAN.read_text(encoding="utf-8")) if PLAN.exists() else []


class PlanItem(BaseModel):
    experiment_id: str
    note: str | None = None


@app.get("/api/plan")
def plan():
    return _plan()


@app.post("/api/plan")
def add_plan(item: PlanItem):
    e = experiment(item.experiment_id)
    items = [p for p in _plan() if p["experiment_id"] != item.experiment_id]
    items.append({"experiment_id": e["id"], "label": e["label"], "levers": e["levers"], "verdict": e["verdict"], "settings": e["settings"],
                  "profit": e["summary"]["profit"], "revenue": e["summary"]["revenue"], "conversion": e["summary"]["conversion"],
                  "note": item.note, "added": time.time()})
    PLAN.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return items


@app.delete("/api/plan/{experiment_id}")
def remove_plan(experiment_id: str):
    items = [p for p in _plan() if p["experiment_id"] != experiment_id]
    PLAN.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return items


# ---------- agent ----------
from fitpath.agent import core as agent_core, store as agent_store  # noqa: E402
from fitpath.agent.tools import TOOLS as AGENT_TOOLS  # noqa: E402

agent_core.recover()


@app.get("/api/agent/sessions")
def agent_sessions():
    return agent_store.list_sessions()


@app.post("/api/agent/sessions")
def agent_new_session():
    return agent_store.create_session()


@app.get("/api/agent/sessions/{sid}")
def agent_session(sid: str):
    s = agent_store.load_session(sid)
    if not s:
        raise HTTPException(404, "conversation not found")
    return s


@app.delete("/api/agent/sessions/{sid}")
def agent_delete_session(sid: str):
    agent_store.delete_session(sid)
    return {"ok": True}


class AgentMessage(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@app.post("/api/agent/sessions/{sid}/messages")
def agent_send(sid: str, m: AgentMessage):
    try:
        return agent_core.send(sid, m.text)
    except KeyError:
        raise HTTPException(404, "conversation not found")
    except RuntimeError as e:
        raise HTTPException(409, str(e))


class Approval(BaseModel):
    call_id: str
    approve: bool


@app.post("/api/agent/sessions/{sid}/approve")
def agent_approve(sid: str, a: Approval):
    return {"ok": agent_core.approve(a.call_id, a.approve)}


@app.post("/api/agent/sessions/{sid}/stop")
def agent_stop(sid: str):
    agent_core.stop(sid)
    return {"ok": True}


@app.get("/api/agent/suggestions")
def agent_suggestions():
    return agent_store.suggestions()


@app.delete("/api/agent/suggestions/{sug_id}")
def agent_remove_suggestion(sug_id: str):
    agent_store.remove_suggestion(sug_id)
    return agent_store.suggestions()


@app.get("/api/agent/notes")
def agent_notes():
    return {"notes": agent_store.notes(), "constraints": agent_store.store_constraints()}


@app.delete("/api/agent/notes/{note_id}")
def agent_remove_note(note_id: str):
    agent_store.remove_note(note_id)
    return agent_notes()


@app.get("/api/agent/info")
def agent_info():
    return {"models": agent_core.MODELS, "tools": [{"name": n, "title": t["title"]} for n, t in AGENT_TOOLS.items()]}


class PreviewRequest(BaseModel):
    strategy: dict


@app.post("/api/strategy/preview")
def strategy_preview(req: PreviewRequest):
    st = strat_mod.normalize(req.strategy)
    return {"layout": strat_mod.apply_layout(BASE_LAYOUT, st, st["name"]), "strategy": st, "levers": strat_mod.describe(st)}
