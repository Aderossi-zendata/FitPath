"""Price elasticity per store zone, estimated from H&M price changes.

For each article and week: units sold and average price. Within each article (article fixed effect) and
within each zone-week (removes seasonality and chain-wide sales events) we regress log units on
log(price / article's highest price). The slope is the elasticity: -1.5 means a 20% discount sells
(0.8 ** -1.5 - 1) = 40% more units.

Markdowns are partly reactive (prices drop when an article stops selling), which biases the estimate
towards zero; values are clipped to a plausible range and flagged as estimates.

Usage: python -m fitpath.elasticity
"""
import json

import duckdb
import numpy as np

from fitpath.catalog import ZONE_SQL
from fitpath.config import OUT, pq

CLIP = (-3.0, -0.6)
PRIOR = -1.2              # reference elasticity for apparel when the data cannot identify it
PRIOR_RANGE = (-2.0, -0.6)  # sampled uniformly in each simulation replica


def estimate() -> dict:
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    zone = ZONE_SQL.replace("index_group_name", "a.index_group_name").replace("garment_group_name", "a.garment_group_name")
    df = con.execute(f"""
        WITH w AS (SELECT article_id, date_trunc('week', t_dat) AS wk, count(*) AS units, avg(price) AS p
                   FROM '{pq("transactions")}' GROUP BY 1, 2),
             art AS (SELECT article_id, max(p) AS full_p, min(wk) AS first_wk FROM w GROUP BY 1 HAVING count(*) >= 6)
        SELECT w.article_id, w.wk, least(52, datediff('week', art.first_wk, w.wk)) AS age,
               ln(w.units) AS lu, ln(w.p / art.full_p) AS lp, {zone} AS zone
        FROM w JOIN art USING (article_id) JOIN '{pq("articles")}' a USING (article_id)
        WHERE w.p > 0""").df()
    df = df[df.zone.notna()]
    # three-way demeaning: article, zone-week (seasons, chain-wide sales) and zone-age (product life cycle:
    # markdowns come late in an article's life, when its sales were already falling)
    for _ in range(4):
        for keys in (["article_id"], ["zone", "wk"], ["zone", "age"]):
            for c in ("lu", "lp"):
                df[c] = df[c] - df.groupby(keys)[c].transform("mean")
    out = {}
    for z, g in df.groupby("zone"):
        var = float((g.lp ** 2).sum())
        raw = float((g.lp * g.lu).sum() / var) if var > 0 else 0.0
        # identified only if the data gives a clearly negative slope; otherwise use the reference prior
        identified = raw < CLIP[1]
        e = float(np.clip(raw, *CLIP)) if identified else PRIOR
        out[z] = {"elasticity": round(e, 3), "range": list(PRIOR_RANGE) if not identified else [round(e * 1.3, 3), round(e * .7, 3)],
                  "data_estimate": round(raw, 3), "identified": bool(identified), "rows": int(len(g)),
                  "uplift_20pct": round((0.8 ** e) - 1, 3)}
    res = {"by_zone": out, "prior": PRIOR, "prior_range": PRIOR_RANGE,
           "note": "In the H&M data discounts are reactive (what already sells poorly gets discounted), so elasticity cannot be measured reliably: "
                   "where the data estimate is not clearly negative, a reference value (-1.2) with range -0.6 / -2.0 is used, "
                   "drawn at random in every simulation replica."}
    (OUT / "elasticity.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def load() -> dict:
    p = OUT / "elasticity.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else estimate()


if __name__ == "__main__":
    for z, v in estimate()["by_zone"].items():
        print(f"  {z:10s} elasticity {v['elasticity']:6.2f} (from data {v['data_estimate']:6.2f}, identified {v['identified']})  20% discount -> {v['uplift_20pct']:+.0%} units  [{v['rows']:,} rows]")
