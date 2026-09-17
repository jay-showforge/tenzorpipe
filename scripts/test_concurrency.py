#!/usr/bin/env python3
"""Exact sequential/concurrent equivalence and real subprocess failure recovery."""
import hashlib,json,os,pathlib,resource,signal,subprocess,sys,tempfile,time
R=pathlib.Path(__file__).resolve().parents[1];B=R/'target/release/tenzor';BASE=pathlib.Path(os.environ.get('TENZOR_OUTPUT_ROOT',R/'out'));BASE.mkdir(parents=True,exist_ok=True);O=pathlib.Path(tempfile.mkdtemp(prefix='concurrency-',dir=BASE))
results=[]
short_audio=O/'short-audio.mp4'
subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=320x240:rate=30:duration=2.27','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.73','-c:v','libx264','-threads','2','-bf','3','-c:a','aac',str(short_audio)],check=True)
def run(name,media,args,fail=False,limited=False):
    out=O/f'{name}.tenzor';cmd=[B,'-i',media,'-o',out,'--video-workers','4',*args]
    def limit():
        signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE,(100000,100000))
    start=time.monotonic();p=subprocess.run(list(map(str,cmd)),capture_output=True,text=True,timeout=30,preexec_fn=limit if limited else None)
    if fail:
        assert p.returncode!=0 and not out.exists(),(name,p.returncode,p.stderr)
        assert 'panicked' not in p.stderr,p.stderr
        results.append(dict(name=name,exit=p.returncode,error=p.stderr.strip(),seconds=time.monotonic()-start));return
    assert p.returncode==0,(name,p.stderr)
    digest=json.loads(subprocess.check_output([sys.executable,R/'scripts/hash_arrow.py',out]))
    profile=json.loads(next(x.removeprefix('TENZOR_PROFILE ') for x in p.stderr.splitlines() if x.startswith('TENZOR_PROFILE ')))
    assert profile['queued_payload_budget_used_bytes']<=int(args[args.index('--queue-mib')+1])*1048576
    if media==short_audio:
        from verify_media import read,verify
        import numpy as np
        data,md,_=read(out)
        assert np.array_equal(data['audio_valid_frames'],[50,23,0,0,0])
        assert np.all(data['audio_mel_tensor'][2:]==-10)
        verify(media,out,224,2)
    out.unlink();results.append(dict(name=name,hashes=digest,profile=profile));return digest
for fixture,res,window in [('high-bframes.mp4',224,'.5'),('high-bframes.mp4',336,'.33'),('short.mp4',1024,'.05'),('selection-sparse.mp4',160,'.5'),('wav-44100-2ch.wav',224,'.33'),('audio-only.mp4',224,'.5'),('silent-video.mp4',224,'.5'),('short-audio.mp4',224,'.5')]:
    reference=None
    for mode,budget in [('sequential',0),('concurrent',0),('concurrent',1),('concurrent',4)]:
        name=f'{fixture}-{res}-{mode}-q{budget}'
        actual=run(name,short_audio if fixture=='short-audio.mp4' else R/'fixtures'/fixture,['--execution',mode,'--resolution',str(res),'--window-sec',window,'--batch-epochs','2','--queue-mib',str(budget),'--profile'])
        if reference is None:reference=actual
        assert actual==reference,name
# Generate independently damaged access units after initially valid packets.
for kind,selector,index in [('video','v:0',30),('audio','a:0',40)]:
    src=R/'fixtures/high-bframes.mp4';blob=bytearray(src.read_bytes())
    packets=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams',selector,'-show_packets','-show_entries','packet=pos,size','-of','json',str(src)]))['packets']
    packet=packets[index];pos,size=int(packet['pos']),int(packet['size'])
    if kind=='video':blob[pos:pos+4]=bytes([127,255,255,255])
    else:blob[pos:pos+size]=b'\xff'*size
    bad=O/f'late-{kind}.mp4';bad.write_bytes(blob)
    for budget in [0,4]:run(f'late-{kind}-q{budget}',bad,['--queue-mib',str(budget),'--batch-epochs','1'],fail=True)
for budget in [0,4]:run(f'writer-failure-q{budget}',R/'fixtures/high-bframes.mp4',['--queue-mib',str(budget),'--batch-epochs','1'],fail=True,limited=True)
(R/'evidence/concurrency-tests.json').write_text(json.dumps(dict(binary_sha256=hashlib.sha256(B.read_bytes()).hexdigest(),cases=results),indent=2))
print('PASS',len(results),'concurrency/budget/failure checks')
