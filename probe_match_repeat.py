import sys, os, json
sys.path.insert(0, '/root/jev-poc')
from tcg_deal_gate_v2 import tcg_candidates, stage1_confirm_match, query_for
l = {"name":"ピカチュウex [SAR]","set":"30th CELEBRATION","edition":"SAR","number":"126/103","asking_jpy":6800,"sold_out":False,"url":"x"}
c = tcg_candidates(query_for(l), n=8)
res = {}
for i in range(4):
    m, _ = stage1_confirm_match(l, c)
    res[i+1] = m if m else 'NONE'
print(json.dumps(res, indent=1))