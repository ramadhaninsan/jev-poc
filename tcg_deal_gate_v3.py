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

STEAL_THRESHOLD = 0.70   # asking <= market * this  => STEAL  (owner "-30% for example")
JPY_PER_USD     = 150    # known approximation; live FX is future work (recorded debt)
POPULAR_MIN_LISTINGS = 100  # optional data-refresh popularity proxy, unused in v1

# ---------------------------------------------------------------------------
# HOT_SEED — fan-favorite pokemon (owner decision 2026-10-01), exploded into
# card-variant matchers. HOT <=> matched card is in here AND verdict == STEAL.
# Each entry matches against the TCGplayer candidate's fields (name/set/rarity).
# Built on affinity (OG 151 + legendaries + fan favorites), NOT market volume.
# ---------------------------------------------------------------------------
HOT_SEED = [
    # --- Pikachu line / Charizard line / starters (fan-favorite core) ---
    {"name": "Pikachu", "rarity": "Special Illustration Rare"},
    {"name": "Charizard", "rarity": "Special Illustration Rare"},
    {"name": "Charizard", "rarity": "Illustration Rare"},
    {"name": "Charizard", "set": "Base"},           # vintage base set 4/102
    {"name": "Charizard", "set": "151"},
    {"name": "Blastoise", "set": "Base"},
    {"name": "Venusaur", "set": "Base"},
    {"name": "Umbreon"},                             # Eevee line fan fave
    {"name": "Sylveon"},
    {"name": "Espeon"},
    # --- Legends / mythicals ---
    {"name": "Mewtwo", "set": "Base"},               # vintage
    {"name": "Mew"},
    {"name": "Lugia", "set": "Neo"},                 # vintage
    {"name": "Ho-Oh", "set": "Neo"},
    {"name": "Rayquaza"},
    {"name": "Garchomp"},
    {"name": "Giratina"},
    {"name": "Arceus"},
    # --- Fan favorites ---
    {"name": "Gengar", "set": "Fossil"},             # vintage
    {"name": "Gengar"},
    {"name": "Lucario"},
    {"name": "Greninja"},
    {"name": "Mimikyu"},
    {"name": "Gyarados", "set": "Base"},             # vintage
    {"name": "Dragonite"},
    {"name": "Snorlax"},
    {"name": "Lapras"},
]

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
    "SSR": "Special Illustration Rare", "SSP": "Special Illustration Rare",
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
        (r'ホウオウ', 'Ho-Oh'), (r'カイリュー', 'Dragonite'), (r'カイロス', 'Pinsir'),
        (r'ニョロモ', 'Poliwag'), (r'セレビィ', 'Celebi'), (r'せいなるはしら', ''),
    ]
    # JP set names -> EN (translates the query, not just the card name).
    JP_SET = [
        (r'スカーレット&バイオレット', 'Scarlet & Violet'), (r'スカーレット＆バイオレット', 'Scarlet & Violet'),
        (r'ブラック&ホワイト', 'Black & White'), (r'ソード&シールド', 'Sword & Shield'),
        (r'スーパーバーストデッキ', ''), (r'30th CELEBRATION', '151'), (r'30th', '151'),
        (r'メガ拡張パック', ''), (r'拡張パック', ''), (r'ハイクラスパック', ''),
        (r'ポケモンカードゲーム', ''), (r'ポケモンカード', ''), (r'プロモ', ''), (r'プロモカード', ''),
    ]
    for jp, en in EN_TABLE:
        en_name = re.sub(jp, en, en_name)
    for jp, en in JP_SET:
        en_name = re.sub(jp, en + ' ', en_name)
    en_name = re.sub(r'(ex|V|GX)\b', r' \1', en_name)          # Pikachuex -> Pikachu ex
    # drop any REMAINING CJK (untranslated set/flavor text) + card numbers from the query
    en_name = re.sub(r'[\u3040-\u30ff\u4e00-\u9fff\u3000-\u303f]+', ' ', en_name)
    en_name = re.sub(r'\b\d+\s*/\s*\d+\b', ' ', en_name)       # 331/190 -> drop
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
    out = []
    # products that are NOT a single card and must never be a match target
    NON_CARD_PRODUCT = ("tin", "mini tin", "booster", "bundle", "case", "etb",
                        "elite trainer", "collection", "figure", "misc", "sleeve",
                        "pokeball", "box", "5-pack", "pack", "display", "uprc",
                        "ultra premium", "starter deck", "theme deck", "deck")
    try:
        for it in tcg_search(query)[:n]:
            if it.get('marketPrice') is None and it.get('lowestPrice') is None:
                continue
            pn = (it.get('productName') or '').lower()
            if any(k in pn for k in NON_CARD_PRODUCT):
                continue  # tins/boxes/collections are never a single card
            out.append({'id': int(it['productId']), 'name': it.get('productName'), 'set': it.get('setName'),
                        'rarity': it.get('rarityName'), 'market': it.get('marketPrice'),
                        'low': it.get('lowestPrice'), 'listings': int(it.get('totalListings') or 0)})
    except Exception as e:
        out.append({'error': str(e)})
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
    want_edition = identity.get('edition', '').strip().lower()
    name = re.sub(r'\b(?:SAR|SR|SSR|SSP|UR|AR|RR)\b.*', '', identity.get('en_name', identity['name'])).strip()
    scored = []
    for c in valid:
        s = 0.0
        # edition is the precision gate: a SAR must match a SIR candidate, else heavily penalize
        if want_edition:
            c_ed = (c.get('rarity') or '').lower()
            ed_match = (('sar' in want_edition or 'sr' in want_edition or 'ssr' in want_edition or 'ssp' in want_edition)
                        and ('special illustration' in c_ed)) or \
                       (want_edition == 'cov' and 'cover' in c_ed)
            s += 3.0 if ed_match else -2.0
        s += _jaccard(name, c.get('name'))            # name similarity
        # candidate must at least mention the top card-name word
        head = name.split()[0][:3] if name.split() else ''
        if head and head not in c.get('name', '').lower():
            s -= 1.5
        scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return None
    top = scored[0]
    # Confidence bar depends on whether edition constrained the match: an edition-
    # gated candidate needs the old strict 2.0; a bare name match (no edition) still
    # needs a clear lead, but jaccard alone rarely reaches 2.0 -> lower to 0.6.
    need = 2.0 if want_edition else 0.6
    if top[0] < need:
        return None
    if len(scored) >= 2 and top[0] - scored[1][0] < 0.5:
        return None   # tied -> SKIP (do NOT guess)
    return top[1]

# ---------------------------------------------------------------------------
# Stage 2: DETERMINISTIC STEAL verdict + HOT + grade annotation (no Jev in the
# pure threshold — it is arithmetic). Grade is ANNOTATION, not a gate (D-COND-BAR).
# ---------------------------------------------------------------------------
def is_hot(matched):
    """Fan-favorite check: is the matched card one of the HOT_SEED variants?"""
    if not matched:
        return False
    name = (matched.get("name") or "").lower()
    rar = (matched.get("rarity") or "").lower()
    setn = (matched.get("set") or "").lower()
    for seed in HOT_SEED:
        if seed.get("name", "").lower() not in name:
            continue
        if seed.get("rarity") and seed["rarity"].lower() not in rar:
            continue
        if seed.get("set") and seed["set"].lower() not in setn:
            continue
        return True
    return False


def _jev_call(questions, state):
    body = json.dumps({"model": JEV_MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(OPENROUTER, data=body,
        headers={"Authorization": f"Bearer {JD}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["answers"]


def assess_verdict(asking_jpy, matched):
    """Return {verdict, pct_below} — pure function, no network, no Jev.

    verdict: STEAL | MARKET | OVERPRICED | SKIP(market missing).
    Uses marketPrice, falls back to lowestPrice; none => SKIP (no guess).
    PNG market is USD, asking is JPY -> convert with JPY_PER_USD.
    """
    market = matched.get("market") if matched.get("market") is not None else matched.get("low")
    if market is None or market <= 0:
        return {"verdict": "SKIP", "pct_below": None}
    asking_usd = asking_jpy / JPY_PER_USD
    pct_below = (market - asking_usd) / market
    if asking_usd <= market * STEAL_THRESHOLD:
        v = "STEAL"
    elif asking_usd <= market:
        v = "MARKET"
    else:
        v = "OVERPRICED"
    return {"verdict": v, "pct_below": pct_below, "asking_usd": asking_usd}


def _vision_condition(img_url):
    """Read a card photo -> text condition profile via a vision model.

    Tries deepseek-v4-flash-vision-exp over OpenRouter chat/completions. Returns
    a short text profile (or None on failure). Kept separate so a grade read
    failure NEVER drops a STEAL (grade is annotation only).
    """
    if not img_url or not JD:
        return None
    body = json.dumps({
        "model": "deepseek/deepseek-v4-flash-vision-exp",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": ("Describe the visible condition of this Pokemon card "
                                      "for grading: surface whitening, corner/edge wear, "
                                      "scratches, dents, centering, print/foil issues. "
                                      "One short sentence.")},
            {"type": "image_url", "image_url": {"url": img_url}},
        ]}],
    }).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {JD}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _jev_grade_question(profile):
    ans = _jev_call({
        "grade": {"type": "choice", "criteria": {
            "NM": "Near Mint: clean surface, sharp corners/edges, no whitening or wear",
            "LP": "Lightly Played: minor edge/surface wear, small whitening on corners",
            "PLAYED": "Played: visible wear, whitening/edge scuffs, light scratches",
            "HP": "Heavily Played: significant wear, creases/dings/scratches, faded",
        }, "instructions": "Pick the card's condition grade from the vision profile. Be strict."},
        "vintage": {"type": "noul", "instructions": "Is this an older/vintage-era card (>~15y)?"},
    }, {"card_condition_observed": profile})
    g = ans.get("grade", {}).get("choice", "NM")
    if g not in ("NM", "LP", "PLAYED", "HP"):
        g = "NM"
    return g, ans.get("vintage", {}).get("noul", 0.0) >= 0.5


def grade_card(img_url, profile=None):
    """grade = vision profile -> Jev condition class + vintage flag.

    Returns {grade, vintage} or {grade:"NM", vintage:False} default on failure
    (never raises; annotation only).
    """
    profile = profile or _vision_condition(img_url)
    if profile is None:
        return {"grade": "UNKNOWN", "vintage": False, "profile": None}
    try:
        g, v = _jev_grade_question(profile)
        return {"grade": g, "vintage": v, "profile": profile}
    except Exception:
        return {"grade": "UNKNOWN", "vintage": False, "profile": profile}


def assess(listing, matched, grade=None):
    """Full assessment for one listing. Returns the row dict.

    listing: {name|raw, asking_jpy, url, img_url}
    grade : optional {grade, vintage}; if None, skipped (no image path here).
    """
    row = {
        "card": (listing.get("name") or listing.get("raw") or "")[:60],
        "url": listing.get("url", ""),
        "asking_jpy": listing.get("asking_jpy"),
        "market": matched.get("market") if matched else None,
        "low": matched.get("low") if matched else None,
        "matched_name": (matched.get("name") if matched else None),
        "matched_set": (matched.get("set") if matched else None),
        "matched_rarity": (matched.get("rarity") if matched else None),
        "grade": grade.get("grade") if grade else None,
        "vintage": grade.get("vintage") if grade else False,
    }
    r = assess_verdict(listing.get("asking_jpy", 0) or 0, matched or {})
    row["verdict"] = r["verdict"]
    row["pct_below"] = r["pct_below"]
    hot = is_hot(matched)
    row["hot"] = hot
    if r["verdict"] == "STEAL" and hot:
        row["verdict"] = "HOT"
    return row

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

def process_listing(raw, asking_jpy, url="", img_url="", do_grade=False):
    """Run one listing through the pipeline. Returns a row dict or None (skip)."""
    idn = parse_identity(raw)
    if idn is None:
        return {"card": raw[:60], "url": url, "asking_jpy": asking_jpy,
                "verdict": "SKIP", "reason": "identity unsafe/uncertain"}
    q = f"{idn['en_name']} {idn['en_set']}".strip() or idn['en_name']
    cands = tcg_candidates(q)
    matched = match_card(idn, cands)
    if matched is None:
        return {"card": raw[:60], "url": url, "asking_jpy": asking_jpy,
                "verdict": "SKIP", "reason": "no confident TCGplayer match"}
    listing = {"name": raw, "edition": idn["edition"], "number": idn["number"],
               "asking_jpy": asking_jpy, "url": url, "img_url": img_url}
    grade = grade_card(img_url) if do_grade and img_url else None
    return assess(listing, matched, grade=grade)


def feed(listings, do_grade=True):
    """Iterate structured listings (from _mercari_listings) through the pipeline."""
    rows = []
    for L in listings:
        row = process_listing(L.get("raw", ""), L.get("asking_jpy", 0),
                              url=L.get("url", ""), img_url=L.get("img_url", ""),
                              do_grade=do_grade)
        rows.append(row)
    return rows


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", default="",
                    help="Mercari search URL -> browse with playwright, feed the "
                         "gate, print STEAL/HOT rows (grades annotations via vision+Jev).")
    ap.add_argument("--no-grade", action="store_true", help="skip the image grade call")
    args = ap.parse_args()

    if args.live:
        if not JD:
            print("OPENROUTER_API_KEY not set (needed for grade/Jev)"); sys.exit(1)
        from playwright.sync_api import sync_playwright
        from jev_agent import _mercari_listings  # structured extractor
        import time
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            ctx = b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36", locale="ja-JP")
            pg = ctx.new_page()
            pg.goto(args.live, timeout=30000, wait_until="domcontentloaded")
            deadline = 0
            while deadline < 20:
                if _mercari_listings(pg):
                    break
                pg.wait_for_timeout(1500); deadline += 1.5
            pg.wait_for_timeout(1500)
            listings = _mercari_listings(pg)
        print(f"=== live Mercari feed: {len(listings)} fixed-price listings ===")
        rows = feed(listings, do_grade=not args.no_grade)
        for r in rows:
            if r["verdict"] == "SKIP":
                print(f"  [SKIP] {r['card'][:44]!r} ({r.get('reason','')})")
                continue
            g = r.get("grade"); vintage = "v" if r.get("vintage") else ""
            print(f"  [{r['verdict']:<7}] {r['card'][:40]!r} asking ¥{r['asking_jpy']} "
                  f"mkt ${r['market']} ({100*(r['pct_below'] or 0):+.0f}%) "
                  f"grade={g}{vintage} hot={r['hot']} {r['url']}")
        print("\n=== STEAL/HOT summary ===")
        for r in [x for x in rows if x["verdict"] in ("STEAL", "HOT")]:
            print(f"  {r['verdict']}: {r['card'][:44]!r} asking ¥{r['asking_jpy']} "
                  f"mkt ${r['market']} grade={r.get('grade')} {r['url']}")
        return

    if not JD:
        print("OPENROUTER_API_KEY not set"); sys.exit(1)
    print("=== precision gate over Mercari-style listings (sample, no grade) ===")
    for raw, asking, sold in sample_listings():
        r = process_listing(raw, asking, do_grade=False)
        if r["verdict"] == "SKIP":
            print(f"  [SKIP] {raw[:48]!r:-<50} ({r.get('reason','')})")
            continue
        print(f"  [evaluated] {raw[:44]!r} asking ¥{r['asking_jpy']} "
              f"mkt ${r['market']} verdict={r['verdict']} hot={r['hot']}")

if __name__ == "__main__":
    main()