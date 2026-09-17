#!/usr/bin/env python3
"""Measure each process tree serially; psutil sampling plus wait4 rusage."""
import os,time,pathlib,subprocess,json,sys,platform,hashlib,tempfile
import psutil
ROOT=pathlib.Path(__file__).resolve().parents[1];BASE=pathlib.Path(os.environ.get('TENZOR_OUTPUT_ROOT',ROOT/'out'));BASE.mkdir(parents=True,exist_ok=True);OUT=pathlib.Path(tempfile.mkdtemp(prefix='benchmark-',dir=BASE))
ENV=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
def measure(name,cmd,seconds,out,batch,engine_binary=None):
    out.unlink(missing_ok=True);log=ROOT/'evidence'/f'benchmark-{name}.log'
    peak=0;io={};rss_trace=[];started=time.perf_counter()
    with log.open('w') as f:
        # Work's PID namespace can differ from the mounted /proc namespace.
        host_pid=int(next(line.split()[1] for line in pathlib.Path('/proc/self/status').read_text().splitlines() if line.startswith('Pid:')))
        parent=psutil.Process(host_pid)
        p=subprocess.Popen(list(map(str,cmd)),stdout=f,stderr=subprocess.STDOUT,env=ENV)
        proc=None
        for child in parent.children():
            try:
                status_text=pathlib.Path(f'/proc/{child.pid}/status').read_text()
                ns=next((line.split()[1:] for line in status_text.splitlines() if line.startswith('NSpid:')), [str(child.pid)])
                if int(ns[-1])==p.pid:proc=child;break
            except (OSError,psutil.NoSuchProcess):pass
        if proc is None:
            # A short-lived process can exit before sampling; wait4 still supplies peak RSS.
            proc=parent

        while True:
            try:
                procs=[proc]+proc.children(recursive=True);rss=0
                for q in procs:
                    try:
                        rss+=q.memory_info().rss
                        counters=pathlib.Path(f'/proc/{q.pid}/io').read_text().splitlines()
                        written=int(next(line.split()[1] for line in counters if line.startswith('write_bytes:')))
                        io[q.pid]=max(io.get(q.pid,0),written)
                    except (psutil.NoSuchProcess,psutil.AccessDenied,OSError):pass
                peak=max(peak,rss)
                if os.environ.get('TENZOR_RSS_TRACE') and proc != parent:
                    status={k:int(v.split()[0])*1024 for k,v in (line.split(':',1) for line in pathlib.Path(f'/proc/{proc.pid}/status').read_text().splitlines()) if k in ['VmRSS','RssAnon','RssFile','RssShmem']}
                    rss_trace.append(dict(seconds=time.perf_counter()-started,**status))
            except (psutil.NoSuchProcess,OSError):pass
            pid,status,usage=os.wait4(p.pid,os.WNOHANG)
            if pid:break
            time.sleep(.01)
        p.returncode=os.waitstatus_to_exitcode(status)
    elapsed=time.perf_counter()-started
    if p.returncode:raise RuntimeError(f'{name} failed, see {log}')
    assert out.stat().st_size>8, f'{name}: empty/truncated artifact after successful exit'
    result={'name':name,'artifact_path':str(out),'command':list(map(str,cmd)),'media_seconds':seconds,'batch_epochs':batch,'wall_seconds':elapsed,'peak_tree_rss_sampled_bytes':peak,'rusage_maxrss_kib':usage.ru_maxrss,'cpu_seconds':usage.ru_utime+usage.ru_stime,'cpu_percent_one_core':(usage.ru_utime+usage.ru_stime)/elapsed*100,'artifact_bytes':out.stat().st_size,'sampled_tree_write_bytes':sum(io.values()),'media_seconds_per_second':seconds/elapsed,'epochs_per_second':seconds*2/elapsed,'intermediate_media_files_bytes':0,'engine_binary_sha256':hashlib.sha256((engine_binary or ROOT/'target'/'release'/'tenzor').read_bytes()).hexdigest()}
    if name.startswith("baseline-"):result.pop("engine_binary_sha256",None)
    if rss_trace:result["rss_trace"]=rss_trace
    print(json.dumps({k:v for k,v in result.items() if k!="rss_trace"}),flush=True);return result
if __name__=='__main__':
    results=[]
    # Paired short/long runs, plus batch-size scaling. Sequential to avoid contention.
    for seconds,batch,kind in [(30,32,'tenzor'),(30,32,'baseline'),(330,32,'tenzor'),(330,32,'baseline'),(330,2,'tenzor'),(330,64,'tenzor'),(30,32,'tenzor'),(30,32,'baseline'),(90,32,'tenzor'),(660,32,'tenzor'),(330,32,'video_only'),(330,32,'audio_only')]:
        name=f'{kind}-{seconds}-b{batch}-r{len(results)}';media=ROOT/'fixtures'/(f'long-{seconds}.mp4' if kind in ['tenzor','baseline'] else f'long-330-{kind}.mp4');out=OUT/f'{name}.tenzor'
        cmd=[ROOT/'target'/'release'/'tenzor','-i',media,'-o',out,'--batch-epochs',str(batch)] if kind!='baseline' else [sys.executable,ROOT/'reference'/'ffmpeg_arrow_baseline.py',media,out,'--batch-epochs',str(batch)]
        results.append(measure(name,cmd,seconds,out,batch));(ROOT/'evidence'/'benchmarks.json').write_text(json.dumps(results,indent=2))
    machine={'platform':platform.platform(),'python':sys.version,'cpu_count':os.cpu_count(),'affinity':sorted(os.sched_getaffinity(0)),'cpuinfo':next((x.split(':',1)[1].strip() for x in pathlib.Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name'))),'thread_env':{k:ENV[k] for k in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']}}
    (ROOT/'evidence'/'machine.json').write_text(json.dumps(machine,indent=2))
