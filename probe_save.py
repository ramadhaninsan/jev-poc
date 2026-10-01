from playwright.sync_api import sync_playwright

captured = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36', locale='en-US')
    pg = ctx.new_page()
    def on_resp(resp):
        if 'mp-search-api' in resp.url and 'request' in resp.url and '.js' not in resp.url:
            try:
                body = resp.text()
                captured.append((resp.url, body))
            except Exception:
                pass
    pg.on('response', on_resp)
    pg.goto('https://www.tcgplayer.com/search/pokemon/product?q=pikachu', timeout=30000, wait_until='networkidle')
    pg.wait_for_timeout(4000)
    b.close()

for i, (url, body) in enumerate(captured):
    fn = f'/tmp/tcg_resp_{i}.json'
    open(fn, 'w').write(body)
    print('saved', fn, 'len', len(body), 'url:', url[:90])