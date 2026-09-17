import subprocess, json, pathlib, hashlib, statistics, time, resource, os
ROOT=pathlib.Path(__file__).resolve().parents[1]; FIX=str(ROOT/'fixtures/long-330.mp4')
OLD=os.environ['TENZOR_OLD_BINARY']; NEW=str(ROOT/'target/release/tenzor')
variants=[('v0.1.6',OLD,[]),('v0.1.8 N=1',NEW,['--video-workers','1']),('v0.1.8 N=2',NEW,['--video-workers','2']),('v0.1.8 N=3',NEW,['--video-workers','3']),('v0.1.8 N=4',NEW,['--video-workers','4'])]
RUNS=int(os.environ.get('RUNS','3')); rows=[]; hashes={}
for rep in range(RUNS+1):
  for name,b,extra in variants:
    o=pathlib.Path('bench.tenzor'); o.unlink(missing_ok=True)
    cmd=[b,'-i',FIX,'-o',str(o),'--resolution','224','--batch-epochs','32','--profile',*extra]
    t0=time.monotonic(); pr=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=open('err.txt','w'),text=True)
    import threading
    peak=[0]; stop=[False]
    def sampler():
        while not stop[0]:
            try:
                for line in open(f'/proc/{pr.pid}/status'):
                    if line.startswith('VmHWM:'): peak[0]=max(peak[0],int(line.split()[1]))
            except Exception: pass
            time.sleep(0.01)
    th=threading.Thread(target=sampler); th.start()
    _,status,ru=os.wait4(pr.pid,0); stop[0]=True; th.join(); el=time.monotonic()-t0; pr.returncode=os.waitstatus_to_exitcode(status)
    class P: pass
    p=P(); p.returncode=pr.returncode; p.stderr=open('err.txt').read(); assert p.returncode==0,p.stderr
    p.stderr+=f'\nTIME {el} {ru.ru_utime} {ru.ru_stime} {peak[0]}'
    d=o.read_bytes(); 
    if b==NEW: d=d.replace(b'0.1.8',b'0.1.6')
    hashes.setdefault(name,set()).add(hashlib.sha256(d).hexdigest()); o.unlink()
    t=[l for l in p.stderr.splitlines() if l.startswith('TIME')][0].split()
    prof=json.loads([l for l in p.stderr.splitlines() if l.startswith('TENZOR_PROFILE')][0].split(' ',1)[1])
    mode=[l for l in p.stderr.splitlines() if l.startswith('video_mode')]
    if rep==0: continue  # warm-up
    wall=float(t[1]); cpu=(float(t[2])+float(t[3]))/wall*100
    rows.append(dict(variant=name,run=rep,wall=wall,cpu_pct=cpu,peak_rss_mib=int(t[4])/1024,stages=prof['stages_seconds'],mode=mode[0] if mode else ''))
    print(name,rep,f'wall={wall:.2f}s cpu={cpu:.0f}% rss={int(t[4])/1024:.1f}MiB', mode[0] if mode else '',flush=True)
json.dump(dict(rows=rows,hashes={k:sorted(v) for k,v in hashes.items()},cores=os.cpu_count()),open(ROOT/'evidence/v0.1.8-bench-330.json','w'),indent=1)
print('\nidentical_across_variants:',len(set().union(*hashes.values()))==1)
for name,_,_ in variants:
    r=[x for x in rows if x['variant']==name]; w=[x['wall'] for x in r]
    st=lambda k: statistics.median(x['stages'].get(k,0) for x in r)
    print(f"{name:12s} median {statistics.median(w):.2f}s [{min(w):.2f}-{max(w):.2f}] cpu {statistics.median(x['cpu_pct'] for x in r):.0f}% rss {max(x['peak_rss_mib'] for x in r):.1f}MiB | decode(sum) {st('video_decode'):.2f} audio_src {st('audio_source'):.2f} resample {st('audio_resample'):.2f} mel {st('audio_mel'):.2f} write {st('arrow_write'):.2f} recv_wait {st('collector_receive_wait'):.2f} win_wait {st('video_window_wait'):.2f} reorder_wait {st('video_reorder_wait'):.2f}")
