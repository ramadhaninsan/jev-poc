#!/usr/bin/env python3
"""
TCG deal gate PoC: is a marketplace listing a STEAL?

3 layers, Jev is the decision gate (the "is this cheap enough to alert user?" call):
  L1 scrape  : marketplace listing  -> {name, asking, sold_out, url}   (snkrdunk stand-in for mercari)
  L2 lookup  : TCGplayer market price for the matching card            (mp-search-api, no key/browser)
  L3 Jev     : decide STEAL / MARKET / OVERPRICED  given asking vs market (+condition), then "alert?"

Run:  python3 tcg_deal_gate.py
Env:  OPENROUTER_API_KEY (for the Jev layer only)
"""
import re, json, os, sys, ssl, urllib.request, urllib.parse, urllib.error

OPENROUTER = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL  = os.environ.get("JEV_MODEL", "typesafe/jev-1.13")
JD = os.environ.get("OPENROUTER_API_KEY", "")

TCG_SEARCH_BODY = {"algorithm":"sales_dismax","from":0,"size":24,
  "filters":{"term":{"productLineName":["pokemon"],"setName":["product"]},"range":{},"match":{}},
  "listingSearch":{"context":{"cart":{"packages":{}}},
    "filters":{"term":{"sellerStatus":"Live","channelId":0},"range":{"quantity":{"gte":1}},"exclude":{"channelExclusion":0}}},
  "context":{"cart":{"packages":{}},"shippingCountry":"MY","userProfile":{}},
  "settings":{"useFuzzySearch":True,"didYouMean":{}},"sort":{}}

# ---------------------------------------------------------------------------
# L2: TCGplayer market-price lookup (no key, no browser)
# ---------------------------------------------------------------------------
def tcg_search(q):
    h={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36',
       'Origin':'https://www.tcgplayer.com','Referer':'https://www.tcgplayer.com/pokemon',
       'Content-Type':'application/json','Accept':'application/json, text/plain, */*'}
    url=f'https://mp-search-api.tcgplayer.com/v1/search/request?q={urllib.parse.quote(q)}&isList=false'
    req=urllib.request.Request(url, data=json.dumps(TCG_SEARCH_BODY).encode(), headers=h, method='POST')
    with urllib.request.urlopen(req, timeout=25) as r:
        d=json.loads(r.read().decode())
    return d['results'][0]['results']

def tcg_candidates(query, n=6):
    """Market candidates for a card; returns [ {name, market, low, median, set, rarity, productId} ]."""
    out=[]
    try:
        for it in tcg_search(query)[:n]:
            if it.get('marketPrice') is None and it.get('lowestPrice') is None:
                continue
            out.append({'name':it.get('productName'),'set':it.get('setName'),
                        'rarity':it.get('rarityName'),'productId':int(it['productId']),
                        'market':it.get('marketPrice'),'low':it.get('lowestPrice'),
                        'median':it.get('medianPrice'),'listings':int(it.get('totalListings') or 0)})
    except Exception as e:
        out.append({'error': str(e)})
    return out

# ---------------------------------------------------------------------------
# L3: Jev decision gate
# ---------------------------------------------------------------------------
def jev_decide(listing, market_candidates):
    cand = [{'name':c.get('name'),'set':c.get('set'),'rarity':c.get('rarity'),
             'market_price_usd':c.get('market'),'lowest_price_usd':c.get('low'),
             'live_listings':c.get('listings')} for c in market_candidates]
    asking_jpy = listing.get('asking_jpy', 0)
    state = {
        "marketplace_listing": {**listing, "asking_price_usd_approx": round(asking_jpy/150, 2)},
        "task": "Decide if this marketplace listing is a steal worth alerting the user about.",
        "note": "market prices are USD; the listing asking was converted to USD at ~150 JPY/USD for comparison.",
        "tcgplayer_market_candidates": cand,
    }
    criteria = {
        "STEAL": "Listing is a real bargain: asking is meaningfully below the matching card's market price, or the ask is far below comparable listings. Alert the user.",
        "MARKET": "About fair market value. Do not alert.",
        "OVERPRICED": "Asking is above market. Do not alert.",
        "CANNOT_JUDGE": "No confident market match found (no market price, or candidate names don't match the listing).",
    }
    questions = {
        "verdict": {"type":"choice","criteria":criteria,
            "instructions":"Which verdict best fits this listing against the TCGplayer market evidence?"},
        "alert": {"type":"noul","instructions":"Should this listing be sent to the user (a clear steal, currently available, sold_out=false)?"},
        "match": {"type":"noul","instructions":"Does the first/strongest TCGplayer candidate closely match the listing's card (same card name + set context)?"},
    }
    body=json.dumps({"model":JEV_MODEL,"state":state,"questions":questions}).encode()
    req=urllib.request.Request(OPENROUTER, data=body,
        headers={"Authorization":f"Bearer {JD}","Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.loads(r.read().decode())

def decide(listing, market_candidates):
    a = jev_decide(listing, market_candidates)["answers"]
    v = a.get("verdict", {}).get("choice")
    alert = a.get("alert", {}).get("noul", 0.0)
    match = a.get("match", {}).get("noul", 0.0)
    return v, alert, match

# ---------------------------------------------------------------------------
# L1: sample marketplace listings (snkrdunk stand-in for mercari), PIKACHU set
# ---------------------------------------------------------------------------
def snkrdunk_pikachu_listings():
    """Real scraper result captured from snkrdunk.com ピカチュウ search (30th CELEBRATION set)."""
    # (listing name as shown on snkrdunk, asking JPY, sold_out)
    return [
        {"name":"ピカチュウex SAR [M6a 126/103] (30th CELEBRATION)","en_query":"Pikachu ex 126/103","asking_jpy":6800,"sold_out":False,"url":"https://snkrdunk.com"},
        {"name":"ピカチュウex SAR [M6a 127/103] (30th CELEBRATION)","en_query":"Pikachu ex 127/103","asking_jpy":8480,"sold_out":False,"url":"https://snkrdunk.com"},
        {"name":"ピカチュウ [M6a 136/103] (30th CELEBRATION)","en_query":"Pikachu 136/103","asking_jpy":3500,"sold_out":False,"url":"https://snkrdunk.com"},
    ]

# convert a JP card code like "126/103" / "M6a 126/103" into an en search query
def search_query_for(listing):
    """Build an ENGLISH TCGplayer search query from the JP listing (TCGplayer is en-only).

    The exact JP-carded-number -> EN set mapping is a real integration point; here we
    derive it from an optional 'en_hint' on the listing, else from the card code.
    """
    if listing.get("en_query"):
        return listing["en_query"]
    name = listing["name"]
    base = re.sub(r'\[.*?\]','', name).split('(')[0].strip()
    return base

def main():
    if not JD:
        print("OPENROUTER_API_KEY not set"); sys.exit(1)
    print("=== L1: snkrdunk Pikachu listings (mercari stand-in) ===")
    listings = snkrdunk_pikachu_listings()
    for l in listings:
        print(f"  {l['name']}  -> ¥{l['asking_jpy']:,}  sold_out={l['sold_out']}")

    print("\n=== L2: TCGplayer market lookup + L3: Jev verdict ===")
    heads = []
    for listing in listings:
        query = search_query_for(listing)
        cands = tcg_candidates(query)
        print(f"\nlisting: {listing['name']}")
        print(f"  search: \"{query}\" -> {len(cands)} candidates")
        for c in cands[:4]:
            print(f"    {c.get('name','')[:40]} | set={c.get('set','')[:26]} | mkt=${c.get('market')} low=${c.get('low')} listings={c.get('listings')}")
        verdict, alert, match = decide(listing, cands)
        print(f"  => Jev verdict: {verdict}  alert={alert:.2f}  match={match:.2f}")
        heads.append((listing, verdict, alert, match))

    print("\n=== ALERTS to send to user ===")
    sent = 0
    for listing, verdict, alert, match in heads:
        if verdict == "STEAL" and alert >= 0.5 and not listing["sold_out"]:
            sent += 1
            print(f"  [STEAL] {listing['name']}  ¥{listing['asking_jpy']:,}  {listing['url']}")
    if not sent:
        print("  (none flagged as steals)")

if __name__ == "__main__":
    main()