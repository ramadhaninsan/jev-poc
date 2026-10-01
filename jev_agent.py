#!/usr/bin/env python3
"""
Jev-decision-gate web scraping PoC.

Mirrors the architecture of shhivv/third-hand (JevClient.swift): a perceive ->
decide -> act loop where a structured-decision model (Jev) chooses the next
action from a shortlist of observed page elements and a history of prior
actions. Cheap enough to call once per action.

  perceive(page) -> elements[ {id,label,role,enabled} ]
  Jev /api/alpha/decisions -> {operation, target, done, absent}   (~$0.00002)
  execute(browser, decision)
  loop until done >= DONE_THRESHOLD or max steps.

Fetch backends (pluggable): playwright browser (for JS/SPA sites), or jina
reader markdown (for sites where the HTML surface is bot-walled but the
reader can render it). The Jev loop is identical for both -- it only consumes
the perceived element list.
"""
import json, os, sys, re, time, argparse



ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
API_KEY  = os.environ.get("OPENROUTER_API_KEY", "")
MODEL    = os.environ.get("JEV_MODEL", "typesafe/jev-1.13")
DONE_THRESHOLD   = 0.70
ABSENT_THRESHOLD = 0.50
MAX_STEPS = 20


# --------------------------------------------------------------------------
# Element model (mirrors AccessibilityElement in third-hand)
# --------------------------------------------------------------------------
class Element:
    def __init__(self, eid, label, role, enabled=True, value=None):
        self.eid = str(eid)
        self.label = label
        self.role = role
        self.enabled = bool(enabled)
        self.value = value

    def to_dict(self):
        d = {"id": self.eid, "label": self.label, "role": self.role, "enabled": self.enabled}
        if self.value: d["value"] = self.value
        return d


# --------------------------------------------------------------------------
# Jev decision client (OpenRouter /api/alpha/decisions)
# --------------------------------------------------------------------------
def jev_decide(task, elements, history, extra_ops=None):
    """Ask Jev for the next action. Returns dict with resolved decision."""
    # shortlist: keep at most 40 elements, prefer ones whose label overlaps the task
    task_words = {w for w in re.split(r"\W+", task.lower()) if len(w) > 2}
    def score(e):
        lab = set(re.split(r"\W+", e.label.lower()))
        return len(task_words & lab) * 10 + (5 if e.role in ("TextField", "SearchBox") else 0)
    shortlist = sorted(elements, key=score, reverse=True)[:40]

    shared = {
        "SCROLL_DOWN": "Reveal content below / load more",
        "SCROLL_UP": "Reveal content above",
        "WAIT": "Wait for content to load",
        "DONE": "All requirements are visibly satisfied on screen",
        "BLOCKED": "No available operation can make progress",
    }
    if extra_ops: shared.update(extra_ops)

    clickable = { e.eid: (e.label or e.role) + " [" + e.role + "]" for e in shortlist if e.enabled }
    questions = {
        "operation": {
            "type": "choice",
            "criteria": shared,
            "instructions": f"Which operation advances the goal '{task}' one step? DONE requires visible evidence.",
        },
        "done": {"type": "noul", "instructions": f"Has this task been completed: '{task}'? Judge only by what is visible on screen and actions already taken."},
        "absent": {"type": "noul", "instructions": f"Is the control needed for the next step of '{task}' missing from the elements on screen?"},
    }
    state = {
        "task": task,
        "step": len(history) + 1,
        "action_attempts": history[-8:] if history else ["nothing yet"],
        "elements": [e.to_dict() for e in shortlist],
    }

    # Always allow a click onto a visible element if any exist.
    if clickable:
        clickable["__none__"] = "None of these -- the needed control is not on screen"
        questions["click_target"] = {
            "type": "choice",
            "criteria": clickable,
            "instructions": f"Which element should be clicked / interacted with to advance '{task}'?",
        }

    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    import urllib.request, urllib.error
    req = urllib.request.Request(ENDPOINT, data=body,
                                 headers={"Authorization": f"Bearer {API_KEY}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Jev HTTP {e.code}: {e.read().decode()[:400]}")


# --------------------------------------------------------------------------
# Execution backends
# --------------------------------------------------------------------------
def _snkrdunk_perceive(page):
    body = page.inner_text("body")
    elements = []
    # Product detail page: surface the price strongly so Jev can call DONE.
    detail_price = re.search(r"[¥￥]\s*([\d,]+)", body)
    if "/products/" in page.url and detail_price:
        elements.append(Element("price", f"Product price is {detail_price.group(0)}", "PriceEvidence"))
        return elements
    for sel in ("input[type='search']", "input[name='keyword']", "input[placeholder*='検索']"):
        if page.query_selector(sel):
            elements.append(Element("searchbox", "search input", "TextField"))
            break
    for i, a in enumerate(page.query_selector_all("header a, nav a")[:12]):
        t = (a.inner_text() or "").strip()
        if t: elements.append(Element(f"nav{i}", t[:40], "Link"))
    # ranked product cards from the text stream (like third-hand OCR regions):
    # rows are "N | name | ..." with a price line.
    rank = re.compile(r"^(\d+)$")
    price = re.compile(r"[¥￥]\s*([\d,]+)")
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    i = 0
    while i < len(lines):
        if rank.match(lines[i]):
            # grab the following lines until a price, as the listing label
            j = i + 1
            chunk = []
            while j < len(lines) and not price.search(lines[j]) and j - i < 4:
                chunk.append(lines[j]); j += 1
            if price.search(lines[j]) if j < len(lines) else False:
                lbl = " / ".join(chunk) or lines[i]
                elements.append(Element(f"prod{len(elements)}", lbl[:90], "Listing"))
                i = j + 1
                continue
        i += 1
    return elements


def _snkrdunk_product_links(page):
    """Return (href) of the visible ranked product-detail anchors, in order."""
    hrefs = page.eval_on_selector_all(
        "a[href*='/products/']",
        "els => els.map(e => e.getAttribute('href'))",
    )
    # dedup preserving order
    seen = []
    for h in hrefs:
        h2 = h.split("?")[0]
        if h2 and h2 not in seen:
            seen.append(h2)
    return seen


def backend_playwright(search_url, perceive_fn, task):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36", locale="ja-JP")
        pg = ctx.new_page()
        pg.goto(search_url, timeout=30000, wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        history = []
        for step in range(MAX_STEPS):
            elements = perceive_fn(pg) or []
            js = jev_decide(task, elements, history,
                            extra_ops={"OPEN_LISTING": "Open the selected listing to see full details and price"})
            answers = js["answers"]
            op = answers["operation"]["choice"]
            done = answers.get("done", {}).get("noul", 0.0)
            absent = answers.get("absent", {}).get("noul", 0.0)
            print(f"[step {step+1}] Jev chose: {op}  done={done:.2f} absent={absent:.2f} "
                  f"probs={json.dumps(answers['operation'].get('probabilities',''), ensure_ascii=False)}")
            if done >= DONE_THRESHOLD or op == "DONE":
                # Guard against false completion: require real price evidence on a
                # product page (perceiver surfaced it) before honoring DONE.
                ev = next((e for e in elements if e.role == "PriceEvidence"), None)
                if ev and "price" in ev.eid:
                    print(f">>> DONE  price_evidence={ev.label}")
                else:
                    print(f">>> DONE(guarded out: no product price evidence, done={done:.2f})")
                    history.append("DONE: attempted but no price evidence"); continue
                break
            if op == "BLOCKED":
                print(">>> BLOCKED"); break
            if op in ("WAIT",):
                pg.wait_for_timeout(2500); history.append("WAIT: waited"); continue
            if op.startswith("SCROLL"):
                pg.mouse.wheel(0, 1200); pg.wait_for_timeout(1200)
                history.append(f"{op}: scrolled"); continue
            if op == "OPEN_LISTING":
                if "/products/" in pg.url:
                    history.append("OPEN_LISTING: already on a product detail page"); continue
                try:
                    links = _snkrdunk_product_links(pg)
                    if not links:
                        history.append("OPEN_LISTING: no product link found"); continue
                    pg.evaluate("(href)=>{const a=document.querySelector(`a[href='${href}']`);if(a)a.click()}", links[0])
                    pg.wait_for_timeout(3500)
                    history.append(f"OPEN_LISTING: opened {links[0]}")
                except Exception as ex:
                    history.append(f"OPEN_LISTING: failed {ex}")
            elif op == "CLICK" and answers.get("click_target"):
                pg.keyboard.press("Enter")  # placeholder; generalized click below
                raise RuntimeError("generalized click not wired in PoC")
            # safety
            if len(history) > 8: history = history[-8:]
        return pg


def backend_jina(search_url, perceive_from_md):
    # fallback for bot-walled HTML: render through r.jina.ai (needs key/grace)
    print("[backend] jina reader -> add JINA_API_KEY or use playwright for SPA")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://snkrdunk.com/search?keyword=%E3%83%AA%E3%82%B6%E3%83%BC%E3%83%89%E3%83%B3")
    ap.add_argument("--task", default="Find the cheapest pokemon card listing on this page and identify its price")
    ap.add_argument("--backend", default="playwright")
    args = ap.parse_args()
    if not API_KEY:
        print("OPENROUTER_API_KEY not set"); sys.exit(1)
    if args.backend == "playwright":
        backend_playwright(args.url, _snkrdunk_perceive, args.task)
    else:
        backend_jina(args.url, None)


if __name__ == "__main__":
    main()