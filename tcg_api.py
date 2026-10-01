import urllib.request, json

BODY = {"algorithm":"sales_dismax","from":0,"size":24,
  "filters":{"term":{"productLineName":["pokemon"],"setName":["product"]},"range":{},"match":{}},
  "listingSearch":{"context":{"cart":{"packages":{}}},
    "filters":{"term":{"sellerStatus":"Live","channelId":0},"range":{"quantity":{"gte":1}},"exclude":{"channelExclusion":0}}},
  "context":{"cart":{"packages":{}},"shippingCountry":"MY","userProfile":{}},
  "settings":{"useFuzzySearch":True,"didYouMean":{}},"sort":{}}

def search(q):
    h={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36',
       'Origin':'https://www.tcgplayer.com','Referer':'https://www.tcgplayer.com/pokemon',
       'Content-Type':'application/json','Accept':'application/json, text/plain, */*'}
    url=f'https://mp-search-api.tcgplayer.com/v1/search/request?q={q}&isList=false'
    req=urllib.request.Request(url, data=json.dumps(BODY).encode(), headers=h, method='POST')
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())

d = search('pikachu')
items = d['results'][0]['results']
print('items:', len(items), '| total:', d['results'][0]['totalResults'])
for it in items[:10]:
    print(' ', int(it['productId']), '|', (it['productName'] or '')[:34], '| mkt', it.get('marketPrice'),
          '| low', it.get('lowestPrice'), '| median', it.get('medianPrice'),
          '| listings', it.get('totalListings'), '|', (it.get('setName') or '')[:24])
# filter to real pikachu charizard-ish cards only (exclude sealed/etc)
print('--- cards only (rarity present) ---')
cards = [it for it in items if it.get('rarityName')]
print('root card count:', len(cards))
open('/tmp/tcg_pika_clean.json','w').write(json.dumps(items, ensure_ascii=False))