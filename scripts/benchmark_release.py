#!/usr/bin/env python3
"""Same-host paired release benchmarks with bounded independent validation."""
import argparse,json,pathlib,platform,subprocess,sys,os
from benchmark_matrix import ROOT,OUT,measure
p=argparse.ArgumentParser();p.add_argument('--old-binary',required=True,type=pathlib.Path);p.add_argument('--v018-binary',type=pathlib.Path);a=p.parse_args()
old=a.old_binary.resolve();new=ROOT/'target/release/tenzor';v018=a.v018_binary.resolve() if a.v018_binary else None
cases=[]
for rep in range(3):
 group=[('v016',330,32,old,[])]
 if v018:group.append(('v018-N2',330,32,v018,['--video-workers','2']))
 group += [(f'N{n}',330,32,new,['--video-workers',str(n)]) for n in [1,2,3,4,8]]
 group.append(('baseline',330,32,None,[]))
 if rep==1:group.reverse()
 cases.extend(group)
for n in [1,2,4,8]:cases.append((f'N{n}-profile',330,32,new,['--video-workers',str(n),'--profile']))
cases += [('N2-memory',660,32,new,['--video-workers','2','--profile']),('N2-batch2',330,2,new,['--video-workers','2']),('N2-batch64',330,64,new,['--video-workers','2']),('video_only',330,32,new,['--video-workers','2','--profile']),('audio_only',330,32,new,['--profile']),('N2',30,32,new,['--video-workers','2']),('baseline',30,32,None,[])]
results=[];expected={}
for i,(kind,seconds,batch,binary,extra) in enumerate(cases):
 label='tenzor' if kind.startswith('N') else kind
 name=f'{label}-{seconds}-b{batch}-{kind}-r{i}'
 media=ROOT/'fixtures'/(f'long-330-{kind}.mp4' if kind in ['video_only','audio_only'] else f'long-{seconds}.mp4')
 out=OUT/f'{name}.tenzor'
 cmd=[binary,'-i',media,'-o',out,'--batch-epochs',str(batch),*extra] if binary else [sys.executable,ROOT/'reference/ffmpeg_arrow_baseline.py',media,out,'--batch-epochs',str(batch)]
 r=measure(name,cmd,seconds,out,batch,engine_binary=binary or new);r['variant']=kind
 if binary is None:r.pop('engine_binary_sha256',None)
 checked=json.loads(subprocess.check_output([sys.executable,ROOT/'scripts/hash_arrow.py',out]));assert checked['rows']==seconds*2;r['logical_columns']=checked
 if binary and kind not in ['audio_only','video_only']:
  if seconds in expected:assert checked==expected[seconds],name
  else:expected[seconds]=checked
 for line in (ROOT/'evidence'/f'benchmark-{name}.log').read_text().splitlines():
  if line.startswith('TENZOR_PROFILE '):r['profile']=json.loads(line.removeprefix('TENZOR_PROFILE '))
  if line.startswith('video_mode='):r['video_mode']=line
 results.append(r);(ROOT/'evidence/release-benchmarks.json').write_text(json.dumps(results,indent=2))
(ROOT/'evidence/machine-v019.json').write_text(json.dumps(dict(platform=platform.platform(),python=sys.version,cpu_count=os.cpu_count(),affinity=sorted(os.sched_getaffinity(0)),cpuinfo=next(x.split(':',1)[1].strip() for x in pathlib.Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name'))),indent=2))
print('PASS',len(results),'measured artifacts and engine equivalence')
