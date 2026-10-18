"""Turn data/hm_sample.json into a compact JS snippet for the FitPath mockup.

Writes data/hm_fitpath.js, defining `const HM = {...}`:
  articles: [id, name, type, colour, index, zone, price]  (price rounded to .99, approximate)
  baskets:  lists of indexes into `articles` (real H&M baskets)

Usage: python -I scripts/export_hm_js.py
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "hm_sample.json"
OUT = ROOT / "data" / "hm_fitpath.js"


def shelf_price(eur):
    """H&M prices are normalised in the dataset; show a plausible shelf price (x.99)."""
    return max(0.99, math.floor(eur) + 0.99)


def main():
    d = json.loads(SRC.read_text(encoding="utf-8"))
    index = {a["id"]: i for i, a in enumerate(d["articles"])}
    articles = [[a["id"], a["name"], a["type"], a["colour"], a["index"], a["zone"], shelf_price(a["eur"])] for a in d["articles"]]
    baskets = [[index[aid] for aid in b if aid in index] for b in d["baskets"]]
    baskets = [b[:6] for b in baskets if b]
    payload = {
        "window": d["meta"]["window"],
        "channel": d["meta"]["channel"],
        "stats": d["stats"],
        "articles": articles,
        "baskets": baskets,
    }
    OUT.write_text("const HM = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(articles)} articles, {len(baskets)} baskets, {OUT.stat().st_size/1024:.0f} KB")


if __name__ == "__main__":
    main()
