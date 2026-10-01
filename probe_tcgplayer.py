from playwright.sync_api import sync_playwright
import json

reqs = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
        locale='en-US')
    pg = ctx.new_page()

    def on_response(resp):
        u = resp.url
        if any(k in u for k in ['search','price','catalog','api']) and 'static' not in u:
            reqs.append((resp.status, u[:160]))
    pg.on('response', on_response)

    try:
        pg.goto('https://www.tcgplayer.com/search/pokemon/product?q=pikachu', timeout=30000, wait_until='domcontentloaded')
        pg.wait_for_timeout(7000)
    except Exception as e:
        print('nav', type(e).__name__)
    print('=== interesting network calls ===')
    seen = set()
    for st, u in reqs:
        if u not in seen:
            seen.add(u)
            print(st, u)
    b.close()