#!/usr/bin/env python3
"""
TCG deal gate PoC v2: is a marketplace listing a STEAL?

Two-stage Jev decision, match-first (user correction):
  Stage 1 — Jev CONFIRMS which TCGplayer candidate is genuinely the SAME card
            (name + set + edition/rarity). If none match -> CANNOT_JUDGE, stop.
  Stage 2 — only after a confirmed match, Jev judges STEAL/MARKET/OVERPRICED by
            comparing asking vs the matched market price.

Key correctness rule: the match is keyed on the identity the MARKETPLACE declares
(set + edition/rarity), NOT a guessed card number. JP card numbers (/103) are
distinct products from EN TCGplayer numbers (/128) — matching on a raw number
yields nonsense (seen live: "Pikachu 136/103" returned Nemona + SDCC 2005 Pikachu).

Layers:
  L1 scrape : marketplace listing  -> {name, set, edition, asking, sold_out, url}
  L2 lookup : TCGplayer market candidates for the card   (mp-search-api, no key/browser)
  L3 Jev    : stage1 = confirm match, stage2 = steal/market/overpriced + alert?
"""
import re, json, os, sys, urllib.request, urllib.parse

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
# L2: TCGplayer market candidates (no key, no browser)
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

def tcg_candidates(query, n=8):
    out=[]
    try:
        for it in tcg_search(query)[:n]:
            if it.get('marketPrice') is None and it.get('lowestPrice') is None:
                continue
            out.append({'id': int(it['productId']), 'name':it.get('productName'), 'set':it.get('setName'),
                        'rarity':it.get('rarityName'), 'market':it.get('marketPrice'),
                        'low':it.get('lowestPrice'), 'listings':int(it.get('totalListings') or 0)})
    except Exception as e:
        out.append({'error': str(e)})
    return out

# ---------------------------------------------------------------------------
# L3: Jev decision client
# ---------------------------------------------------------------------------
def _jev_call(questions, state):
    body=json.dumps({"model":JEV_MODEL,"state":state,"questions":questions}).encode()
    req=urllib.request.Request(OPENROUTER, data=body,
        headers={"Authorization":f"Bearer {JD}","Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.loads(r.read().decode())["answers"]

def stage1_confirm_match(listing, candidates, retries=2):
    """Jev picks WHICH candidate (if any) is the same card as the listing. Returns (id_or_None, conf).

    Retries on a __none__/invalid answer: Jev is probabilistic, and a wrong NONE here
    silently drops a real steal. 2 cheap retries make the confirmed-match near deterministic.
    """
    cand = [
        f'{c["id"]}: {c.get("name")} [{c.get("set")}][{c.get("rarity")}] mkt=${c.get("market")}'
        for c in candidates if not c.get('error')
    ]
    if not cand:
        return None, 0.0
    criteria = {}
    for c in cand: criteria[c.split(":")[0]] = c
    criteria["__none__"] = "None of these is the same card as the listing"
    state = {"task":"Identify the exact reference card for a marketplace listing.",
             "marketplace_listing":listing, "tcgplayer_candidates":cand}

    for attempt in range(max(1, retries + 1)):
        questions = {
            "selected": {"type":"choice","criteria":criteria,
                "instructions":"Which TCGplayer candidate is the SAME card as the marketplace listing? "
                + f"Listing: {listing.get('name')} | set:{listing.get('set')} | edition:{listing.get('edition')}. "
                + "Edition mapping: SAR/SR = Special Illustration Rare, Normal = common, ex = Double Rare / ex. "
                + "Match on BOTH the set AND the exact card name AND the edition/rarity. A special-illustration "
                + "SAR is NOT the same card as the common double-rare version — they are different products. "
                + "If no candidate shares the set + exact card + edition, choose __none__. "
                + (f"This is attempt {attempt+1} of {retries+1}; be precise, prefer a real match over none."
                   if retries else ""),
            },
        }
        a = _jev_call(questions, state)
        choice = a.get("selected", {}).get("choice")
        if choice not in (None, "__none__"):
            try:
                return int(choice), 1.0
            except (TypeError, ValueError):
                pass
    return None, 1.0

def stage2_verdict(listing, matched, candidates):
    ref = next((c for c in candidates if c.get('id') == matched), None)
    asking_usd = round(listing['asking_jpy']/150, 2)
    state = {
        "marketplace_listing": {**listing, "asking_price_usd_approx": asking_usd},
        "confirmed_market_card": ref,
        "note": "TCGplayer market USD vs marketplace asking converted at ~150 JPY/USD.",
    }
    questions = {
        "verdict": {"type":"choice","criteria":{
            "STEAL":"Asking is meaningfully BELOW the confirmed card's market price. Alert the user.",
            "MARKET":"About fair market value. Do not alert.",
            "OVERPRICED":"Asking is above market. Do not alert.",
        },"instructions":"Decide the verdict for this listing against the confirmed card's market price."},
        "alert":{"type":"noul","instructions":"Should this be sent to the user (clear steal AND sold_out=false)?"},
    }
    a = _jev_call(questions, state)
    return a.get("verdict", {}).get("choice"), a.get("alert", {}).get("noul", 0.0)

# ---------------------------------------------------------------------------
# L1: marketplace listings — identity declared BY the marketplace (mercari-style)
# ---------------------------------------------------------------------------
def sample_listings():
    return [
        {"name":"ピカチュウex [SAR]","set":"30th CELEBRATION","edition":"SAR","number":"126/103","asking_jpy":6800,"sold_out":False,"url":"https://snkrdunk.com"},
        {"name":"ピカチュウex [SAR]","set":"30th CELEBRATION","edition":"SAR","number":"127/103","asking_jpy":8480,"sold_out":False,"url":"https://snkrdunk.com"},
        {"name":"ピカチュウ","set":"30th CELEBRATION","edition":"Normal","number":"136/103","asking_jpy":3500,"sold_out":False,"url":"https://snkrdunk.com"},
        # decoy: plausible-looking but a different card -> must NOT secretly match
        {"name":"ピカチュウV","set":"30th CELEBRATION","edition":"SR","number":"141/103","asking_jpy":1200,"sold_out":False,"url":"https://snkrdunk.com"},
    ]

# JP set/edition -> EN TCGplayer set/rarity. This is the real integration table:
# Mercari declares the JP set + edition; TCGplayer matches on the EN names.
EN_SET = {"30th CELEBRATION":"ME: 30th Celebration"}
EN_RARITY = {"SAR":"Special Illustration Rare","SR":"Special Illustration Rare",
             "Normal":"","N":"","V":"V","ex":"ex"}

def query_for(listing, en_query=None):
    if en_query:
        return en_query
    n = re.sub(r'\[.*?\]','', listing["name"]).strip()
    if 'ピカチュウ' in n: n = n.replace('ピカチュウ','Pikachu')
    n = n.replace('ex',' ex').replace('V',' V').strip()
    n = re.sub(r'\s+',' ', n)
    en_set = EN_SET.get(listing.get("set",""), listing.get("set",""))
    # put the set first so the edition/rarity isn't drowned out
    return f"{n} {en_set}"

def main():
    if not JD:
        print("OPENROUTER_API_KEY not set"); sys.exit(1)
    print("=== L1: sample marketplace listings (mercari-style identity) ===")
    for l in sample_listings():
        print(f"  {l['name']} {l['edition']} {l['number']} [{l['set']}] -> ¥{l['asking_jpy']:,} sold={l['sold_out']}")

    print("\n=== L2 lookup + L3 Jev (match-first, then verdict) ===")
    for listing in sample_listings():
        q = query_for(listing)
        cands = tcg_candidates(q)
        print(f"\nlisting: {listing['name']} {listing['edition']} {listing['number']} [{listing['set']}]")
        print(f"  search: \"{q}\" -> {len([c for c in cands if not c.get('error')])} price candidates")
        matched, _ = stage1_confirm_match(listing, cands)
        if matched is None:
            print("  STAGE1: Jev found NO matching card -> CANNOT_JUDGE (correctly skips, no false steal)")
            continue
        print(f"  STAGE1: Jev confirmed match -> TCG product {matched}")
        verdict, alert = stage2_verdict(listing, matched, cands)
        print(f"  STAGE2: verdict={verdict}  alert={alert:.2f}")

if __name__ == "__main__":
    main()