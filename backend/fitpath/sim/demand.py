"""Real daily demand for the simulated store, taken from H&M store-channel baskets.

For each simulated date we load that day's real baskets (adult articles only), attach the
customer's persona, and scale the volume to one store: busy days in the data are busy days here.
"""
from collections import defaultdict
from datetime import date, timedelta

import duckdb

from fitpath.analytics.real import PRICE_FACTOR
from fitpath.catalog import NEW_ARTICLE_DAYS, TRY_ON_GARMENTS, ZONE_SQL
from fitpath.config import PARQUET, STORE_CHANNEL, pq


class Demand:
    def __init__(self):
        self.con = duckdb.connect()
        self.con.execute("SET preserve_insertion_order = false")
        # volume relative to the surrounding year (±182 days): keeps weekly and seasonal swings,
        # removes the long-term decline of the store channel in the data
        self.con.execute(f"""
            CREATE TEMP TABLE daily AS
            WITH d AS (SELECT t_dat, count(DISTINCT cid) AS baskets FROM '{pq("transactions")}'
                       WHERE channel = {STORE_CHANNEL} GROUP BY 1)
            SELECT t_dat, baskets, baskets / avg(baskets) OVER (ORDER BY t_dat RANGE BETWEEN INTERVAL 182 DAYS PRECEDING
                                                                   AND INTERVAL 182 DAYS FOLLOWING) AS idx
            FROM d""")
        self.first_day, self.last_day = self.con.execute("SELECT min(t_dat), max(t_dat) FROM daily").fetchone()

    def volume_index(self, d: date) -> float:
        r = self.con.execute("SELECT idx FROM daily WHERE t_dat = ?", [d]).fetchone()
        return float(r[0]) if r else 1.0

    def baskets(self, start: date, days: int, dates: list[date] | None = None) -> dict:
        """date -> list of baskets {cid, persona, items:[...]} for the store channel (a range, or explicit dates)."""
        end = start + timedelta(days=days - 1)
        where, params = ("t.t_dat BETWEEN ? AND ?", [start, end]) if not dates else (f"t.t_dat IN ({','.join('?' * len(dates))})", list(dates))
        try_on = ",".join(f"'{g}'" for g in TRY_ON_GARMENTS)
        zone = ZONE_SQL.replace("index_group_name", "a.index_group_name").replace("garment_group_name", "a.garment_group_name")
        rows = self.con.execute(f"""
            WITH fs AS (SELECT article_id, min(t_dat) AS first_sale FROM '{pq("transactions")}' GROUP BY 1)
            SELECT t.t_dat, t.cid, cp.persona, t.article_id, a.prod_name, a.product_type_name, a.garment_group_name,
                   a.colour_group_name, {zone} AS zone, t.price * {PRICE_FACTOR} AS eur,
                   a.garment_group_name IN ({try_on}) AS tryon, (t.t_dat - fs.first_sale) <= {NEW_ARTICLE_DAYS} AS is_new
            FROM '{pq("transactions")}' t
            JOIN '{pq("articles")}' a USING (article_id)
            JOIN fs USING (article_id)
            LEFT JOIN '{(PARQUET / "customer_persona.parquet").as_posix()}' cp USING (cid)
            WHERE t.channel = {STORE_CHANNEL} AND {where}""", params).fetchall()
        by_day = defaultdict(lambda: defaultdict(list))
        persona_of = {}
        for d, cid, persona, aid, name, ptype, garment, colour, z, eur, tryon, is_new in rows:
            if z is None:
                continue
            persona_of[cid] = persona
            by_day[d][cid].append({"article_id": aid, "name": name, "ptype": ptype, "garment": garment, "colour": colour,
                                   "zone": z, "eur": round(eur, 2), "tryon": bool(tryon), "new": bool(is_new)})
        # deterministic order (DuckDB returns rows in parallel order): same seed -> same customers
        return {d: [{"cid": cid, "persona": persona_of[cid], "items": sorted(items, key=lambda i: i["article_id"])}
                    for cid, items in sorted(custs.items())]
                for d, custs in sorted(by_day.items())}
