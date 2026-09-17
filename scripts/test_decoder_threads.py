#!/usr/bin/env python3
"""Experimental decoder threads run only in timeout-protected subprocesses."""
import os
import argparse,hashlib,json,pathlib,subprocess,sys,tempfile,time
R=pathlib.Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--binary',required=True,type=pathlib.Path);p.add_argument('--reference',required=True,type=pathlib.Path);a=p.parse_args()
BASE=pathlib.Path(os.environ.get('TENZOR_OUTPUT_ROOT',R/'out'));BASE.mkdir(parents=True,exist_ok=True)
O=pathlib.Path(tempfile.mkdtemp(prefix='decoder-threads-',dir=BASE));results=[];quarantined=set()
for fixture in ['baseline720.mp4','high-bframes.mp4','short.mp4','silent-video.mp4','selection-vfr.mp4','corrupt-nal.mp4']:
    reference=None
    if fixture!='corrupt-nal.mp4':
        out=O/'reference.tenzor';out.unlink(missing_ok=True)
        subprocess.run([str(a.reference),'-i',str(R/'fixtures'/fixture),'-o',str(out),'--resolution','160'],check=True,capture_output=True,timeout=30)
        reference=json.loads(subprocess.check_output([sys.executable,R/'scripts/hash_arrow.py',out]))
    for threads in [0,1,2,4]:
        if threads in quarantined:
            results.append(dict(fixture=fixture,threads=threads,status="not_run_after_prior_failure"));continue
        out=O/f'{fixture}-t{threads}.tenzor';started=time.monotonic()
        cmd=[str(a.binary),'-i',str(R/'fixtures'/fixture),'-o',str(out),'--resolution','160','--decoder-threads',str(threads)]
        try:
            proc=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            rec=dict(fixture=fixture,threads=threads,exit=proc.returncode,seconds=time.monotonic()-started,stderr=proc.stderr[-4000:])
            if proc.returncode==0 and reference is not None:
                actual=json.loads(subprocess.check_output([sys.executable,R/'scripts/hash_arrow.py',out]));rec['bit_identical']=actual==reference
            if proc.returncode!=0:rec['partial_output_present']=out.exists()
        except subprocess.TimeoutExpired as e:
            rec=dict(fixture=fixture,threads=threads,timeout=True,seconds=time.monotonic()-started,partial_output_present=out.exists())
        if threads and (rec.get("timeout") or rec.get("exit",0)<0 or rec.get("bit_identical") is False or (reference is not None and rec.get("exit")!=0)):quarantined.add(threads)
        results.append(rec);out.unlink(missing_ok=True)
        (R/'evidence/decoder-thread-experiments.json').write_text(json.dumps(dict(experimental_binary_sha256=hashlib.sha256(a.binary.read_bytes()).hexdigest(),reference_binary_sha256=hashlib.sha256(a.reference.read_bytes()).hexdigest(),cases=results),indent=2))
        print(json.dumps(rec),flush=True)
