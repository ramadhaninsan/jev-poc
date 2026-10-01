from playwright.sync_api import sync_playwright
import json

captured = {}
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36', locale='en-US')
    pg = ctx.new_page()
    def on_resp(resp):
        if 'search/request' in resp.url or 'search/product' in resp.url:
            try:
                captured[resp.url.split('?')[0]] = json.loads(resp.text() or '{}')
            except Exception:
                pass
    pg.on('response', on_resp)
    pg.goto('https://www.tcgplayer.com/search/pokemon/product?q=pikachu', timeout=30000, wait_until='networkidle')
    pg.wait_for_timeout(4000)
    b.close()

for url, d in captured.items():
    r = d.get('results', [{}])[0] if isinstance(d.get('results'), list) and d.get('results') else {}
    print('URL:', url)
    print('URL:', url)
    print('  keys:', list(d.keys()))
    items = r.get('results') if isinstance(r.get('results'), list) else []
    if items:
        print('  FIRST ITEM:')
        print(json.dumps(items[0], indent=1)[:1200])
    else:
        print('  r keys:', list(r.keys()))
        # maybe products under different key
        for k in r:
            v = r[k]
            if isinstance(v, list):
                print('   list key:', k, 'len', len(v))