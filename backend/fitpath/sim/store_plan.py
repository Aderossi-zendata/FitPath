"""Floor plan of the FitPath store: a single-floor H&M-style store of about 2,000 m² (50 x 40 m).

Sizes follow public figures for H&M stores (US stores 20-25k sq ft, new German stores ~2,000 m²) and the
usual fast-fashion structure: entrance with an empty decompression zone and window displays, a loop of
main aisles, departments in blocks, fitting rooms next to womenswear, tills near the exit.

The plan is data. Departments are assigned to blocks (`assign`); the fixtures of each department are
generated from its type and its block, so moving a department is just a different assignment. This is
what the layout optimizer explores (block layout, as in Ozgormus & Smith 2015).

Coordinates: metres, x to the right, z towards the entrance (the door is at z = +20).
"""
import json
import math

from fitpath.catalog import ZONE_NAMES, ZONES

STORE = {"x0": -25.0, "z0": -20.0, "w": 50.0, "d": 40.0, "door": (-4.0, 4.0)}

# department blocks: rectangle + position class (drives the attraction model in sim/behavior.py)
BLOCKS = {
    "C1": {"x0": -6.0, "x1": 6.0, "z0": 3.0, "z1": 11.0, "name": "Centre, in sight from the entrance", "cls": "sight_line"},
    "E": {"x0": -24.0, "x1": -10.0, "z0": 14.0, "z1": 19.3, "name": "Left window", "cls": "window"},
    "R1": {"x0": 9.5, "x1": 24.0, "z0": 3.0, "z1": 11.0, "name": "Front right, near the tills", "cls": "near_tills"},
    "L": {"x0": -24.0, "x1": -9.5, "z0": -13.0, "z1": 11.0, "name": "Left side, near the fitting rooms", "cls": "side_fitting"},
    "C2": {"x0": -6.0, "x1": 6.0, "z0": -13.0, "z1": 0.0, "name": "Centre back", "cls": "center_back"},
    "R2": {"x0": 9.5, "x1": 24.0, "z0": -6.0, "z1": 0.0, "name": "Right", "cls": "side"},
    "R3": {"x0": 9.5, "x1": 24.0, "z0": -13.0, "z1": -8.5, "name": "Right back", "cls": "side_back"},
    "BW": {"x0": -6.0, "x1": 24.0, "z0": -19.5, "z1": -16.5, "name": "Back wall", "cls": "back_wall"},
}
# H&M-like default: Ladies on the big side next to the fitting rooms, New-in in the line of sight from the door,
# Divided at the front, Accessories in the window, Kids at the back wall (a destination department)
DEFAULT_ASSIGN = {"new_in": "C1", "accessories": "E", "divided": "R1", "ladies": "L", "basics": "C2", "men": "R2", "denim": "R3", "kids": "BW"}

# which kind of fixtures each department uses
DEPT_KIND = {"ladies": "racks", "divided": "racks", "men": "racks", "kids": "racks", "new_in": "tables", "denim": "tables",
             "basics": "gondolas", "accessories": "small_tables"}

CABINS = [round(-22.6 + 1.6 * i, 2) for i in range(8)]
FITTING = {"door_z": -16.7, "in_z": -18.6, "wall_z": -18.5, "depth": 3.0, "x0": -24.4, "x1": -9.6,
           "queue": {"x": -8.6, "z": -15.4, "dx": 0.65}}
TILLS = [12.0, 14.0, 16.0, 18.0, 20.0, 22.0]
TILL_Z, PAY_Z = 17.6, 16.4
ENTRANCE = {"x": 0.0, "z_in": 18.0, "z_out": 21.5, "exit_x": 1.5}
DECOMPRESSION = {"x0": -9.0, "x1": 9.0, "z0": 14.0, "z1": 20.0}
COLUMNS = [(-7.75, 12.5), (7.75, 12.5), (-7.75, 1.5), (7.75, 1.5), (-7.75, -14.0), (7.75, -14.0)]
MANNEQUINS = [(-8.0, 19.2), (-6.5, 19.2), (6.5, 19.2), (8.0, 19.2), (-1.2, 15.4), (1.2, 15.4)]
DECOR = [{"t": "podium", "x": 0.0, "z": 15.4, "w": 2.4, "d": 1.2, "label": "Indoor display"}]


def _grid(b: dict, sx: float, sz: float, margin: float):
    """Centres of a regular grid filling a block."""
    w, d = b["x1"] - b["x0"] - 2 * margin, b["z1"] - b["z0"] - 2 * margin
    nx, nz = max(1, int(w // sx) + 1), max(1, int(d // sz) + 1)
    ox = b["x0"] + margin + (w - (nx - 1) * sx) / 2
    oz = b["z0"] + margin + (d - (nz - 1) * sz) / 2
    return [(round(ox + i * sx, 2), round(oz + j * sz, 2)) for i in range(nx) for j in range(nz)]


def _walls(b: dict) -> list[dict]:
    """Wall shelves where the block touches the store's outer walls (never on the entrance side)."""
    out, x0, x1 = [], STORE["x0"] + 1.0, STORE["x0"] + STORE["w"] - 1.0
    if b["x0"] <= x0:
        out.append({"t": "wall", "x": STORE["x0"] + 0.35, "z": round((b["z0"] + b["z1"]) / 2, 2), "w": 0.5, "d": round(b["z1"] - b["z0"] - 0.6, 2)})
    if b["x1"] >= x1:
        out.append({"t": "wall", "x": STORE["x0"] + STORE["w"] - 0.35, "z": round((b["z0"] + b["z1"]) / 2, 2), "w": 0.5, "d": round(b["z1"] - b["z0"] - 0.6, 2)})
    if b["z0"] <= STORE["z0"] + 1.0:
        out.append({"t": "wall", "x": round((b["x0"] + b["x1"]) / 2, 2), "z": STORE["z0"] + 0.35, "w": round(b["x1"] - b["x0"] - 0.6, 2), "d": 0.5})
    return out


def fixtures_for(dept: str, block_id: str) -> list[dict]:
    b, kind = BLOCKS[block_id], DEPT_KIND[dept]
    shallow = b["z1"] - b["z0"] < 4.5
    out = _walls(b)
    if kind == "racks":
        pts = _grid(b, 3.6, 3.6, 1.7) if not shallow else [(x, round(b["z1"] - 1.2, 2)) for x, _ in _grid(b, 3.6, 10, 1.7)]
        out += [{"t": "rack", "x": x, "z": z} for x, z in pts]
    elif kind == "tables":
        out += [{"t": "table", "x": x, "z": z, "w": 3.0, "d": 1.6} for x, z in _grid(b, 4.8, 3.8, 2.0 if not shallow else 1.0)]
    elif kind == "small_tables":
        out += [{"t": "table", "x": x, "z": z, "w": 2.0, "d": 1.2} for x, z in _grid(b, 3.6, 3.2, 1.6 if not shallow else 0.9)]
    else:  # gondolas: long double-sided shelves in rows
        length = min(8.0, b["x1"] - b["x0"] - 3.0)
        rows = _grid({**b, "x0": b["x0"], "x1": b["x1"]}, length + 2.2, 3.0, 1.5 if not shallow else 0.8)
        out += [{"t": "wall", "x": x, "z": z, "w": round(length, 2), "d": 0.9} for x, z in rows]
    return out


def _read_cache(path):
    """Cached JSON, or None if missing or half-written (parallel workers may build it at the same time)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_cache(path, data):
    import os
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def zone_share() -> dict:
    """Share of store-channel revenue per department (cached), used for the space each one needs."""
    from fitpath.config import OUT
    path = OUT / "zone_share.json"
    cached = _read_cache(path)
    if cached is not None:
        return cached
    import duckdb
    from fitpath.catalog import ZONE_SQL
    from fitpath.config import STORE_CHANNEL, pq
    zone = ZONE_SQL.replace("index_group_name", "a.index_group_name").replace("garment_group_name", "a.garment_group_name")
    rows = duckdb.connect().execute(f"""
        SELECT {zone} AS z, sum(t.price) AS rev FROM '{pq("transactions")}' t JOIN '{pq("articles")}' a USING (article_id)
        WHERE t.channel = {STORE_CHANNEL} GROUP BY 1""").fetchall()
    tot = sum(r for _, r in rows)
    share = {z: round(r / tot, 4) for z, r in rows if z}
    share["new_in"] = 0.08   # the new-in area takes space from all lines; H&M stores give it the front
    _write_cache(path, share)
    return share


def block_area(bid: str) -> float:
    b = BLOCKS[bid]
    return (b["x1"] - b["x0"]) * (b["z1"] - b["z0"])


def space_fit(assign: dict) -> dict:
    """Area of each department's block vs the area its sales would justify (1.0 = right size)."""
    share, total = zone_share(), sum(block_area(b) for b in BLOCKS)
    s = sum(share.get(z, 0) for z in ZONES)
    return {z: round(block_area(assign[z]) / (share.get(z, .05) / s * total), 2) for z in ZONES}


def zone_lift() -> dict:
    """How much more often two departments end up in the same store basket than by chance (cached)."""
    from fitpath.config import OUT
    path = OUT / "zone_lift.json"
    cached = _read_cache(path)
    if cached is not None:
        return cached
    import duckdb
    from fitpath.catalog import ZONE_SQL
    from fitpath.config import STORE_CHANNEL, pq
    zone = ZONE_SQL.replace("index_group_name", "a.index_group_name").replace("garment_group_name", "a.garment_group_name")
    con = duckdb.connect()
    con.execute(f"""CREATE TEMP TABLE bz AS SELECT DISTINCT t.cid, t.t_dat, {zone} AS z
        FROM '{pq("transactions")}' t JOIN '{pq("articles")}' a USING (article_id) WHERE t.channel = {STORE_CHANNEL}""")
    n = con.execute("SELECT count(*) FROM (SELECT DISTINCT cid, t_dat FROM bz)").fetchone()[0]
    p = dict(con.execute("SELECT z, count(*) FROM bz GROUP BY 1").fetchall())
    pairs = con.execute("SELECT a.z, b.z, count(*) FROM bz a JOIN bz b USING (cid, t_dat) WHERE a.z < b.z GROUP BY 1, 2").fetchall()
    out = {f"{a}|{b}": round(c * n / (p[a] * p[b]), 3) for a, b, c in pairs}
    _write_cache(path, out)
    return out


def lift_of(a: str, b: str) -> float:
    lf = zone_lift()
    return lf.get(f"{min(a, b)}|{max(a, b)}", 1.0)


def adjacent(b1: str, b2: str, gap: float = 4.0) -> bool:
    a, b = BLOCKS[b1], BLOCKS[b2]
    dx = max(0.0, max(a["x0"], b["x0"]) - min(a["x1"], b["x1"]))
    dz = max(0.0, max(a["z0"], b["z0"]) - min(a["z1"], b["z1"]))
    return math.hypot(dx, dz) <= gap


def cameras() -> list[tuple]:
    """Ceiling cameras: a grid with overlapping cones, plus entrance, tills and the corridor outside the fitting rooms."""
    def nearest(x, z):
        return min(BLOCKS.values(), key=lambda b: math.hypot(x - (b["x0"] + b["x1"]) / 2, z - (b["z0"] + b["z1"]) / 2))["name"]
    cams = [(x, z, 5.0, f"Area · {nearest(x, z)}") for z in (-14.0, -6.0, 2.0, 10.0) for x in (-20.0, -12.0, -4.0, 4.0, 12.0, 20.0)]
    cams += [(ENTRANCE["x"], 17.5, 3.4, "Entrance · New in"), (17.0, 15.0, 3.6, "Tills"), (-12.0, -15.2, 2.6, "Fitting-room entrance · outside only")]
    return cams


def build_layout(assign: dict | None = None, name: str = "Current store") -> dict:
    assign = dict(assign or DEFAULT_ASSIGN)
    return {
        "name": name, "store": STORE, "area_m2": STORE["w"] * STORE["d"],
        "blocks": BLOCKS, "assign": assign,
        "fixtures": {z: fixtures_for(z, assign[z]) for z in ZONES},
        "cabins": CABINS, "cabins_open": len(CABINS), "fitting": FITTING,
        "tills": TILLS, "tills_open": 3, "till_z": TILL_Z, "pay_z": PAY_Z,
        "entrance": ENTRANCE, "decompression": DECOMPRESSION,
        "columns": COLUMNS, "mannequins": MANNEQUINS, "decor": DECOR,
        "zone_names": ZONE_NAMES,
    }


# rules every department arrangement must respect (the optimizer only explores feasible ones)
FRONT_BLOCKS = ("C1", "E", "R1")          # the new-in area must be seen from the door
FITTING_BLOCKS = ("L", "C2")              # Ladies (most try-ons) next to the fitting rooms
SPACE_MIN, SPACE_MAX = .33, 7.0           # block area vs the area the department's sales justify


def feasible(assign: dict) -> tuple[bool, str]:
    if sorted(assign) != sorted(ZONES) or sorted(assign.values()) != sorted(BLOCKS):
        return False, "each department needs its own block"
    if assign["new_in"] not in FRONT_BLOCKS:
        return False, "New in must stay visible from the entrance"
    if assign["ladies"] not in FITTING_BLOCKS:
        return False, "Ladies must stay near the fitting rooms"
    fit = space_fit(assign)
    small = [ZONE_NAMES[z] for z, f in fit.items() if f < SPACE_MIN]
    if small:
        return False, f"not enough space for {', '.join(small)}"
    big = [ZONE_NAMES[z] for z, f in fit.items() if f > SPACE_MAX]
    if big:
        return False, f"block too large for {', '.join(big)}"
    return True, "ok"


def swapped(assign: dict, a: str, b: str) -> dict:
    out = dict(assign)
    out[a], out[b] = assign[b], assign[a]
    return out


def describe_assign(assign: dict) -> list[str]:
    """Departments that moved compared to the current store."""
    return [f"{ZONE_NAMES[z]} → {BLOCKS[b]['name']}" for z, b in assign.items() if DEFAULT_ASSIGN.get(z) != b]
