import urllib.request, json, urllib.parse
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
    url=f'https://mp-search-api.tcgplayer.com/v1/search/request?q={urllib.parse.quote(q)}&isList=false'
    req=urllib.request.Request(url, data=json.dumps(BODY).encode(), headers=h, method='POST')
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())
for q in ['Pikachu ex 30th Celebration','Pikachu 30th Celebration','Pikachu ex','Pikachu']:
    d=search(q); items=d['results'][0]['results']
    print(f'=== "{q}" ({len(items)}) ===')
    for it in items[:6]:
        print('  ',int(it['productId']),'|',(it['productName'] or '')[:38],'| mkt',it.get('marketPrice'),'|',(it.get('rarityName') or '')[:8],'|',(it.get('setName') or '')[:22])