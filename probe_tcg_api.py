import urllib.request, json

def get(url, extra=None):
    h = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
         'Referer':'https://www.tcgplayer.com/','Content-Type':'application/json'}
    if extra: h.update(extra)
    data = b'{}'  # the page posts an empty JSON body
    req = urllib.request.Request(url, headers=h, data=data, method='POST')
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status, r.read().decode()

st, body = get('https://mp-search-api.tcgplayer.com/v1/search/request?q=pikachu&isList=false')
print('search status:', st, 'len:', len(body))
d = json.loads(body)
results = d.get('results', [])
print('result groups:', len(results))
for g in results[:5]:
    print(' ', g.get('headings', {}).get('name', g.get('name','?')))
    items = g.get('results', [])
    print('   items:', len(items))
    for it in items[:3]:
        pid = it.get('productId'); name = it.get('productName'); mp = it.get('marketPrice'); lm = it.get('lowestPrice')
        print(f'     id={pid} {name[:45]} market={mp} low={lm} ext={it.get("extendedData","")[:0]}')
    # print one full item keys
    if items:
        print('   KEYS:', list(items[0].keys()))