from playwright.sync_api import sync_playwright
import re
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36', locale='en-US')
    pg = ctx.new_page()
    try:
        pg.goto('https://www.pricecharting.com/search-products?q=charizard+151', timeout=25000, wait_until='domcontentloaded')
        pg.wait_for_timeout(4000)
        print('TITLE:', pg.title()[:60]); print('URL:', pg.url[:60])
        body = pg.inner_text('body')
        lines = body.split('\n')
        for l in lines:
            if re.search(r'charizard', l, re.I) or re.search(r'\$\s?\d', l):
                print(' >', l.strip()[:110])
        # dump first 800 chars to see structure
        print('---BODY HEAD---')
        print(' |'.join(x.strip() for x in lines[:25] if x.strip()))
    except Exception as e:
        print('err', type(e).__name__)
    b.close()