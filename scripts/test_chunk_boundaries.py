#!/usr/bin/env python3
"""Verified IDR-span parity, independent boundary oracle, bounded queues and failure cleanup."""
import argparse,hashlib,json,os,pathlib,resource,signal,subprocess,sys,tempfile,time,re
import numpy as np
from verify_media import read,visual
R=pathlib.Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--old-binary',required=True,type=pathlib.Path);a=p.parse_args()
old=a.old_binary.resolve();new=R/'target/release/tenzor'
base=pathlib.Path(os.environ.get('TENZOR_OUTPUT_ROOT',R/'out'));base.mkdir(parents=True,exist_ok=True)
o=pathlib.Path(tempfile.mkdtemp(prefix='dual-',dir=base));fixtures=R/'fixtures';results=[]
def ff(name,rate,duration,gop,extra=(),audio_duration=None):
    path=fixtures/name
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'testsrc2=size=320x240:rate={rate}:duration={duration}',
        '-f','lavfi','-i',f'sine=frequency=440:sample_rate=48000:duration={audio_duration or duration}',
        '-c:v','libx264','-threads','2','-profile:v','high','-g',str(gop),'-keyint_min',str(gop),'-sc_threshold','0','-bf','3',
        '-c:a','aac',*extra,str(path)],check=True)
    return path
ff('dual-idr.mp4',20,4.15,40)
ff('dual-off-grid.mp4',30,5.23,37)
ff('dual-audio-tail.mp4',20,4.15,40,audio_duration=6.27)
ff('dual-sparse.mp4',1,6,2,audio_duration=7.27)
ff('dual-open-gop.mp4',20,4.15,20,['-x264-params','open-gop=1'])
ff('dual-vfr.mp4',30,5.23,20,['-vf',"select='eq(mod(n,3),0)+eq(mod(n,7),0)'",'-fps_mode','vfr'])
def run(binary,name,media,res=224,window='.5',extra=(),failure=False,limited=False):
    out=o/f'{name}.tenzor'
    def limit():
        signal.signal(signal.SIGXFSZ,signal.SIG_IGN);resource.setrlimit(resource.RLIMIT_FSIZE,(100000,100000))
    t=time.monotonic()
    proc=subprocess.run(list(map(str,[binary,'-i',media,'-o',out,'--resolution',res,'--window-sec',window,'--batch-epochs','2',*extra])),
        capture_output=True,text=True,timeout=30,preexec_fn=limit if limited else None)
    if failure:
        assert proc.returncode!=0 and not out.exists(),(name,proc.stderr)
        assert 'panicked' not in proc.stderr,proc.stderr
        assert not list(o.glob(f'.{out.name}*')),name
        result=dict(name=name,exit=proc.returncode,elapsed_seconds=time.monotonic()-t,error=proc.stderr.strip())
        results.append(result);print('PASS',name,flush=True);return
    assert proc.returncode==0,(name,proc.stderr)
    digest=json.loads(subprocess.check_output([sys.executable,R/'scripts/hash_arrow.py',out]))
    mode=next((line for line in proc.stderr.splitlines() if line.startswith('video_mode=')), '')
    match=re.search(r'workers=(\d+)',mode)
    partition=dict(workers=int(match.group(1)) if match else 1,mode=mode)
    if media.name=='dual-idr.mp4':partition['split_sample']=40
    profile=next((json.loads(x.removeprefix('TENZOR_PROFILE ')) for x in proc.stderr.splitlines() if x.startswith('TENZOR_PROFILE ')),None)
    return out,digest,partition,profile
# New fixtures exercise actual splits; most pre-existing short cases deliberately fall back.
for name in ['dual-idr.mp4','dual-off-grid.mp4','dual-audio-tail.mp4','dual-sparse.mp4','dual-open-gop.mp4','dual-vfr.mp4']:
    for res,window,budget,depth in [(160,'.05',0,2),(224,'.33',1,4),(336,'.5',4,2)]:
        tag=f'{name}-{res}-{window}'
        ref,h0,_,_=run(old,tag+'-old',fixtures/name,res,window)
        out,h1,partition,profile=run(new,tag+'-new',fixtures/name,res,window,['--video-workers','2','--queue-mib',str(budget),'--queue-depth',str(depth),'--profile'])
        assert h0==h1,(name,res,window)
        assert partition['workers']==(1 if name=='dual-open-gop.mp4' else 2),(name,partition)
        assert profile['queued_payload_budget_used_bytes']<=budget*1048576
        results.append(dict(name=tag,all_columns_bit_identical=True,partition=partition,profile=profile,logical_columns=h1))
        print('PASS',tag,partition,flush=True);ref.unlink();out.unlink()
# Native decoded frames around the split, in sample/decode order K-1,K,K+1.
ref,h0,_,_=run(old,'boundary-old',fixtures/'dual-idr.mp4',160,'.05')
out,h1,partition,_=run(new,'boundary-new',fixtures/'dual-idr.mp4',160,'.05',['--video-workers','2'])
assert h0==h1
old_data,md,_=read(ref);new_data,_,_=read(out)
e0=visual(fixtures/'dual-idr.mp4',old_data,md);e1=visual(fixtures/'dual-idr.mp4',new_data,md)
packets=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_packets','-show_entries','packet=pts_time','-of','json',str(fixtures/'dual-idr.mp4')]))['packets']
boundary=[];tiles=[]
from PIL import Image
for index in [partition['split_sample']-1,partition['split_sample'],partition['split_sample']+1]:
    pts=round(float(packets[index]['pts_time'])*1000);row=int(np.flatnonzero(new_data['video_timestamp_ms']==pts)[0]);assert e0[row]==e1[row]
    actual=new_data['video_tensor'][row].reshape(3,160,160)
    prior=old_data['video_tensor'][row].reshape(3,160,160)
    tiles.append(((np.concatenate([prior,actual],axis=2).transpose(1,2,0)+1)*127.5).clip(0,255).astype('uint8'))
    boundary.append(dict(sample_index=index,pts_ms=pts,epoch=row,v016=e0[row],v017=e1[row],exact_match=True))
Image.fromarray(np.concatenate(tiles)).save(R/'evidence/dual-boundary-v016-v017.png')
results.append(dict(name='boundary-oracle',frames=boundary));ref.unlink();out.unlink()
# Corrupt non-sync packets in each decoder span after initialization.
src=fixtures/'dual-idr.mp4'
packets=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_packets','-show_entries','packet=pos,size','-of','json',str(src)]))['packets']
for worker,index in [(0,10),(1,60)]:
    blob=bytearray(src.read_bytes());pos=int(packets[index]['pos']);blob[pos:pos+4]=bytes([127,255,255,255]);bad=o/f'bad-worker{worker}.mp4';bad.write_bytes(blob)
    for budget in [0,1,4]:run(new,f'corrupt-worker{worker}-q{budget}',bad,extra=['--video-workers','2','--queue-mib',str(budget)],failure=True)
for budget in [0,1,4]:run(new,f'writer-failure-q{budget}',src,extra=['--video-workers','2','--queue-mib',str(budget)],failure=True,limited=True)
for workers in [65,999]:run(new,f'invalid-workers-{workers}',src,extra=['--video-workers',str(workers)],failure=True)
report=dict(binary_sha256=hashlib.sha256(new.read_bytes()).hexdigest(),old_binary_sha256=hashlib.sha256(old.read_bytes()).hexdigest(),cases=results,directory=str(o))
(R/'evidence/chunk-boundary-tests.json').write_text(json.dumps(report,indent=2))
print('PASS',len(results),'dual-decoder checks')
