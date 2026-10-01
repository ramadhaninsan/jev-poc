from playwright.sync_api import sync_playwright
import json, re

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36', locale='ja-JP')
    pg = ctx.new_page()
    pg.goto('https://snkrdunk.com/search?keyword=%E3%83%94%E3%82%AB%E3%83%81%E3%83%A5%E3%82%A6', timeout=25000, wait_until='domcontentloaded')
    pg.wait_for_timeout(5000)
    body = pg.inner_text('body')
    lines = [l.strip() for l in body.split('\n') if l.strip()]
    # print first 60 lines to see layout
    for l in lines[:60]:
        print(' |', l[:70])
    open('/tmp/snk_pika_body.txt','w').write(body)