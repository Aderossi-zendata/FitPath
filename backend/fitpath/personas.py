"""Customer personas from the real H&M data.

1. Per-customer features from all their transactions (both channels) + customers.csv
2. KMeans on standardised features (k picked by silhouette)
3. Each cluster gets a readable name, a description, and the behaviour parameters the
   store simulation uses (zone preferences, try-on propensity, patience, ...).

Outputs: data/out/personas.json, data/parquet/customer_persona.parquet (cid -> persona)

Behaviour parameters are derived from the cluster's purchase data where possible; patience,
walking speed and browsing are assumptions scaled by age and novelty, and are flagged as such.

Usage: python -m fitpath.personas
"""
import json
import time

import duckdb
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from fitpath.analytics.real import PRICE_FACTOR
from fitpath.catalog import NEW_ARTICLE_DAYS, TRY_ON_GARMENTS, ZONE_SQL, ZONES
from fitpath.config import OUT, PARQUET, STORE_CHANNEL, pq

FEATURES = ["log_baskets", "log_ticket", "avg_units", "store_share", "weekend_share", "share_ladies", "share_men",
            "share_divided", "share_kids", "share_sport", "novelty_share", "discount_share", "price_index", "age"]
MIN_BASKETS = 2
SEED = 7


def customer_features(con) -> pd.DataFrame:
    try_on = ",".join(f"'{g}'" for g in TRY_ON_GARMENTS)
    return con.execute(f"""
        WITH tx AS (
            SELECT t.t_dat, t.cid, t.article_id, t.price * {PRICE_FACTOR} AS eur, t.price, t.channel,
                   a.product_type_name AS ptype, a.index_group_name AS igroup, a.garment_group_name AS garment,
                   {ZONE_SQL.replace('index_group_name', 'a.index_group_name').replace('garment_group_name', 'a.garment_group_name')} AS zone
            FROM '{pq("transactions")}' t JOIN '{pq("articles")}' a USING (article_id)),
        art AS (SELECT article_id, min(t_dat) AS first_sale, max(price) AS full_price FROM tx GROUP BY 1),
        med AS (SELECT ptype, median(eur) AS med FROM tx GROUP BY 1),
        b AS (SELECT cid, t_dat, channel, count(*) AS n, sum(eur) AS rev FROM tx GROUP BY 1, 2, 3),
        bf AS (SELECT cid, count(*) AS baskets, avg(n) AS avg_units, avg(rev) AS avg_ticket,
                      avg(CASE WHEN channel = {STORE_CHANNEL} THEN 1 ELSE 0 END) AS store_share,
                      avg(CASE WHEN isodow(t_dat) >= 6 THEN 1 ELSE 0 END) AS weekend_share,
                      sum(CASE WHEN channel = {STORE_CHANNEL} THEN 1 ELSE 0 END) AS store_baskets
               FROM b GROUP BY 1 HAVING count(*) >= {MIN_BASKETS}),
        tf AS (SELECT tx.cid,
                      avg(CASE WHEN igroup = 'Ladieswear' THEN 1 ELSE 0 END) AS share_ladies,
                      avg(CASE WHEN igroup = 'Menswear' THEN 1 ELSE 0 END) AS share_men,
                      avg(CASE WHEN igroup = 'Divided' THEN 1 ELSE 0 END) AS share_divided,
                      avg(CASE WHEN igroup = 'Baby/Children' THEN 1 ELSE 0 END) AS share_kids,
                      avg(CASE WHEN igroup = 'Sport' THEN 1 ELSE 0 END) AS share_sport,
                      avg(CASE WHEN tx.t_dat - art.first_sale <= {NEW_ARTICLE_DAYS} THEN 1 ELSE 0 END) AS novelty_share,
                      avg(CASE WHEN tx.price < 0.85 * art.full_price THEN 1 ELSE 0 END) AS discount_share,
                      avg(eur / med.med) AS price_index,
                      avg(CASE WHEN garment IN ({try_on}) THEN 1 ELSE 0 END) AS tryon_share,
                      {", ".join(f"avg(CASE WHEN zone = '{z}' THEN 1 ELSE 0 END) AS z_{z}" for z in ZONES if z != 'new_in')}
               FROM tx JOIN art USING (article_id) JOIN med USING (ptype)
               WHERE tx.cid IN (SELECT cid FROM bf) GROUP BY 1)
        SELECT bf.*, tf.* EXCLUDE (cid), c.age, c.club_member_status, c.fashion_news_frequency
        FROM bf JOIN tf USING (cid) JOIN '{pq("customers")}' c USING (cid)
    """).df()


# (phrase when the feature is high, phrase when it is low)
PHRASES = {
    "log_baskets": ("buys often", "buys rarely"), "log_ticket": ("high receipt", "low receipt"),
    "avg_units": ("many items per visit", "few items per visit"), "store_share": ("prefers the store", "prefers online"),
    "weekend_share": ("shops at weekends", "shops on weekdays"), "share_ladies": ("mostly Ladieswear", "little Ladieswear"),
    "share_men": ("also buys Menswear", "no Menswear"), "share_divided": ("Divided line", "little Divided"),
    "share_kids": ("kidswear", "no kidswear"), "share_sport": ("sportswear", "no sportswear"),
    "novelty_share": ("chases new arrivals", "buys items that have been in store a while"), "discount_share": ("buys on discount", "buys at full price"),
    "price_index": ("high price range", "low price range"), "age": ("older", "younger"),
}
# persona name from its most distinctive high feature
NAMES = {
    "share_kids": "Parent", "share_sport": "Sporty", "share_divided": "Young Divided", "share_men": "Shops for two",
    "avg_units": "Big basket", "log_ticket": "Big basket", "store_share": "Store regular", "discount_share": "Bargain hunter",
    "novelty_share": "Trend hunter", "share_ladies": "Premium ladies", "log_baskets": "Loyal regular", "price_index": "Premium",
    "weekend_share": "Weekend shopper", "age": "Mature shopper",
}


def _name(z: pd.Series, taken: set) -> tuple[str, str]:
    """Readable name + one-line description from a cluster's z-scored centroid."""
    high = [f for f in z.sort_values(ascending=False).index if z[f] > .3]
    low = [f for f in z.sort_values().index if z[f] < -.3]
    name = next((NAMES[f] for f in high if NAMES[f] not in taken), "Classic")
    parts = [PHRASES[f][0] for f in high[:3]] + [PHRASES[f][1] for f in low[:1]]
    text = ", ".join(parts) or "behaviour close to the average"
    return name, text[:1].upper() + text[1:]


def behaviour(c: pd.Series) -> dict:
    """Simulation parameters for a persona. 'derived' come from data, 'assumed' are documented heuristics."""
    zone_w = {z: float(c[f"z_{z}"]) for z in ZONES if z != "new_in"}
    zone_w["new_in"] = float(c.novelty_share)
    tot = sum(zone_w.values()) or 1
    age = float(c.age) if not np.isnan(c.age) else 35.0
    return {
        "derived": {
            "zone_weights": {z: round(w / tot, 3) for z, w in zone_w.items()},
            "items_per_basket": round(float(c.avg_units), 2),
            "avg_ticket_eur": round(float(c.avg_ticket), 2),
            "tryon_share": round(float(c.tryon_share), 3),
            "discount_sensitivity": round(float(c.discount_share), 3),
            "novelty_attraction": round(float(c.novelty_share), 3),
            "weekend_share": round(float(c.weekend_share), 3),
        },
        "assumed": {
            "fitting_prob": round(min(.85, .25 + .8 * float(c.tryon_share)), 3),
            "queue_patience_s": round(240 + 4 * (age - 35)),
            "walk_speed": round(max(.8, min(1.3, 1.15 - .006 * (age - 30))), 2),
            "extra_zones": round(.5 + 2.5 * float(c.novelty_share) + float(c.weekend_share), 2),
        },
    }


def build(k: int | None = None) -> dict:
    t0 = time.time()
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    df = customer_features(con).sort_values("cid").reset_index(drop=True)   # DuckDB row order is not stable: sort for reproducible clusters
    print(f"  features for {len(df):,} customers in {time.time() - t0:.0f}s")
    df["log_baskets"] = np.log1p(df.baskets)
    df["log_ticket"] = np.log1p(df.avg_ticket)
    df["age"] = df.age.fillna(df.age.median())
    X = StandardScaler().fit_transform(df[FEATURES])

    rng = np.random.default_rng(SEED)
    sample = rng.choice(len(df), size=min(60000, len(df)), replace=False)
    if k is None:
        scores = {}
        for kk in range(5, 9):
            km = KMeans(kk, n_init=4, random_state=SEED).fit(X[sample])
            scores[kk] = silhouette_score(X[sample][:15000], km.labels_[:15000])
            print(f"  k={kk} silhouette={scores[kk]:.3f}")
        k = max(scores, key=scores.get)
    km = KMeans(k, n_init=8, random_state=SEED).fit(X[sample])
    df["persona"] = km.predict(X)

    means = df.groupby("persona")[FEATURES + ["baskets", "avg_ticket", "tryon_share", "store_baskets"] + [f"z_{z}" for z in ZONES if z != "new_in"]].mean()
    zc = (means[FEATURES] - df[FEATURES].mean()) / df[FEATURES].std()
    personas, used = [], set()
    # name the most distinctive clusters first so they get the clearest names
    for p in zc.abs().max(axis=1).sort_values(ascending=False).index:
        name, desc = _name(zc.loc[p], used)
        used.add(name)
        size = int((df.persona == p).sum())
        store_visits = int(df.loc[df.persona == p, "store_baskets"].sum())
        top = zc.loc[p].sort_values()
        personas.append({
            "id": int(p), "name": name, "description": desc, "customers": size,
            "share_customers": round(size / len(df), 4),
            "share_store_visits": round(store_visits / max(1, df.store_baskets.sum()), 4),
            "profile": {f: round(float(means.loc[p, f]), 3) for f in FEATURES + ["baskets", "avg_ticket"]},
            "distinctive": {"high": [f for f in top.index[::-1][:3]], "low": [f for f in top.index[:2]]},
            "club_active_share": round(float((df.loc[df.persona == p, "club_member_status"] == "ACTIVE").mean()), 3),
            "behaviour": behaviour(means.loc[p]),
        })
    con.register("cp", df[["cid", "persona"]])
    con.execute(f"COPY cp TO '{(PARQUET / 'customer_persona.parquet').as_posix()}' (FORMAT parquet)")
    personas.sort(key=lambda x: x["id"])
    out = {"k": k, "customers_clustered": len(df), "min_baskets": MIN_BASKETS, "features": FEATURES, "personas": personas,
           "note": "Clustering on all H&M transactions per customer (both channels). Behaviour 'assumed' fields are heuristics."}
    (OUT / "personas.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  wrote personas.json ({k} personas) in {time.time() - t0:.0f}s")
    return out


if __name__ == "__main__":
    import sys
    # k = 7 is the segmentation used across the app and the pitch; pass "auto" to pick k by silhouette instead
    arg = sys.argv[1] if len(sys.argv) > 1 else "7"
    res = build(k=None if arg == "auto" else int(arg))
    for p in res["personas"]:
        print(f"  {p['name']:22s} {p['share_customers']:6.1%} customers  {p['share_store_visits']:6.1%} store visits  — {p['description']}")
