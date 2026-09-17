#!/usr/bin/env python3
"""Five paired unprofiled runs per variant; profile separately; verify every output."""
import hashlib,json,os,pathlib,platform,statistics,subprocess,sys
from benchmark_matrix import ROOT,OUT,measure
OLD=ROOT/'reference/bin/tenzor-v0.1.9-linux-x86_64';NEW=ROOT/'target/release/tenzor';rows=[];expected={};retained=[]
group=[('old-N2',OLD,'av',['--video-workers','2'])]+[(f'N{n}',NEW,'av',['--video-workers',str(n)]) for n in [1,2,3,4,6,8]]+[('baseline',None,'av',[]),('old-audio',OLD,'audio',[]),('audio-inline',NEW,'audio',['--no-audio-decode-thread']),('audio-thread',NEW,'audio',[])]
def run(variant,binary,track,extra,rep,seconds=330,batch=32):
 name=f'{"baseline" if binary is None else "tenzor"}-{seconds}-b{batch}-{variant}-r{rep}'
 media=ROOT/'fixtures'/(f'long-{seconds}.mp4' if track=='av' else f'long-{seconds}-{track}_only.mp4');out=OUT/f'{name}.tenzor'
 cmd=[binary,'-i',media,'-o',out,'--batch-epochs',str(batch),*extra] if binary else [sys.executable,ROOT/'reference/ffmpeg_arrow_baseline.py',media,out,'--batch-epochs',str(batch)]
 rec=measure(name,cmd,seconds,out,batch,engine_binary=binary or NEW);rec.update(variant=variant,track=track,repetition=rep,profiled='--profile' in extra,warmup=rep==0)
 if binary is None:rec.pop('engine_binary_sha256',None)
 rec['logical_columns']=json.loads(subprocess.check_output([sys.executable,ROOT/'scripts/check_benchmark_artifact.py',out,str(seconds*2),str(batch)]))
 if binary:
  key=(track,seconds)
  if key in expected:assert rec['logical_columns']==expected[key],name
  else:expected[key]=rec['logical_columns']
 for line in (ROOT/'evidence'/f'benchmark-{name}.log').read_text().splitlines():
  if line.startswith('TENZOR_PROFILE '):rec['profile']=json.loads(line.removeprefix('TENZOR_PROFILE '))
  if line.startswith('video_mode='):rec['video_mode']=line
 # Preserve one artifact per mode and all extra/profile artifacts for delayed rereads.
 rec['artifact_retained']=rep==1 or rep>=6
 if rec['artifact_retained']:retained.append(rec)
 else:out.unlink()
 rows.append(rec);(ROOT/'evidence/epyc-benchmarks-v020.json').write_text(json.dumps(rows,indent=2))
for rep in range(6):
 for args in (group if rep%2==0 else list(reversed(group))):run(*args,rep)
for n in [1,2,3,4,6,8]:run(f'N{n}-profile',NEW,'av',['--video-workers',str(n),'--profile'],6)
for label,bin_,extra in [('audio-thread-profile',NEW,[]),('audio-inline-profile',NEW,['--no-audio-decode-thread']),('old-audio-profile',OLD,[])]:run(label,bin_,'audio',extra+['--profile'],6)
for batch in [2,64]:run(f'N4-batch{batch}',NEW,'av',['--video-workers','4'],7,batch=batch)
run('video-N4',NEW,'video',['--video-workers','4','--profile'],8)
run('N4-short',NEW,'av',['--video-workers','4'],9,seconds=30)
run('baseline-short',None,'av',[],9,seconds=30)
for rec in retained:
 checked=json.loads(subprocess.check_output([sys.executable,ROOT/'scripts/check_benchmark_artifact.py',rec['artifact_path'],str(int(rec['media_seconds']*2)),str(rec['batch_epochs'])]));assert checked==rec['logical_columns'],rec['name']
machine=dict(platform=platform.platform(),python=sys.version,affinity=sorted(os.sched_getaffinity(0)),cpuinfo=next(x.split(':',1)[1].strip() for x in pathlib.Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name')),binary_sha256=hashlib.sha256(NEW.read_bytes()).hexdigest(),old_binary_sha256=hashlib.sha256(OLD.read_bytes()).hexdigest())
(ROOT/'evidence/machine-v020.json').write_text(json.dumps(machine,indent=2))
(ROOT/'evidence/epyc-retained-v020.json').write_text(json.dumps(retained,indent=2))
print('PASS',len(rows),'measured/verified outputs;',sum(not x['warmup'] for x in rows),'non-warmup;',len(retained),'delayed rereads')
for variant,*_ in group:
 xs=[x for x in rows if x['variant']==variant and 1<=x['repetition']<=5];print(variant,'median',statistics.median(x['wall_seconds'] for x in xs),'RSS MiB',statistics.median(x['peak_tree_rss_sampled_bytes'] for x in xs)/1048576)
