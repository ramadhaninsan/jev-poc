# Jev-decision-gate web scraper (PoC)

A workable PoC of the "Jev as the decision model in a scraping loop" pattern,
mirroring `shhivv/third-hand` (JevClient.swift) — perceive -> decide -> act,
with a cheap structured-decision model choosing every next action.

## How it works

```
perceive(page) -> [Element{id,label,role,enabled}]
        |
Jev /api/alpha/decisions  (model typesafe/jev-1.13, ~$0.00002/call)
        |  -> {operation, done:noul, absent:noul, probs:{op:0..1}}
        v
execute(browser)  ->  click / scroll / wait / open listing
        |
   history[-8:] fed back as action_attempts
        |
   loop until done >= 0.70  (or op == DONE with evidence)
```

This is the key insight from third-hand: **Jev is the planner, not the parser.**
It only ever picks an action + target from a shortlist of *already-perceived*
elements; it never sees HTML. The LLM/browser does the free-form work
(rendering, reading text), and Jev supplies the cheap, typed, probabilistic
decision at each step. That keeps per-decision cost ~nothing and lets a
full LLM stay out of the hot loop.

## The decision protocol (OpenRouter, not native TypeSafe)

`typesafe/jev-1.13` / `~typesafe/jev-latest` REJECT the chat/completions
endpoint ("is a decisions model"). You must use:

```
POST https://openrouter.ai/api/alpha/decisions
{
  "model": "typesafe/jev-1.13",
  "state": { "task", "step", "action_attempts": [...], "elements": [...] },
  "questions": {
    "operation": { "type":"choice", "criteria": {OPERATION: description},
                    "instructions": "Which operation advances the goal?" },
    "done":  { "type":"noul", "instructions": "Is the task done?" },
    "absent":{ "type":"noul", "instructions": "Is the needed control missing?" }
  }
}
```

Response: `{"answers":{"operation":{"choice":"OP","probabilities":{...},"confidence":..},
  "done":{"noul":..},"absent":{"noul":..}}}`. `noul` = 0..1 number.
Your existing `~/.hermes/.env` `OPENROUTER_API_KEY` works.

## Real-world run (snkrdunk, reachable from this box)

`bash run.sh` opens the snkrdunk リザードン search, Jev repeatedly picks
`SCROLL_DOWN` (absent high -> listings not yet perceived), then `OPEN_LISTING`,
then `DONE`. Verified: it navigates to a real product page with a price.

Honest gaps found in testing:
- If the perceiver doesn't surface listings, Jev loops `SCROLL_DOWN` forever.
  Garbage-in = degenerate action. The perceiver is the part that matters.
- The executor opened `links[0]` (first DOM anchor) rather than Jev's chosen
  target, so it opened a mingled Nike sneaker, not a card. Real build must
  carry the element id through to execution (third-hand validates the chosen
  target is a real enabled element before acting).
- `DONE` was fired at low confidence (0.37) until a guard was added requiring
  a perceived price-evidence element. Keep a false-completion guard.

## Mercari.jp — the actual blocker, not the pattern

Mercari is **fully bot-walled from this box** on every route:
- direct curl            -> HTTP 403
- r.jina.ai reader       -> Cloudflare "Just a moment..." (with & without key)
- headless Chromium      -> "しばらくお待ちください..." challenge, never auto-solves
- mobile UA + webview    -> same challenge
- mercari JSON API       -> 404 NotFoundException
- wayback                -> only 2020-2021 archived search SPAs (no live data)

So scraping Mercari is *not* a Jev problem — it's an access problem. The Jev
decision loop is identical regardless of source. To get Mercari data you need
one of:
1. A residential/rotating proxy IP that Mercari's Cloudflare trusts (this
   datacenter IP is flagged), or
2. A real headed browser on a machine matching your IP (what third-hand does
   on macOS via Accessibility — it talks to the *user's real browser*), or
3. Mercari's official API / a 3rd-party Mercari data API with credentials.

The Jev-decision-gate code here ports unchanged to any of those once access
exists — only the fetch backend changes.

## Files
- `jev_agent.py` — the loop + Jev client + playwright backend + snkrdunk perceiver
- `run.sh` — sources `~/.hermes/.env`, runs the agent

---

# TCG deal gate (v2) — the "is it a STEAL?" layer

The above is the *browse* loop. The deal gate answers a different question:
**is this listing cheap relative to the same card's market price?** It's the
second-generation pipeline, in `tcg_deal_gate_v2.py`.

## Why match-first (the design fix this proves)

"Cheaper than X" is only meaningful if X is the *same card*. The trap:
**JP card numbers are NOT EN TCGplayer numbers.** A JP `M6a 126/103` is a
different product than any EN `/128` card. Searching TCGplayer by a JP number
returns garbage (live: "Pikachu 136/103" -> Nemona + SDCC-2005 Pikachu). So:

- **Never** key the match on a raw card number.
- **Do** key it on what the marketplace declares: **name + set + edition/rarity**
  (Mercari listings carry this detail — use it as the identity source of truth).

## Two-stage Jev decision

```
L1 scrape : listing -> {name, set, edition, asking, sold_out, url}
L2 lookup : TCGplayer market candidates   (mp-search-api, NO key / NO browser)
L3 Jev :
   STAGE 1  confirm the match  -> which candidate is the SAME card (name+set+edition)?
            none -> CANNOT_JUDGE, STOP (never judge price on an unmatched card)
   STAGE 2  verdict            -> STEAL/MARKET/OVERPRICED (asking vs matched market)
            + alert noul (alert only if STEAL AND sold_out=false)
```

Stage 1 maps JP edition -> EN rarity (SAR/SR -> Special Illustration Rare)
so a SAR is judged against the SAR, never the common double-rare.

## Verified live run (snkrdunk Pikachu as Mercari stand-in)

| Listing | Asking | Match | Verdict | Alert |
|---|---|---|---|---|
| ピカチュウex SAR 126/103 | ¥6,800 | SIR Pikachu ex $73–93 | STEAL | 0.60 |
| ピカチュウex SAR 127/103 | ¥8,480 | SIR Pikachu ex $73–93 | STEAL | 0.57 |
| ピカチュウ 136/103 (normal) | ¥3,500 | — (no match) | CANNOT_JUDGE | — |
| ピカチュウV SR 141/103 decoy ¥1,200 | ¥1,200 | — (no match) | CANNOT_JUDGE | — |

The last two are the false-steal traps — Jev refused to match them (no alert)
even though they look "cheap", because match is keyed on identity, not price.

## Key facts learned (v2)

- **TCGplayer market price API**: `POST mp-search-api.tcgplayer.com/v1/search/request?q=<q>`.
  Body MUST include `filters`/`listingSearch`/`context` — an empty `{}` returns 0
  items. Returns `marketPrice`, `lowestPrice`, `medianPrice`, `totalListings`.
  **No API key, no browser.** (The site HTML is a bot-walled SPA; this JSON
  endpoint answers cleanly.)
- Put the **EN set name** in the query (`Pikachu ex ME: 30th Celebration`) to
  surface the exact set; searching by card + JP set string returns box noise.
- OpenRouter decision endpoint: `POST openrouter.ai/api/alpha/decisions`,
  `model: typesafe/jev-1.13`. Jev is a "decisions model" and REJECTS
  chat/completions. Returns per-choice probabilities + `noul` (0..1). ~$0.00002/call.
- JPY->USD ~150 ¥/$ is a rough converter for the comparison; a real build should
  fetch the live rate per run.

---

# TCG deal gate v3 — PRECISION over RECALL (the current design)

`tcg_deal_gate_v3.py` is the precision-first production shape. User rule: **if a
listing can't be confidently identified, we skip it entirely** — no guessing, no
fuzzy price compare on an uncertain identity. Skip is the desired outcome.

## The four gates (all fail-closed -> SKIP)

1. **Stage 0 identity parse** — extract {name, set, edition, number} from the
   Mercari-style detail text. Gates: missing/trivial name, or a NON-CARD
   (ボックス/pack/BOX/box/セット/スリーブ/エナジー…) -> SKIP. The Mercari
   listing's own detail is the source of truth for identity.
2. **JP->EN translation** — the tricky bridge. `ピカチュウ`->Pikachu,
   `ex` spacing (`Pikachuex`->`Pikachu ex`), edition token stripped from the
   query so it never pollutes TCGplayer search with "SAR" (which returns
   collection boxes instead of the card).
3. **Stage 1 deterministic match** — edition-first (+3 exact-SIR, -2 wrong
   edition) + name jaccard, minus a penalty if the candidate lacks the card
   keyword. Only confident when the top candidate clears 2.0 AND beats the
   #2 by >=0.5. **Ties -> SKIP** (this is where precision bites: it refuses
   to guess).
4. **Stage 2 Jev verdict** — only after a confirmed card; STEAL/MARKET/OVERPRICED
   + alert noul by comparing asking (converted ~150¥/$) to the matched market.

## Honest behavior seen in testing
- The valid SAR listings (126/103, 127/103) DO resolve to the two SIR Pikachu ex
  cards ($73.62 / $93.67). But on the same edition, both SIR candidates score
  identically (3.5) -> margin 0.0 -> **the gate correctly SKIPs rather than guess
  between 149/128 and 150/128.** Both are ~$75-95 anyway, so either verdict would
  be STEAL, but the gate won't manufacture a choice it can't justify. That is
  precision working, not a bug.
- Boxes, no-identity listings, and the ¥1,200 "ピカチュウV" decoy all SKIP with
  no false steal.
- Deterministic matcher means **no LLM call, no nondeterminism** in the match
  step (the v2 problem). Jev only appears at the verdict, where a judgment is
  genuinely required.

## Next step to remove the SAR tie (if wanted)
On same-name + same-edition candidates, use the listing's card `number`
(126/103 etc.) to disambiguate, OR accept any candidate within the same edition
when their market prices are within a small band (they're interchangeable for
the STEAL decision). Until one of those is wired, the gate prefers to skip —
which stays true to precision-not-recall.