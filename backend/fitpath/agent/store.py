"""Persistent agent state: sessions (conversations), suggestions shown in the side drawer, store notes.

A session mirrors opencode's model: a list of messages, each made of parts. A part is either text or a
tool call with its status (pending_approval, running, done, error, rejected), arguments, progress and result.
"""
import json
import threading
import time
import uuid

from fitpath.config import OUT

DIR = OUT / "agent"
SESS = DIR / "sessions"
SESS.mkdir(parents=True, exist_ok=True)
SUGG = DIR / "suggestions.json"
NOTES = DIR / "notes.json"
_lock = threading.RLock()


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except json.JSONDecodeError:
        return default


def _write(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------- sessions ----------
def create_session(title: str = "New conversation") -> dict:
    s = {"id": new_id("ses"), "title": title, "created": time.time(), "updated": time.time(), "busy": False, "messages": []}
    save_session(s)
    return s


def save_session(s: dict):
    with _lock:
        s["updated"] = time.time()
        _write(SESS / f"{s['id']}.json", s)


def load_session(sid: str) -> dict | None:
    with _lock:
        return _read(SESS / f"{sid}.json", None)


def list_sessions() -> list[dict]:
    out = []
    for p in SESS.glob("ses-*.json"):
        s = _read(p, None)
        if s:
            out.append({"id": s["id"], "title": s["title"], "updated": s["updated"], "busy": s.get("busy", False), "messages": len(s["messages"])})
    return sorted(out, key=lambda x: -x["updated"])


def delete_session(sid: str):
    (SESS / f"{sid}.json").unlink(missing_ok=True)


# ---------- suggestions (left drawer) ----------
def suggestions() -> list[dict]:
    return _read(SUGG, [])


def add_suggestions(items: list[dict], session_id: str) -> list[dict]:
    with _lock:
        cur = suggestions()
        for it in items:
            it = {**it, "id": new_id("sug"), "session_id": session_id, "created": time.time(), "experiment_id": None, "verdict": None}
            cur.insert(0, it)
        _write(SUGG, cur[:60])
        return cur


def update_suggestion(sid: str, **fields):
    with _lock:
        cur = suggestions()
        for it in cur:
            if it["id"] == sid:
                it.update(fields)
        _write(SUGG, cur)


def link_experiment(strategy_name: str, experiment_id: str, verdict: str, profit: float):
    """Attach a test result to the suggestion with the same strategy name, if any."""
    with _lock:
        cur = suggestions()
        for it in cur:
            if it.get("strategy", {}).get("name") == strategy_name or it.get("title") == strategy_name:
                it.update(experiment_id=experiment_id, verdict=verdict, profit=profit)
        _write(SUGG, cur)


def remove_suggestion(sid: str):
    with _lock:
        _write(SUGG, [s for s in suggestions() if s["id"] != sid])


# ---------- store notes (what the manager noticed in the real store) ----------
def notes() -> list[dict]:
    return _read(NOTES, [])


def add_note(text: str, effect: dict | None) -> dict:
    with _lock:
        n = {"id": new_id("note"), "text": text, "effect": effect or {}, "created": time.time()}
        _write(NOTES, [n, *notes()])
        return n


def remove_note(nid: str):
    with _lock:
        _write(NOTES, [n for n in notes() if n["id"] != nid])


def store_constraints() -> dict:
    """Simulation parameters implied by the notes (e.g. a broken fitting room)."""
    from fitpath.sim.store_plan import CABINS, TILLS
    cabins, tills = len(CABINS), 3
    for n in notes():
        e = n.get("effect") or {}
        cabins -= int(e.get("fitting_rooms_out_of_order", e.get("cabine_fuori_servizio", 0)) or 0)
        tills_av = e.get("tills_available", e.get("casse_disponibili"))
        if tills_av:
            tills = int(tills_av)
    return {"cabins_open": max(1, cabins), "tills_open": max(1, min(len(TILLS) - 1, tills))}
