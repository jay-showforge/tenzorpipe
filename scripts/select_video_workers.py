#!/usr/bin/env python3
"""Smallest measured worker count within 5% of the best median; do not change CLI defaults."""
import json,pathlib,statistics
r=pathlib.Path(__file__).resolve().parents[1];xs=json.loads((r/'evidence/epyc-benchmarks-v020.json').read_text());med={}
for n in [1,2,3,4,6,8]:
 a=[x['wall_seconds'] for x in xs if x['variant']==f'N{n}' and 1<=x['repetition']<=5 and not x['profiled']];assert len(a)==5;med[n]=statistics.median(a)
best=min(med.values());pick=min(n for n,t in med.items() if t<=best*1.05)
(r/'evidence/worker-recommendation-v020.json').write_text(json.dumps(dict(rule='smallest worker count within 5% of best five-run median',medians=med,recommended=pick,cli_default=1),indent=2));print(pick)
