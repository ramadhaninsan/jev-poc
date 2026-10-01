from playwright.sync_api import sync_playwright
import json

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36', locale='en-US')
    pg = ctx.new_page()
    info = {}
    def on_req(req):
        if 'mp-search-api' in req.url and 'request' in req.url:
            h = req.all_headers()
            info['headers'] = {k:v for k,v in h.items() if k.lower() not in ('cookie',)}
            info['cookie'] = h.get('cookie','')[:300]
            info['method'] = req.method
            info['post_data'] = req.post_data
    pg.on('request', on_req)
    pg.goto('https://www.tcgplayer.com/search/pokemon/product?q=pikachu', timeout=30000, wait_until='networkidle')
    pg.wait_for_timeout(3000)
    b.close()
print('method:', info.get('method'))
print('post_data:', info.get('post_data'))
for k,v in info.get('headers',{}).items():
    print(f'  {k}: {v[:90]}')
print('cookie(first 300):', info.get('cookie'))
open('/tmp/tcg_req_headers.json','w').write(json.dumps({'headers':info.get('headers'),'cookie':info.get('cookie'),'post_data':info.get('post_data')}))