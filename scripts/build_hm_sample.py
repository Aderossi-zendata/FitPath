"""Build a small sample of the H&M Kaggle dataset for the FitPath store mockup.

Reads data/raw/articles.csv and data/raw/transactions_train.csv.zip (streamed,
never fully extracted), maps articles onto the six store zones and exports
real baskets to data/hm_sample.json.

Usage: python -I scripts/build_hm_sample.py [--weeks 4] [--channel 1]
"""
import argparse
import collections
import csv
import datetime as dt
import io
import json
import random
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "hm_sample.json"

# Prices in the dataset are normalised; ~590 is the factor commonly used by the
# Kaggle community to get back to an approximate euro value. Approximation only.
PRICE_FACTOR = 590

ZONES = ["novita", "donna", "uomo", "denim", "basics", "accessori"]
PER_ZONE = 50
BASKETS_OUT = 3000
NEW_ARRIVAL_DAYS = 14

BASIC_GROUPS = {"Jersey Basic", "Socks and Tights", "Under-, Nightwear"}
ACCESSORY_GROUPS = {"Accessories", "Shoes"}


def base_zone(a):
    """Zone from the catalogue alone; 'novita' is decided later from first-sale date."""
    if a["index_group_name"] in {"Baby/Children"}:
        return None
    g = a["garment_group_name"]
    if g == "Trousers Denim":
        return "denim"
    if g in ACCESSORY_GROUPS:
        return "accessori"
    if g in BASIC_GROUPS:
        return "basics"
    if a["index_group_name"] in {"Ladieswear", "Divided"}:
        return "donna"
    if a["index_group_name"] == "Menswear":
        return "uomo"
    return None  # Sport and the rest stay out of this store


def load_articles():
    with open(RAW / "articles.csv", encoding="utf-8", newline="") as f:
        return {r["article_id"]: r for r in csv.DictReader(f)}


def stream_transactions():
    with zipfile.ZipFile(RAW / "transactions_train.csv.zip") as z:
        with z.open("transactions_train.csv") as raw:
            yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=4, help="weeks of history to sample, ending at the last date")
    ap.add_argument("--channel", default="1", help="sales_channel_id treated as the physical store")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    articles = load_articles()
    print(f"articles: {len(articles):,}")

    # Pass 1 over the stream: first-sale date per article, channel totals, last date.
    first_sale, channel_rows = {}, collections.Counter()
    last_date, rows = "", 0
    # Baskets kept only for a generous recent window; trimmed once the end date is known.
    recent = collections.defaultdict(list)
    keep_from = "2020-07-01"
    for r in stream_transactions():
        rows += 1
        d, aid, ch = r["t_dat"], r["article_id"], r["sales_channel_id"]
        if aid not in first_sale:
            first_sale[aid] = d
        channel_rows[ch] += 1
        if d > last_date:
            last_date = d
        if d >= keep_from and ch == args.channel:
            recent[(r["customer_id"], d)].append((aid, float(r["price"])))
        if rows % 5_000_000 == 0:
            print(f"  {rows:,} rows…")
    print(f"transactions: {rows:,}, last date {last_date}, rows per channel {dict(channel_rows)}")

    end = dt.date.fromisoformat(last_date)
    start = (end - dt.timedelta(weeks=args.weeks)).isoformat()
    new_from = (end - dt.timedelta(days=NEW_ARRIVAL_DAYS)).isoformat()
    baskets = {k: v for k, v in recent.items() if k[1] > start}
    print(f"baskets in window {start}..{last_date} (channel {args.channel}): {len(baskets):,}")

    # Zone per article and sales count in the window.
    sales, price_sum = collections.Counter(), collections.defaultdict(float)
    for items in baskets.values():
        for aid, p in items:
            sales[aid] += 1
            price_sum[aid] += p

    def zone_of(aid):
        a = articles.get(aid)
        if not a:
            return None
        z = base_zone(a)
        if z and z not in ("basics", "accessori") and first_sale.get(aid, "") >= new_from:
            return "novita"
        return z

    by_zone = collections.defaultdict(list)
    for aid, n in sales.most_common():
        z = zone_of(aid)
        if z and len(by_zone[z]) < PER_ZONE:
            by_zone[z].append(aid)
    kept = {aid for z in ZONES for aid in by_zone[z]}

    out_articles = []
    for z in ZONES:
        for aid in by_zone[z]:
            a = articles[aid]
            out_articles.append({
                "id": aid,
                "name": a["prod_name"],
                "type": a["product_type_name"],
                "colour": a["colour_group_name"],
                "index": a["index_name"],
                "garment": a["garment_group_name"],
                "zone": z,
                "eur": round(price_sum[aid] / sales[aid] * PRICE_FACTOR, 2),
                "sales": sales[aid],
                "first_sale": first_sale[aid],
            })

    # Real baskets, restricted to the kept catalogue (a basket keeps its kept items).
    full_sizes = collections.Counter(min(len(v), 8) for v in baskets.values())
    cand = [[aid for aid, _ in v if aid in kept] for v in baskets.values()]
    cand = [b for b in cand if b]
    sample = random.sample(cand, min(BASKETS_OUT, len(cand)))
    tickets = [sum(p for _, p in v) * PRICE_FACTOR for v in baskets.values()]

    data = {
        "meta": {
            "source": "H&M Personalized Fashion Recommendations (Kaggle), non-commercial use",
            "window": [start, last_date],
            "channel": args.channel,
            "channel_note": "sales_channel_id meaning is not documented by H&M; treated as the physical store by assumption",
            "price_factor": PRICE_FACTOR,
            "price_note": "eur = normalised price x factor, approximate",
            "new_arrival_days": NEW_ARRIVAL_DAYS,
        },
        "stats": {
            "baskets_in_window": len(baskets),
            "basket_size_distribution": {str(k): v for k, v in sorted(full_sizes.items())},
            "avg_items_per_basket": round(sum(len(v) for v in baskets.values()) / len(baskets), 2),
            "avg_ticket_eur": round(sum(tickets) / len(tickets), 2),
            "articles_per_zone": {z: len(by_zone[z]) for z in ZONES},
        },
        "articles": out_articles,
        "baskets": sample,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(out_articles)} articles, {len(sample)} baskets, {OUT.stat().st_size/1024:.0f} KB")
    print(json.dumps(data["stats"], indent=2))


if __name__ == "__main__":
    main()
