"""Single-process server for the public demo: the API under /api and the built dashboard at /.

Run: uvicorn fitpath.serve:app --port 7860   (from backend/, after `npm run build` in frontend/)

Requests that spend LLM tokens or CPU (agent messages, simulations, experiments, searches) are
rate-limited per visitor, so a public link cannot drain the AMD key or saturate the machine.
"""
import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from fitpath.api import app

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
LIMIT = int(os.getenv("DEMO_RATE_LIMIT", "20"))          # costly requests per visitor per window
WINDOW = int(os.getenv("DEMO_RATE_WINDOW", "3600"))      # seconds
COSTLY = re.compile(r"^/api/(agent/sessions/[^/]+/(messages|approve)|sim|experiments(/[^/]+/explain)?|search"
                    r"|advice|strategies/propose|strategy/preview)$")

_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def _visitor(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.method == "POST" and COSTLY.match(request.url.path):
        now, who = time.time(), _visitor(request)
        with _hits_lock:
            q = _hits[who]
            while q and q[0] < now - WINDOW:
                q.popleft()
            if len(q) >= LIMIT:
                wait = int(q[0] + WINDOW - now) // 60 + 1
                return JSONResponse({"detail": f"Demo limit reached: try again in about {wait} minutes."}, status_code=429)
            q.append(now)
    return await call_next(request)


if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="dashboard")
