#!/usr/bin/env python3
"""Look up real Dutch supermarket prices for shopping-list items. Stdlib only.

Data: the open Checkjebon dataset (github.com/supermarkt/checkjebon), refreshed daily,
covering AH, Jumbo, Lidl, PLUS, Dirk, DekaMarkt, Hoogvliet, SPAR, Vomar, Poiesz.

Usage:  python3 prices.py queries.json > prices.txt
queries.json is a list of objects:
  {"item": "Chicken breast (kipfilet)",   # label used in the plan
   "q": "kipfilet|kip filet",             # Dutch search terms; "|" separates alternatives, every word must match
   "need": "600 g",                        # amount the plan uses (g / ml / stuks); optional
   "shops": ["lidl", "ah", "jumbo"],       # optional, default lidl+ah+jumbo
   "exclude": ["gerookt", "reepjes"]}      # optional extra words to drop from matches
Output per item and shop: best-value matches with pack size, shelf price, unit price and
the cost of buying enough packs for `need`. Items without a usable match say so explicitly.
"""
import json, math, os, re, sys, time, urllib.request

DATA_URL = "https://raw.githubusercontent.com/supermarkt/checkjebon/main/data/supermarkets.json"
META_URL = "https://api.github.com/repos/supermarkt/checkjebon/commits?path=data/supermarkets.json&per_page=1"
CACHE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "checkjebon-supermarkets.json")
DEFAULT_SHOPS = ["lidl", "ah", "jumbo"]
JUNK = ["kattenvoer", "hondenvoer", " kat ", " hond ", "noedels", "instant", "chips", "koek", "muffin",
        "jam", "fruitspread", "coupon", "testsieger", "testwinnaar", "smaak", "cake", "ijs", "snack",
        "salade", "maaltijd", "bakmix", "mix voor", "kroket", "croquet", "schnitzel", "saus", "gesuikerd"]
TOP = 3


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "meal-plan-prices/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_data():
    if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 12 * 3600:
        with open(CACHE, "rb") as f:
            return json.load(f)
    raw = fetch(DATA_URL)
    data = json.loads(raw)
    with open(CACHE, "wb") as f:
        f.write(raw)
    return data


def dataset_date():
    try:
        return json.loads(fetch(META_URL, 15))[0]["commit"]["committer"]["date"][:10]
    except Exception:
        return "unknown"


UNIT_RE = re.compile(r"(?:ca\.?\s*)?(\d+(?:[.,]\d+)?)\s*(kg|kilo|g|gr|gram|l|liter|litre|ml|cl|stuks|stuk|st|pcs)\b", re.I)
MULTI_RE = re.compile(r"(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*(kg|g|gr|gram|l|liter|ml|cl)\b", re.I)


def parse_amount(s):
    """Return (qty, unit) with unit in g / ml / st, or None."""
    if not s:
        return None
    m = MULTI_RE.search(s)
    if m:
        n, q, u = int(m.group(1)), float(m.group(2).replace(",", ".")), m.group(3).lower()
        base = parse_amount(f"{q} {u}")
        return (n * base[0], base[1]) if base else None
    m = UNIT_RE.search(s)
    if not m:
        if re.search(r"\bper stuk\b", s, re.I):
            return (1, "st")
        return None
    q, u = float(m.group(1).replace(",", ".")), m.group(2).lower()
    if u in ("kg", "kilo"):
        return (q * 1000, "g")
    if u in ("g", "gr", "gram"):
        return (q, "g")
    if u in ("l", "liter", "litre"):
        return (q * 1000, "ml")
    if u == "cl":
        return (q * 10, "ml")
    if u == "ml":
        return (q, "ml")
    return (q, "st")


def unit_price(price, amount):
    if not amount:
        return None
    qty, unit = amount
    if qty <= 0:
        return None
    if unit == "g":
        return price / qty * 1000, "€/kg"
    if unit == "ml":
        return price / qty * 1000, "€/l"
    return price / qty, "€/st"


def matches(name, q, excludes):
    n = f" {name.lower()} "
    if any(j in n for j in JUNK) or any(e.lower() in n for e in excludes):
        return False
    return any(all(w in n for w in alt.lower().split()) for alt in q.split("|") if alt.strip())


def lookup(data, query):
    shops = query.get("shops") or DEFAULT_SHOPS
    need = parse_amount(query.get("need", ""))
    excludes = query.get("exclude", [])
    out = {}
    for shop in data:
        if shop["n"] not in shops:
            continue
        hits = []
        for p in shop["d"]:
            if p["p"] < 0.10 or not matches(p["n"], query["q"], excludes):
                continue
            amount = parse_amount(p.get("s") or "") or parse_amount(p["n"])
            up = unit_price(p["p"], amount)
            hits.append((p, amount, up))
        # rank: items with a usable unit price first (cheapest per unit), then by shelf price
        hits.sort(key=lambda h: (0, h[2][0]) if h[2] else (1, h[0]["p"]))
        rows = []
        for p, amount, up in hits[:TOP]:
            row = {"name": p["n"], "size": p.get("s") or "", "price": p["p"],
                   "url": (shop.get("u") or "") + p.get("l", "")}
            if up:
                row["unit_price"] = f"€{up[0]:.2f}{up[1]}"
            if need and amount and amount[1] == need[1]:
                packs = max(1, math.ceil(need[0] * 0.93 / amount[0]))  # a pack within ~7% of the need counts as enough
                row["packs"] = packs
                row["cost"] = round(packs * p["p"], 2)
            rows.append(row)
        out[shop["n"]] = rows
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    with open(sys.argv[1]) as f:
        queries = json.load(f)
    try:
        data = load_data()
    except Exception as e:
        print(f"PRICE LOOKUP FAILED: could not load dataset ({e}). Treat all prices as unverified.")
        sys.exit(1)
    print(f"Source: Checkjebon open dataset, updated {dataset_date()} (regular shelf prices, promotions not included)")
    result = {}
    for q in queries:
        need = q.get("need", "")
        print(f"\n## {q['item']}" + (f"  [need {need}]" if need else ""))
        res = lookup(data, q)
        result[q["item"]] = res
        for shop, rows in res.items():
            if not rows:
                print(f"  {shop:5}: no match — estimate and mark it as such")
                continue
            for r in rows:
                line = f"  {shop:5}: {r['name']} ({r['size'] or 'size n/a'}) €{r['price']:.2f}"
                if "unit_price" in r:
                    line += f" → {r['unit_price']}"
                if "cost" in r:
                    line += f" · {r['packs']} pack(s) for {need} = €{r['cost']:.2f}"
                print(line)
    with open("prices.json", "w") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
