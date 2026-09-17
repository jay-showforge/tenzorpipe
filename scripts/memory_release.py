#!/usr/bin/env python3
"""Duration scaling at the selected worker count, plus exact 22-minute v0.1.9 comparison."""
import argparse,json,os,pathlib,subprocess,sys
from benchmark_matrix import ROOT,OUT,measure
p=argparse.ArgumentParser();p.add_argument('--old-binary',type=pathlib.Path,default=ROOT/'reference/bin/tenzor-v0.1.9-linux-x86_64');p.add_argument('--workers',type=int,required=True);a=p.parse_args()
os.environ['TENZOR_RSS_TRACE']='1';records=[];long=OUT/'long-1320.mp4';new=ROOT/'target/release/tenzor'
subprocess.run(['ffmpeg','-v','error','-y','-stream_loop','1','-i',str(ROOT/'fixtures/long-660.mp4'),'-t','1320','-c','copy','-movflags','+faststart',str(long)],check=True)
for nominal,binary,workers,variant in [(330,new,a.workers,'new'),(660,new,a.workers,'new'),(1320,new,a.workers,'new'),(1320,a.old_binary.resolve(),2,'v019')]:
 media=long if nominal==1320 else ROOT/'fixtures'/f'long-{nominal}.mp4';out=OUT/f'memory-{variant}-{nominal}.tenzor'
 seconds=float(json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(media)]))['format']['duration'])
 r=measure(f'memory-{variant}-N{workers}-{nominal}',[binary,'-i',media,'-o',out,'--video-workers',str(workers),'--profile'],seconds,out,32,engine_binary=binary)
 r.update(nominal_seconds=nominal,variant=variant,workers=workers)
 r['logical_columns']=json.loads(subprocess.check_output([sys.executable,ROOT/'scripts/check_benchmark_artifact.py',out,str(__import__('math').ceil(seconds*2)),'32']))
 r['epochs_per_second']=r['logical_columns']['rows']/r['wall_seconds']
 for field in ['VmRSS','RssAnon','RssFile']:r[f'peak_{field}_bytes']=max(v.get(field,0) for v in r['rss_trace'])
 records.append(r);(ROOT/'evidence/memory-release.json').write_text(json.dumps(records,indent=2))
assert records[-1]['logical_columns']==records[-2]['logical_columns']
print('PASS four memory artifacts; 22-minute v0.1.9/current exact-column comparison')
