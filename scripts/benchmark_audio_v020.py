import subprocess, json, pathlib, hashlib, statistics, time, os, threading, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]; OUTD=ROOT/'evidence'/'v0.2.0'
A330=str(ROOT/'fixtures/long-330-audio_only.mp4'); L330=str(ROOT/'fixtures/long-330.mp4')
B19=str(ROOT/'reference/bin/tenzor-v0.1.9-linux-x86_64'); B19L=os.environ.get('TENZOR_V019_SAME_TOOLCHAIN',B19); B20=str(ROOT/'target/release/tenzor')
variants=[
 ('audio-only v0.1.9 (bundled)',B19,A330,[]),
 ('audio-only v0.1.9 (same toolchain)',B19L,A330,[]),
 ('audio-only v0.2.0 inline decode',B20,A330,['--no-audio-decode-thread']),
 ('audio-only v0.2.0',B20,A330,[]),
 ('full N1 v0.1.9 (same toolchain)',B19L,L330,['--video-workers','1']),
 ('full N1 v0.2.0',B20,L330,['--video-workers','1']),
 ('full N2 v0.1.9 (same toolchain)',B19L,L330,['--video-workers','2']),
 ('full N2 v0.2.0',B20,L330,['--video-workers','2']),
]
RUNS=int(os.environ.get('RUNS','5')); rows=[]; hashes={}
def once(name,b,f,extra,rep):
    o=pathlib.Path('/tmp')/'bench-v020.tenzor'; o.unlink(missing_ok=True)
    cmd=[b,'-i',f,'-o',str(o),'--resolution','224','--batch-epochs','32','--profile',*extra]
    t0=time.monotonic(); pr=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=open(pathlib.Path('/tmp')/'bench-v020-err.txt','w'))
    peak=[0]; stop=[False]
    def sampler():
        while not stop[0]:
            try:
                for line in open(f'/proc/{pr.pid}/status'):
                    if line.startswith('VmHWM:'): peak[0]=max(peak[0],int(line.split()[1]))
            except Exception: pass
            time.sleep(0.01)
    th=threading.Thread(target=sampler); th.start()
    _,status,ru=os.wait4(pr.pid,0); wall=time.monotonic()-t0; stop[0]=True; th.join()
    err=open(pathlib.Path('/tmp')/'bench-v020-err.txt').read(); assert os.waitstatus_to_exitcode(status)==0, err
    d=o.read_bytes().replace(b'0.2.0',b'0.1.9'); hashes.setdefault(pathlib.Path(f).name,set()).add(hashlib.sha256(d).hexdigest()); o.unlink()
    prof=json.loads([l for l in err.splitlines() if l.startswith('TENZOR_PROFILE')][0].split(' ',1)[1])
    return dict(variant=name,run=rep,wall=wall,cpu_pct=(ru.ru_utime+ru.ru_stime)/wall*100,peak_rss_mib=peak[0]/1024,stages=prof['stages_seconds'])
for rep in range(RUNS+1):
    order=variants if rep%2==0 else list(reversed(variants))
    for name,b,f,extra in order:
        r=once(name,b,f,extra,rep)
        if rep==0: continue
        rows.append(r); print(f"{name:38s} r{rep} {r['wall']:.3f}s cpu {r['cpu_pct']:.0f}% rss {r['peak_rss_mib']:.1f}",flush=True)
summary=[]
for name,_,_,_ in variants:
    r=[x for x in rows if x['variant']==name]; w=[x['wall'] for x in r]
    st=lambda k: statistics.median(x['stages'].get(k,0) for x in r)
    summary.append(dict(variant=name,median_wall=statistics.median(w),min=min(w),max=max(w),cpu=statistics.median(x['cpu_pct'] for x in r),rss=statistics.median(x['peak_rss_mib'] for x in r),audio_source=st('audio_source'),audio_source_wait=st('audio_source_wait'),audio_resample=st('audio_resample'),audio_mel=st('audio_mel'),video_decode=st('video_decode')))
out=dict(host=open('/proc/cpuinfo').read().split('model name')[1].split('\n')[0].strip(': '),cpus=os.cpu_count(),runs=RUNS,rows=rows,summary=summary,identical_outputs={k:len(v)==1 for k,v in hashes.items()})
json.dump(out,open(OUTD/'benchmark-v020.json','w'),indent=1)
print('\nidentical outputs per input:',out['identical_outputs'])
for s in summary: print(f"{s['variant']:38s} median {s['median_wall']:.3f}s [{s['min']:.3f}-{s['max']:.3f}] cpu {s['cpu']:.0f}% rss {s['rss']:.1f}MiB | src {s['audio_source']:.2f} src_wait {s['audio_source_wait']:.2f} resample {s['audio_resample']:.2f} mel {s['audio_mel']:.2f} vdec {s['video_decode']:.2f}")
