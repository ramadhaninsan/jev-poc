import urllib.request, json

def raw(url):
    h={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36',
       'Referer':'https://www.tcgplayer.com/','Content-Type':'application/json'}
    req=urllib.request.Request(url,headers=h,data=b'{}',method='POST')
    return json.loads(urllib.request.urlopen(req,timeout=25).read())

variants = [
 'https://mp-search-api.tcgplayer.com/v1/search/request?q=pikachu&isList=false&from=0&size=20',
 'https://mp-search-api.tcgplayer.com/v1/search/product?q=pikachu&mpfev=5580',
 'https://mp-search-api.tcgplayer.com/v1/search/request?q=pikachu&isList=true',
 'https://mp-search-api.tcgplayer.com/v1/search/request?query=pikachu&page=1&size=20',
 'https://mp-search-api.tcgplayer.com/v1/catalog/search?q=pikachu&limit=20',
]
for url in variants:
    try:
        d = raw(url)
        g = d.get('results', d) if isinstance(d.get('results'), list) else {}
        items = 0
        if isinstance(d.get('results'), list) and d['results']:
            r = d['results'][0]
            items = len(r.get('results') or []) if isinstance(r.get('results'), list) else 0
        print(f'{url.split("tcgplayer.com")[-1][:60]:62s} -> items={items} keys={list(d.keys())[:5]}')
    except Exception as e:
        print(f'{url.split("tcgplayer.com")[-1][:60]:62s} -> ERR {type(e).__name__}')