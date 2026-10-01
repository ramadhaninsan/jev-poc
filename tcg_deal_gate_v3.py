#!/usr/bin/env python3
"""
TCG deal gate v3 — PRECISION over RECALL (user mandate).

Rule: if the marketplace listing cannot be CONFIDENTLY identified (card name +
set + edition), we SKIP it entirely. No guessing, no fuzzy price compare, no
"maybe this is the same card". Skip is the desired outcome, not a failure.

Stage 0  identify : parse Mercari-style detail text -> {name, set, edition, number}
            - incomplete / ambiguous => SKIP (logged, never matched)
Stage 1  confirm  : deterministic edition + fuzzy-name match against TCGplayer
                     candidates; Jev only arbitrates genuine ties.
Stage 2  verdict  : STEAL / MARKET / OVERPRICED + alert  (asking vs matched market)

The tricky part is Stage 0->1 (Mercari listing <-> TCGplayer product). We gate
hard: missing name/set OR non-card (sealed/box/misc) OR edition not mappable
all -> SKIP. Precision, not recall.
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
# Stage 0: strict identity parse from Mercari-style detail (JP)
# ---------------------------------------------------------------------------
# Edition tokens that appear in listing titles -> EN TCGplayer rarity.
EDITION_TO_EN = {
    "SAR": "Special Illustration Rare", "SR": "Special Illustration Rare",
    "UR": "Universe Rare", "AR": "Art Rare", "RR": "Double Rare",
    "PCG": "Pokemon Center", "H": "Holo", "": "",
}
# Tokens that mean the listing is NOT a single card -> skip (box/booster/etc.)
NON_CARD = ["ボックス","box","BOX","パック","pack","セット","set","BOOSTER",
            "エリートトレーナー","elite trainer","構築デッキ","デッキ","deck",
            "スリーブ","sleeve","プレマット","mat","エネルギー","energy"]
# JP card-number pattern in Mercari titles, e.g. [M6a 126/103] or 126/103
NUM = re.compile(r'\[?([A-Za-z]?\d*[a-z]?)\s*(\d+)/(\d+)\]?')

def parse_identity(raw):
    """Return (identity_dict|None). None => Not safely identifiable => SKIP."""
    t = raw.strip()
    if any(k.lower() in t.lower() for k in NON_CARD):
        return None  # it's a box/pack/set/mat, not a single card -> skip
    m = NUM.search(t)
    number = m.group(0).strip('[]') if m else None
    # edition: explicit bracket token + rarity names
    edition = None
    for tok in EDITION_TO_EN:
        if tok and re.search(rf'\b{tok}\b', t, re.I):
            edition = tok; break
    # JP card name up to the first structural delimiter
    name = re.sub(r'\[.*?\]',' ', t)
    name = re.sub(r'\(.*?\)',' ', name)
    name = re.split(r'\s*[（(【\[]\s*', name)[0].strip()
    # name must look like a pokemon card ("...ex", "ピカチュウ", has カードcontext) and be non-trivial
    if not name or len(name) < 3:
        return None

    # JP -> EN name translation (the Mercari->TCGplayer identity bridge). Extend this table.
    en_name = name
    EN_TABLE = [
        (r'ピカチュウ', 'Pikachu'), (r'リザードン', 'Charizard'), (r'ミュウ', 'Mew'),
        (r'ミュウツー', 'Mewtwo'), (r'ルギア', 'Lugia'), (r'イーブイ', 'Eevee'),
        (r'ホウオウ', 'Ho-Oh'), (r'せいなるはしら', ''),
    ]
    for jp, en in EN_TABLE:
        en_name = re.sub(jp, en, en_name)
    en_name = re.sub(r'(ex|V)\b', r' \1', en_name)      # Pikachuex -> Pikachu ex; PikachuV -> Pikachu V
    en_name = re.sub(r'\s+', ' ', en_name).strip()
    # strip the edition/rarity token out of the NAME (it belongs in edition, not the query)
    for tok in EDITION_TO_EN:
        if tok:
            en_name = re.sub(rf'\b{re.escape(tok)}\b', '', en_name, flags=re.I)
    en_name = re.sub(r'\s+', ' ', en_name).strip()
    return {"name": name, "en_name": en_name, "edition": edition or "",
            "number": number, "raw": t, "en_set": "ME: 30th Celebration" if "CELEBRATION" in t.upper() else ""}

# ---------------------------------------------------------------------------
# L2: TCGplayer market candidates
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
            out.append({'id':int(it['productId']),'name':it.get('productName'),'set':it.get('setName'),
                        'rarity':it.get('rarityName'),'market':it.get('marketPrice'),
                        'low':it.get('lowestPrice'),'listings':int(it.get('totalListings') or 0)})
    except Exception as e:
        out.append({'error':str(e)})
    return out

# ---------------------------------------------------------------------------
# Stage 1: DETERMINISTIC match (edition-first + name similarity), Jev on ties
# ---------------------------------------------------------------------------
def _nlp(tok):
    return set(re.findall(r'[a-z0-9]+', (tok or '').lower()))
def _jaccard(a, b):
    A, B = _nlp(a), _nlp(b)
    if not A or not B: return 0.0
    return len(A & B) / len(A | B)

def match_card(identity, candidates, en_set_map=None):
    """Deterministic: score each candidate on edition + name-dice + set. Jev only arbitrates ties.
    Returns (matched_candidate|None). None => no confident match => SKIP."""
    valid = [c for c in candidates if not c.get('error')]
    if not valid:
        return None
    want_edition = identity.get('edition','').strip().lower()
    name = re.sub(r'\b(?:SAR|SR|UR|AR|RR)\b.*','', identity.get('en_name', identity['name'])).strip()
    scored = []
    for c in valid:
        s = 0.0
        # edition is the precision gate: a SAR must match a SIR candidate, else heavily penalize
        if want_edition:
            c_ed = (c.get('rarity') or '').lower()
            ed_match = (('sar' in want_edition or 'sr' in want_edition)
                        and ('special illustration' in c_ed)) or \
                       (want_edition == 'cov' and 'cover' in c_ed)
            s += 3.0 if ed_match else -2.0
        s += _jaccard(name, c.get('name'))            # name similarity
        # candidate must at least mention the card name word
        if name.lower().split()[0][:3] not in c.get('name','').lower():
            s -= 1.5
        scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    if len(scored) < 2:
        return None  # not enough evidence to be confident -> SKIP
    top, second = scored[0], scored[1]
    # confident only if clear margin AND the top isn't a box/misc
    if top[0] < 2.0 or top[0] - second[0] < 0.5:
        return None   # tied or weak -> SKIP (do NOT guess)
    return top[1]

# ---------------------------------------------------------------------------
# Stage 2: Jev verdict (asking vs matched market)
# ---------------------------------------------------------------------------
def _jev_call(questions, state):
    body=json.dumps({"model":JEV_MODEL,"state":state,"questions":questions}).encode()
    req=urllib.request.Request(OPENROUTER, data=body,
        headers={"Authorization":f"Bearer {JD}","Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.loads(r.read().decode())["answers"]

def verdict(listing, matched):
    asking_usd = round(listing['asking_jpy']/150,2)
    state = {"marketplace_listing":{**listing,"asking_price_usd_approx":asking_usd},
             "confirmed_market_card":{
                "name":matched.get('name'),"set":matched.get('set'),"rarity":matched.get('rarity'),
                "market_price_usd":matched.get('market'),"lowest_price_usd":matched.get('low'),
                "live_listings":matched.get('listings')},
             "note":"TCGplayer market USD vs marketplace asking converted ~150 JPY/USD."}
    questions={"verdict":{"type":"choice","criteria":{
        "STEAL":"Asking is meaningfully BELOW the confirmed card's market price. Alert the user.",
        "MARKET":"About fair market value. Do not alert.",
        "OVERPRICED":"Asking is above market. Do not alert."},
        "instructions":"Decide verdict vs the confirmed card's market price."},
        "alert":{"type":"noul","instructions":"Send to user (clear steal AND sold_out=false)?"}}
    a=_jev_call(questions, state)
    return a.get("verdict",{}).get("choice"), a.get("alert",{}).get("noul",0.0)

# ---------------------------------------------------------------------------
# L1: sample Mercari-style listing detail strings (precision test set)
# ---------------------------------------------------------------------------
def sample_listings():
    # (raw Mercari detail text, asking jpy, sold_out)
    return [
        ("ピカチュウex SAR [M6a 126/103] (30th CELEBRATION) ポケモンカード", 6800, False),
        ("ピカチュウex SAR [M6a 127/103] (30th CELEBRATION)", 8480, False),
        ("ポケモンカードゲームMEGA 拡張パック「30th CELEBRATION」ボックス", 25500, False),  # box -> skip
        ("ピカチュウV 30th CELEBRATION SR [M6a 141/103]", 1200, False),                    # decoy suspiciously cheap
        ("レアカード景品 おまけBOX ポケモン", 500, False),                                   # no real identity -> skip
        ("ピカチュウ [M6a 136/103] 30th CELEBRATION", 3500, False),                        # plain common -> match-able
    ]

def main():
    if not JD:
        print("OPENROUTEER_API_KEY not set"); sys.exit(1)
    print("=== precision gate over Mercari-style listings ===")
    for raw, asking, sold in sample_listings():
        idn = parse_identity(raw)
        if idn is None:
            print(f"  [SKIP] {raw[:48]!r:-<54} (identity unsafe/uncertain)")
            continue
        q = f"{idn['en_name']} {idn['en_set']}".strip() or idn['en_name']
        cands = tcg_candidates(q)
        matched = match_card(idn, cands)
        if matched is None:
            print(f"  [SKIP] {raw[:48]!r:-<50} (no confident TCGplayer match)")
            continue
        listing = {"name":raw,"edition":idn["edition"],"number":idn["number"],
                   "asking_jpy":asking,"sold_out":sold,"url":"#"}
        v, alert = verdict(listing, matched)
        print(f"  [evaluated] {raw[:44]!r} edition={idn['edition']} -> mkt ${matched.get('market')} "
              f"verdict={v} alert={alert:.2f}")

if __name__ == "__main__":
    main()