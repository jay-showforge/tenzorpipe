#!/usr/bin/env python3
"""Serial alternating old/new benchmark with full logical-column identity checks."""
import argparse,hashlib,json,pathlib,re,subprocess,sys
from benchmark_matrix import ROOT, OUT, measure
p=argparse.ArgumentParser();p.add_argument('--old-binary',required=True,type=pathlib.Path);a=p.parse_args()
records=[];expected=None
for i,label in enumerate(['old','new','old','new']):
    binary=a.old_binary.resolve() if label=='old' else ROOT/'target/release/tenzor'
    name=f'epoch-patch-{label}-330-r{i}';out=OUT/f'{name}.tenzor'
    rec=measure(name,[binary,'-i',ROOT/'fixtures/long-330.mp4','-o',out,'--batch-epochs','32'],330,out,32,engine_binary=binary)
    rec['engine_binary_sha256']=hashlib.sha256(binary.read_bytes()).hexdigest()
    verified=json.loads(subprocess.check_output([sys.executable,ROOT/'scripts/hash_arrow.py',out]))
    actual=verified['columns'];rows=verified['rows']
    if expected is None:expected=actual
    assert actual==expected and rows==660
    rec['column_sha256']=actual;rec['all_columns_bit_identical']=True
    if label=='new':
        log=(ROOT/'evidence'/f"benchmark-{rec['name']}.log").read_text()
        match=re.search(r'decoded (\d+) access units, resized (\d+) selected pictures',log)
        rec['decoded'],rec['resized']=map(int,match.groups())
        assert (rec['decoded'],rec['resized'])==(9900,660)
    records.append(rec)
    (ROOT/'evidence/epoch-patch-benchmarks.json').write_text(json.dumps(records,indent=2))
    out.unlink()
print('PASS alternating benchmark artifact equality')
