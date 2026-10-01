import urllib.request, json
from tcg_api import search

d = search('pikachu')
group = d['results'][0]
print('group keys:', list(group.keys()))
print('totalResults:', group.get('totalResults'))
print('results key value type:', type(group.get('results')))
r = group.get('results')
# walk deeper paths
def walk(o, path, depth=0):
    if depth>3: return
    if isinstance(o, dict):
        for k,v in o.items():
            if k in ('results','products','items') and isinstance(v,list):
                print(f'  found list at {path}/{k} len={len(v)}')
                if v and isinstance(v[0],dict):
                    print('    item keys:', list(v[0].keys())[:12])
            walk(v, f'{path}/{k}', depth+1)
    elif isinstance(o,list):
        for i,v in enumerate(o[:2]):
            walk(v, f'{path}[{i}]', depth+1)
walk(group, 'group')
open('/tmp/tcg_raw.json','w').write(json.dumps(d,ensure_ascii=False))