"""Insights computed on the real H&M transactions (no simulation involved).

Every function returns plain JSON-able data. `run_all` writes data/out/analytics_real.json.

A basket is all the items one customer bought on one day in one channel.
Prices are normalised in the dataset; EUR = price x 590 is an approximation used by the Kaggle community.

Usage: python -m fitpath.analytics.real [--scope store|all]
"""
import argparse
import json
import time
from datetime import date

import duckdb

from fitpath.config import OUT, STORE_CHANNEL, pq

PRICE_FACTOR = 590
SEASON_SQL = """CASE WHEN month(t_dat) IN (12,1,2) THEN 'Winter' WHEN month(t_dat) IN (3,4,5) THEN 'Spring'
                     WHEN month(t_dat) IN (6,7,8) THEN 'Summer' ELSE 'Autumn' END"""
SEASONS = ["Winter", "Spring", "Summer", "Autumn"]


def connect(scope: str = "store") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    where = f"WHERE t.channel = {STORE_CHANNEL}" if scope == "store" else ""
    con.execute(f"""
        CREATE VIEW tx AS
        SELECT t.t_dat, t.cid, t.article_id, t.price * {PRICE_FACTOR} AS eur, t.channel,
               a.product_code, a.prod_name, a.product_type_name AS ptype, a.garment_group_name AS garment,
               a.index_group_name AS igroup, a.index_name, a.colour_group_name AS colour, a.section_name AS section,
               {SEASON_SQL} AS season
        FROM '{pq("transactions")}' t JOIN '{pq("articles")}' a USING (article_id)
        {where}
    """)
    return con


def _rows(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        out.append({c: (v.isoformat() if isinstance(v, date) else (round(v, 4) if isinstance(v, float) else v)) for c, v in zip(cols, r)})
    return out


def overview(con) -> dict:
    r = _rows(con, """
        SELECT min(t_dat) AS start, max(t_dat) AS "end", count(*) AS units, count(DISTINCT cid) AS customers,
               count(DISTINCT article_id) AS articles, round(sum(eur)) AS revenue_eur,
               count(DISTINCT (cid, t_dat)) AS baskets
        FROM tx""")[0]
    r["avg_basket_units"] = round(r["units"] / r["baskets"], 2)
    r["avg_ticket_eur"] = round(r["revenue_eur"] / r["baskets"], 2)
    return r


def weekly(con) -> list[dict]:
    return _rows(con, """
        SELECT date_trunc('week', t_dat)::DATE AS week, round(sum(eur)) AS revenue_eur, count(*) AS units,
               count(DISTINCT (cid, t_dat)) AS baskets
        FROM tx GROUP BY 1 ORDER BY 1""")


def monthly_by_group(con) -> list[dict]:
    return _rows(con, """
        SELECT date_trunc('month', t_dat)::DATE AS month, igroup, round(sum(eur)) AS revenue_eur, count(*) AS units
        FROM tx GROUP BY 1, 2 ORDER BY 1, 2""")


def season_lift(con, level: str = "garment", top: int = 6) -> dict:
    """Categories that sell much more (or less) than usual in each season: share in season / share overall."""
    rows = _rows(con, f"""
        WITH s AS (SELECT season, {level} AS cat, count(*) AS n FROM tx GROUP BY 1, 2),
             st AS (SELECT season, sum(n) AS tot FROM s GROUP BY 1),
             c AS (SELECT cat, sum(n) AS n FROM s GROUP BY 1),
             ct AS (SELECT sum(n) AS tot FROM c)
        SELECT s.season, s.cat, s.n AS units, s.n / st.tot AS share_season, c.n / ct.tot AS share_all,
               (s.n / st.tot) / (c.n / ct.tot) AS lift
        FROM s JOIN st USING (season) JOIN c USING (cat), ct
        WHERE c.n > 20000""")
    out = {}
    for season in SEASONS:
        rs = sorted([r for r in rows if r["season"] == season], key=lambda r: r["lift"], reverse=True)
        out[season] = {"up": rs[:top], "down": rs[-top:][::-1]}
    return out


def season_lift_full(con) -> dict:
    """season -> garment group -> lift (share in season / share over the year), every adult category."""
    rows = _rows(con, """
        WITH s AS (SELECT season, garment AS cat, count(*) AS n FROM tx WHERE igroup <> 'Baby/Children' GROUP BY 1, 2),
             st AS (SELECT season, sum(n) AS tot FROM s GROUP BY 1),
             c AS (SELECT cat, sum(n) AS n FROM s GROUP BY 1), ct AS (SELECT sum(n) AS tot FROM c)
        SELECT s.season, s.cat, (s.n / st.tot) / (c.n / ct.tot) AS lift
        FROM s JOIN st USING (season) JOIN c USING (cat), ct WHERE c.n > 5000""")
    out = {}
    for r in rows:
        out.setdefault(r["season"], {})[r["cat"]] = round(r["lift"], 3)
    return out


def weekday(con) -> dict:
    days = _rows(con, """
        SELECT isodow(t_dat) AS dow, count(*) AS units, round(sum(eur)) AS revenue_eur,
               count(DISTINCT (cid, t_dat)) AS baskets
        FROM tx GROUP BY 1 ORDER BY 1""")
    weekend = _rows(con, """
        WITH g AS (SELECT garment, sum(CASE WHEN isodow(t_dat) >= 6 THEN 1 ELSE 0 END) AS we, count(*) AS n FROM tx GROUP BY 1),
             t AS (SELECT sum(we)::DOUBLE / sum(n) AS base FROM g)
        SELECT garment, n AS units, we::DOUBLE / n AS weekend_share, (we::DOUBLE / n) / t.base AS lift
        FROM g, t WHERE n > 20000 ORDER BY lift DESC""")
    return {"days": days, "weekend_lift": weekend[:6], "weekday_lift": weekend[-6:][::-1]}


def month_day(con) -> list[dict]:
    """Payday effect: average daily revenue by day of month, indexed to 100."""
    rows = _rows(con, """
        WITH d AS (SELECT t_dat, sum(eur) AS rev FROM tx GROUP BY 1)
        SELECT day(t_dat) AS dom, avg(rev) AS avg_rev FROM d GROUP BY 1 ORDER BY 1""")
    mean = sum(r["avg_rev"] for r in rows) / len(rows)
    for r in rows:
        r["index"] = round(r["avg_rev"] / mean * 100, 1)
    return rows


def markdowns(con) -> list[dict]:
    """Weekly average discount vs each article's highest observed price: shows when the sales happen."""
    return _rows(con, """
        WITH mx AS (SELECT article_id, max(eur) AS full_price FROM tx GROUP BY 1)
        SELECT date_trunc('week', t_dat)::DATE AS week, round(1 - sum(eur) / sum(full_price), 4) AS avg_discount,
               round(avg(CASE WHEN eur < 0.85 * full_price THEN 1 ELSE 0 END), 4) AS share_discounted
        FROM tx JOIN mx USING (article_id) GROUP BY 1 ORDER BY 1""")


def top_by_season(con, top: int = 10) -> dict:
    rows = _rows(con, f"""
        WITH s AS (SELECT season, product_code, any_value(prod_name) AS name, any_value(ptype) AS ptype,
                          any_value(igroup) AS igroup, count(*) AS units, round(sum(eur)) AS revenue_eur
                   FROM tx GROUP BY 1, 2)
        SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY season ORDER BY units DESC) AS rk FROM s) WHERE rk <= {top}""")
    return {s: [r for r in rows if r["season"] == s] for s in SEASONS}


def trends(con, weeks: int = 8, top: int = 10) -> dict:
    """Product types growing or shrinking: last N weeks vs the same weeks one year earlier."""
    rows = _rows(con, f"""
        WITH b AS (SELECT max(t_dat) AS last FROM tx),
             cur AS (SELECT ptype, count(*) AS n FROM tx, b WHERE t_dat > b.last - INTERVAL {weeks} WEEK GROUP BY 1),
             prev AS (SELECT ptype, count(*) AS n FROM tx, b
                      WHERE t_dat > b.last - INTERVAL {weeks} WEEK - INTERVAL 52 WEEK AND t_dat <= b.last - INTERVAL 52 WEEK GROUP BY 1),
             ct AS (SELECT sum(n) AS t FROM cur), pt AS (SELECT sum(n) AS t FROM prev)
        SELECT cur.ptype, cur.n AS units_now, prev.n AS units_year_ago,
               (cur.n / ct.t) / (prev.n / pt.t) - 1 AS share_change
        FROM cur JOIN prev USING (ptype), ct, pt WHERE prev.n > 2000 AND cur.n > 500""")
    rows.sort(key=lambda r: r["share_change"], reverse=True)
    return {"weeks": weeks, "rising": rows[:top], "falling": rows[-top:][::-1]}


def basket_pairs(con, level: str = "ptype", weeks: int = 52, min_pair: int = 300, top: int = 25) -> dict:
    """Pairs bought in the same basket more often than chance (lift > 1). Uses the last `weeks` of data."""
    rows = _rows(con, f"""
        WITH b AS (SELECT max(t_dat) AS last FROM tx),
             bi AS (SELECT DISTINCT cid, t_dat, {level} AS item FROM tx, b WHERE t_dat > b.last - INTERVAL {weeks} WEEK),
             nb AS (SELECT count(DISTINCT (cid, t_dat))::DOUBLE AS n FROM bi),
             single AS (SELECT item, count(*) AS n FROM bi GROUP BY 1),
             pairs AS (SELECT x.item AS a, y.item AS b, count(*) AS n
                       FROM bi x JOIN bi y ON x.cid = y.cid AND x.t_dat = y.t_dat AND x.item < y.item
                       GROUP BY 1, 2 HAVING count(*) >= {min_pair})
        SELECT p.a, p.b, p.n AS baskets, p.n / nb.n AS support,
               p.n::DOUBLE / sa.n AS conf_a_to_b, p.n::DOUBLE / sb.n AS conf_b_to_a,
               (p.n / nb.n) / ((sa.n / nb.n) * (sb.n / nb.n)) AS lift
        FROM pairs p JOIN single sa ON sa.item = p.a JOIN single sb ON sb.item = p.b, nb""")
    by_lift = sorted([r for r in rows if r["a"] != r["b"]], key=lambda r: r["lift"], reverse=True)
    by_count = sorted(rows, key=lambda r: r["baskets"], reverse=True)
    return {"level": level, "weeks": weeks, "top_lift": by_lift[:top], "most_common": by_count[:top]}


def run_all(scope: str = "store") -> dict:
    con = connect(scope)
    steps = [
        ("overview", overview), ("weekly", weekly), ("monthly_by_group", monthly_by_group),
        ("season_lift", season_lift), ("season_lift_full", season_lift_full), ("weekday", weekday), ("month_day", month_day),
        ("markdowns", markdowns), ("top_by_season", top_by_season), ("trends", trends),
        ("basket_types", lambda c: basket_pairs(c, "ptype")),
        ("basket_articles", lambda c: basket_pairs(c, "product_code", weeks=12, min_pair=40)),
    ]
    res = {"scope": scope, "store_channel": STORE_CHANNEL, "price_note": f"EUR = normalised price x {PRICE_FACTOR}, approximate"}
    for name, fn in steps:
        t = time.time()
        res[name] = fn(con)
        print(f"  {name:18s} {time.time() - t:6.1f}s")
    # readable names for article-level pairs
    codes = {r[k] for r in res["basket_articles"]["top_lift"] + res["basket_articles"]["most_common"] for k in ("a", "b")}
    if codes:
        names = dict(con.execute(f"""SELECT product_code, any_value(prod_name || ' · ' || product_type_name)
                                     FROM '{pq("articles")}' WHERE product_code IN ({','.join(map(str, codes))}) GROUP BY 1""").fetchall())
        res["basket_articles"]["names"] = {str(k): v for k, v in names.items()}
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["store", "all"], default="store")
    args = ap.parse_args()
    t0 = time.time()
    out = run_all(args.scope)
    path = OUT / f"analytics_real_{args.scope}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {path} in {time.time() - t0:.0f}s")
