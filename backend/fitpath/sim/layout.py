"""Physical layout of the FitPath store (metres, x right, z towards the entrance).

The floor plan itself (an H&M-style 2,000 m² store) is data in sim/store_plan.py; a layout is the plan
with a department -> block assignment plus open tills/cabins. This module builds the navigation grid
the simulated shoppers walk on, and the camera coverage used by the tracking layer.
"""
import copy
import heapq
import math

from fitpath.sim import store_plan

STORE = store_plan.STORE
BASE_LAYOUT = store_plan.build_layout()
PAY_Z = store_plan.PAY_Z
CAMERAS = store_plan.cameras()


def variant(**changes) -> dict:
    """Copy of the base layout with some fields changed, e.g. variant(tills_open=4) or variant(assign={...})."""
    if "assign" in changes:
        lay = store_plan.build_layout(changes.pop("assign"))
    else:
        lay = copy.deepcopy(BASE_LAYOUT)
    for k, v in changes.items():
        lay[k] = v
    return lay


def covered(x: float, z: float) -> bool:
    return any(math.hypot(x - cx, z - cz) < r * .95 for cx, cz, r, _ in CAMERAS)


def block_of(layout: dict, x: float, z: float, pad: float = 0.0) -> str | None:
    """Department whose block contains the point (pad widens the blocks, e.g. to count passers-by in the aisle)."""
    for zone, b in layout["assign"].items():
        r = layout["blocks"][b]
        if r["x0"] - pad <= x <= r["x1"] + pad and r["z0"] - pad <= z <= r["z1"] + pad:
            return zone
    return None


class NavGrid:
    """Occupancy grid with cached A* paths and line-of-sight smoothing, built from the layout's geometry."""
    CELL = .75
    STAND_SPOTS = 28     # browsing spots kept per department (keeps the path cache small)

    def __init__(self, layout: dict):
        self.layout = layout
        st = layout["store"]
        self.x0, self.z0 = st["x0"], st["z0"]
        self.gx, self.gz = int(st["w"] / self.CELL), int(st["d"] / self.CELL)
        self.blocked = [[False] * self.gx for _ in range(self.gz)]
        door0, door1 = st["door"]
        x1, z1 = st["x0"] + st["w"], st["z0"] + st["d"]
        fit, tills = layout["fitting"], layout["tills"]
        obstacles = [f for fl in layout["fixtures"].values() for f in fl] + layout.get("decor", [])
        for k in range(self.gz):
            for i in range(self.gx):
                x, z = self.center(i, k)
                b = x < st["x0"] + .45 or x > x1 - .45 or z < st["z0"] + .45
                b |= z > z1 - .45 and not (door0 < x < door1)
                b |= fit["x0"] <= x <= fit["x1"] and z < fit["door_z"] + .3                          # fitting-room block
                b |= tills[0] - 1.3 <= x <= tills[-1] + 1.3 and z > layout["till_z"] - .7           # till counter
                if not b:
                    for f in obstacles:
                        if f["t"] == "rack":
                            b = math.hypot(x - f["x"], z - f["z"]) < 1.0
                        else:
                            b = abs(x - f["x"]) < f["w"] / 2 + .3 and abs(z - f["z"]) < f["d"] / 2 + .3
                        if b:
                            break
                for mx, mz in layout["mannequins"] + layout.get("columns", []):
                    b |= math.hypot(x - mx, z - mz) < .55
                self.blocked[k][i] = b
        self.stand = {}
        for zone, fl in layout["fixtures"].items():
            pts = []
            for k in range(self.gz):
                for i in range(self.gx):
                    if self.blocked[k][i]:
                        continue
                    x, z = self.center(i, k)
                    near = any(math.hypot(x - f["x"], z - f["z"]) < 1.8 if f["t"] == "rack"
                               else abs(x - f["x"]) < f["w"] / 2 + 1.0 and abs(z - f["z"]) < f["d"] / 2 + 1.0 for f in fl)
                    if near:
                        pts.append((x, z))
            step = max(1, len(pts) // self.STAND_SPOTS)
            self.stand[zone] = pts[::step][:self.STAND_SPOTS] or pts
        self._cache = {}

    def center(self, i, k):
        return self.x0 + (i + .5) * self.CELL, self.z0 + (k + .5) * self.CELL

    def cell(self, x, z):
        i = min(self.gx - 1, max(0, int((x - self.x0) / self.CELL)))
        k = min(self.gz - 1, max(0, int((z - self.z0) / self.CELL)))
        return i, k

    def free(self, x, z):
        i, k = int((x - self.x0) / self.CELL), int((z - self.z0) / self.CELL)
        return 0 <= i < self.gx and 0 <= k < self.gz and not self.blocked[k][i]

    def _nearest_free(self, i, k):
        if not self.blocked[k][i]:
            return i, k
        for r in range(1, 16):
            for dk in range(-r, r + 1):
                for di in range(-r, r + 1):
                    a, b = i + di, k + dk
                    if 0 <= a < self.gx and 0 <= b < self.gz and not self.blocked[b][a]:
                        return a, b
        return i, k

    def _los(self, p, q):
        d = math.hypot(q[0] - p[0], q[1] - p[1])
        n = max(1, int(d / .3))
        return all(self.free(p[0] + (q[0] - p[0]) * s / n, p[1] + (q[1] - p[1]) * s / n) for s in range(1, n))

    def path(self, a: tuple, b: tuple) -> list:
        s, t = self._nearest_free(*self.cell(*a)), self._nearest_free(*self.cell(*b))
        key = (s, t)
        if key not in self._cache:
            self._cache[key] = self._astar(s, t)
        cells = self._cache[key]
        pts = [a] + cells + [b]
        out, i = [pts[0]], 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self._los(pts[i], pts[j]):
                j -= 1
            out.append(pts[j])
            i = j
        return out[1:]

    def _astar(self, s, t):
        h = lambda i, k: max(abs(i - t[0]), abs(k - t[1])) + .414 * min(abs(i - t[0]), abs(k - t[1]))
        g, came, closed = {s: 0.0}, {}, set()
        heap = [(h(*s), s)]
        while heap:
            _, n = heapq.heappop(heap)
            if n in closed:
                continue
            closed.add(n)
            if n == t:
                break
            i, k = n
            for di in (-1, 0, 1):
                for dk in (-1, 0, 1):
                    if not di and not dk:
                        continue
                    a, b = i + di, k + dk
                    if not (0 <= a < self.gx and 0 <= b < self.gz) or self.blocked[b][a] or (a, b) in closed:
                        continue
                    if di and dk and (self.blocked[k][a] or self.blocked[b][i]):
                        continue
                    ng = g[n] + (1.414 if di and dk else 1)
                    if ng < g.get((a, b), 1e9):
                        g[(a, b)] = ng
                        came[(a, b)] = n
                        heapq.heappush(heap, (ng + h(a, b), (a, b)))
        cells, n = [], t
        while n in came:
            cells.append(self.center(*n))
            n = came[n]
        return cells[::-1]
