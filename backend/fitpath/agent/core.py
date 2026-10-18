"""The FitPath agent loop (structured like opencode's harness).

user message -> model (with tools) -> tool calls run one by one, each shown as a part in the chat
(asking approval first when the tool says so) -> results go back to the model -> ... until it answers in text.

It speaks the OpenAI chat-completions protocol with tool calling, so it runs on AMD Token Factory
(models served on AMD GPUs), on any other OpenAI-compatible endpoint, or on Gemini's compatible endpoint.
Earlier turns are replayed to the model as text, with a compact summary of the tools used.
"""
import json
import os
import threading
import time
import traceback

from fitpath import config
from fitpath.agent import store
from fitpath.agent.tools import DATA_END, DOW, TOOLS
from fitpath.llm import provider


def _agent_models() -> list[str]:
    if config.LLM_PROVIDER == "amd":
        return provider.models()
    env = [m.strip() for m in os.getenv("AGENT_MODELS", "").split(",") if m.strip()]
    return env or provider.models()


MODELS = _agent_models()
MAX_STEPS = 14
_cooldown: dict[str, float] = {}          # model -> unavailable until (quota or not found)
_approvals: dict[str, dict] = {}          # call_id -> {"event", "approve"}
_stops: set[str] = set()


def system_prompt() -> str:
    notes = store.notes()
    c = store.store_constraints()
    return f"""You are the strategy assistant of FitPath, the digital twin of a 2,000 m² single-floor fast-fashion store in the style of H&M
(departments Ladies, Divided, Men, Kids, Denim, Basics, Accessories and New in, arranged in 8 blocks; 8 fitting rooms; 6 tills,
3 open by default plus one more with the extra_till lever). In-store shopper behaviour follows the retail literature
(position attraction, purchases along the path, adjacent departments bought together, crowding): read_context summarises it.
Today is {DOW[DATA_END.weekday()]} {DATA_END.isoformat()}: the last day of real sales data (H&M, two years). The past is real,
the future is forecast. "Last week" = the 7 days up to today.

How you work:
- Answer only what the user asks, in English, clearly and concisely. Do not propose strategies unless asked.
- Use the tools to get real numbers: never invent data. Quote numbers and always say whether they come from real sales or from the simulation.
- When you propose concrete strategies, put them in the panel with propose_suggestions (2-5 strategies, different from each other).
- To know whether a strategy works, test it with test_strategy on the right period, then explain the result: verdict, net profit,
  side effects (queues, fitting rooms) and whether the 90% interval is reliable (does not cross zero) or uncertain.
- There are many levers: window display at the entrance (feature), end-of-season sale (clearance), 3-for-2 or second item half price (multibuy),
  bundles of products bought together, department discounts, cross-merchandising (cross_merch), moving departments (layout: swap two departments),
  an extra till and a fitting-room attendant. To rearrange the departments systematically use optimize_layout. Pick the ones that fit the season and the data.
- If the user tells you something they saw in the store, save it with save_note (with fitting rooms out of order or tills available when relevant)
  and take it into account in your advice.
- To show a layout or window change without simulating, use show_in_twin.
- Black Friday 2020 = Friday 27 November 2020. Next month = October 2020.
- Format with short sentences and bullet points; use **bold** for the key figures. Amounts in euros, like €3,677.

Notes the user saved about the store: {json.dumps([n['text'] for n in notes], ensure_ascii=False) if notes else 'none'}.
Current simulation constraints: {c['cabins_open']} fitting rooms, {c['tills_open']} tills open by default."""


class Ctx:
    def __init__(self, session: dict, part: dict):
        self.session, self.part, self.session_id = session, part, session["id"]
        self._last = 0.0

    def progress(self, done, total, phase=""):
        self.part["progress"] = {"done": done, "total": total, "phase": phase}
        if time.time() - self._last > .5 or done == total:
            self._last = time.time()
            store.save_session(self.session)


def _tool_defs() -> list[dict]:
    return [{"type": "function", "function": {"name": n, "description": t["desc"], "parameters": t["params"]}} for n, t in TOOLS.items()]


def _history(session: dict) -> list[dict]:
    """Earlier turns as plain text for the model."""
    out = []
    for m in session["messages"][:-1]:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["text"]})
        else:
            chunks = []
            for p in m["parts"]:
                if p["type"] == "text":
                    chunks.append(p["text"])
                elif p["type"] == "tool" and p["status"] == "done":
                    chunks.append(f"[tool {p['name']}({json.dumps(p['args'], ensure_ascii=False)[:300]}) -> {json.dumps(p.get('result'), ensure_ascii=False, default=str)[:1200]}]")
            if chunks:
                out.append({"role": "assistant", "content": "\n".join(chunks)})
    return out


def _generate(messages: list[dict]):
    cl = provider.client(timeout=180)
    errors = []
    for model in MODELS:
        if _cooldown.get(model, 0) > time.time():
            continue
        for attempt in range(2):
            try:
                resp = cl.chat.completions.create(model=model, messages=messages, tools=_tool_defs(), temperature=.3)
                return resp.choices[0].message, model
            except Exception as e:
                code = provider.status_code(e)
                errors.append(f"{model}: {code or type(e).__name__}")
                if code in (404, 429):
                    _cooldown[model] = time.time() + 3600
                    break
                if attempt == 0 and (code is None or (isinstance(code, int) and code >= 500)):
                    time.sleep(2)
                    continue
                break
    raise RuntimeError("no model available (" + "; ".join(errors) + ")")


def _clean(text: str | None) -> str:
    text = (text or "").strip()
    if "</think>" in text:                 # reasoning models may inline their thinking
        text = text.split("</think>", 1)[1].strip()
    return text


def _run(session_id: str):
    s = store.load_session(session_id)
    msg = {"id": store.new_id("msg"), "role": "assistant", "parts": [], "model": None, "started": time.time()}
    s["messages"].append(msg)
    s["busy"] = True
    store.save_session(s)
    user_text = s["messages"][-2]["text"]
    messages = [{"role": "system", "content": system_prompt()}] + _history({**s, "messages": s["messages"][:-1]}) + [{"role": "user", "content": user_text}]
    try:
        for _ in range(MAX_STEPS):
            if session_id in _stops:
                msg["parts"].append({"type": "text", "text": "_Stopped._"})
                break
            reply, model = _generate(messages)
            msg["model"] = model
            text, calls = _clean(reply.content), list(reply.tool_calls or [])
            if text:
                msg["parts"].append({"type": "text", "text": text})
            store.save_session(s)
            if not calls:
                break
            messages.append({"role": "assistant", "content": reply.content or "",
                             "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}} for c in calls]})
            for call in calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool = TOOLS.get(name)
                part = {"type": "tool", "call_id": store.new_id("call"), "name": name, "title": tool["title"] if tool else name,
                        "args": args, "status": "running", "started": time.time()}
                msg["parts"].append(part)

                def answer(payload):
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(payload, ensure_ascii=False, default=str)})

                if not tool:
                    part.update(status="error", error="unknown tool")
                    answer({"error": "unknown tool"})
                    continue
                if tool["ask"](args):
                    part["status"] = "pending_approval"
                    ev = {"event": threading.Event(), "approve": False}
                    _approvals[part["call_id"]] = ev
                    store.save_session(s)
                    ev["event"].wait(timeout=1800)
                    _approvals.pop(part["call_id"], None)
                    if not ev["approve"] or session_id in _stops:
                        part.update(status="rejected", ended=time.time())
                        answer({"rejected": "The user did not approve this action."})
                        store.save_session(s)
                        continue
                    part["status"] = "running"
                store.save_session(s)
                try:
                    out = tool["fn"](args, Ctx(s, part))
                    part.update(status="done", result=out.get("result"), summary=out.get("summary"), ui=out.get("ui"), ended=time.time())
                    answer({"result": out.get("result")})
                except Exception as e:
                    part.update(status="error", error=f"{type(e).__name__}: {e}", ended=time.time())
                    traceback.print_exc()
                    answer({"error": str(e)})
                part.pop("progress", None)
                store.save_session(s)
    except Exception as e:
        msg["parts"].append({"type": "error", "text": f"Error: {e}"})
        traceback.print_exc()
    finally:
        _stops.discard(session_id)
        msg["ended"] = time.time()
        s["busy"] = False
        store.save_session(s)


def send(session_id: str, text: str) -> dict:
    s = store.load_session(session_id)
    if s is None:
        raise KeyError(session_id)
    if s.get("busy"):
        raise RuntimeError("the agent is still working")
    if not s["messages"]:
        s["title"] = text[:60]
    s["messages"].append({"id": store.new_id("msg"), "role": "user", "text": text, "time": time.time()})
    s["busy"] = True
    store.save_session(s)
    threading.Thread(target=_run, args=(session_id,), daemon=True).start()
    return s


def approve(call_id: str, ok: bool) -> bool:
    ev = _approvals.get(call_id)
    if not ev:
        return False
    ev["approve"] = ok
    ev["event"].set()
    return True


def stop(session_id: str):
    _stops.add(session_id)
    for ev in list(_approvals.values()):
        ev["event"].set()


def recover():
    """Sessions left busy by a server restart are marked idle."""
    for meta in store.list_sessions():
        if meta["busy"]:
            s = store.load_session(meta["id"])
            s["busy"] = False
            for m in s["messages"]:
                for p in m.get("parts", []):
                    if p.get("type") == "tool" and p.get("status") in ("running", "pending_approval"):
                        p["status"] = "error"; p["error"] = "interrupted by a server restart"
            store.save_session(s)
