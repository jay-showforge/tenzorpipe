#!/usr/bin/env python3
"""Additional paired sequential/concurrent runs and active/wait stage profiles."""
import argparse,hashlib,json,subprocess,sys
from benchmark_matrix import ROOT,OUT,measure
p=argparse.ArgumentParser();p.add_argument('--old-binary',required=True,type=__import__('pathlib').Path);args=p.parse_args()
old=args.old_binary.resolve();new=ROOT/'target/release/tenzor';results=[];expected={}
cases=[('old',330,[],old),('concurrent',330,[],new),('old',330,[],old),('concurrent',330,[],new),
       ('sequential-profile',330,['--execution','sequential','--profile'],new),
       ('concurrent-profile',330,['--profile'],new),('rendezvous',330,['--queue-mib','0','--profile'],new),
       ('old-memory',660,[],old),('resolution336',30,['--resolution','336','--profile'],new)]
for index,(kind,seconds,extra,binary) in enumerate(cases):
    name=f'v016-{kind}-{seconds}-{index}';out=OUT/f'{name}.tenzor'
    cmd=[binary,'-i',ROOT/'fixtures'/f'long-{seconds}.mp4','-o',out,'--batch-epochs','32',*extra]
    record=measure(name,cmd,seconds,out,32,engine_binary=binary)
    checked=json.loads(subprocess.check_output([sys.executable,ROOT/'scripts/hash_arrow.py',out]));assert checked['rows']==seconds*2
    record['logical_columns']=checked
    if kind!='resolution336':
        if seconds in expected:assert checked==expected[seconds],name
        else:expected[seconds]=checked
    text=(ROOT/'evidence'/f'benchmark-{name}.log').read_text()
    for line in text.splitlines():
        if line.startswith('TENZOR_PROFILE '):record['profile']=json.loads(line.removeprefix('TENZOR_PROFILE '))
    results.append(record);(ROOT/'evidence/concurrency-benchmarks.json').write_text(json.dumps(results,indent=2))
print('PASS additional benchmark exact-column checks')
