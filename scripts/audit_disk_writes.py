#!/usr/bin/env python3
"""Test-only libc writable-open audit, used where sandbox policy blocks ptrace.
This observes dynamically linked libc calls; direct syscalls can bypass it.
"""
import json,os,pathlib,subprocess,sys,tempfile
R=pathlib.Path(__file__).resolve().parents[1]
base=pathlib.Path(os.environ.get('TENZOR_OUTPUT_ROOT',R/'out'));base.mkdir(parents=True,exist_ok=True)
o=pathlib.Path(tempfile.mkdtemp(prefix='disk-audit-',dir=base));results=[]
workers=json.loads((R/'evidence/worker-recommendation-v020.json').read_text())['recommended']
lib=o/'audit_opens.so'
subprocess.run(['cc','-shared','-fPIC','-O2',R/'scripts/audit_opens.c','-ldl','-o',lib],check=True)
env=dict(os.environ,LD_PRELOAD=str(lib),PYTHONDONTWRITEBYTECODE='1')
# Demonstrate observation of a deliberately created intermediate in a descendant.
probe=o/'probe.log';sentinel=o/'deliberate-intermediate.raw'
probe_env=dict(env,TENZOR_OPEN_AUDIT=str(probe))
subprocess.run([sys.executable,'-c',
    'import subprocess,sys;subprocess.run([sys.executable,"-c",'+
    repr('import sys;open(sys.argv[1], "w").write("x")')+',sys.argv[1]],check=True)',
    str(sentinel)],env=probe_env,check=True)
assert str(sentinel) in probe.read_text();sentinel.unlink()
for variant in ['single','chunked','baseline']:
    out=o/f'{variant}.tenzor';trace=R/'evidence'/f'disk-audit-{variant}.opens';trace.unlink(missing_ok=True)
    media=R/'fixtures/long-30.mp4'
    cmd=[sys.executable,R/'reference/ffmpeg_arrow_baseline.py',media,out] if variant=='baseline' else [R/'target/release/tenzor','-i',media,'-o',out,'--video-workers',str(workers) if variant=='chunked' else '1']
    proc=subprocess.run(list(map(str,cmd)),env=dict(env,TENZOR_OPEN_AUDIT=str(trace)),capture_output=True,text=True,timeout=60)
    assert proc.returncode==0,proc.stderr
    paths=[line.split('\t',1)[1] for line in trace.read_text().splitlines()]
    unexpected=sorted(set(paths)-{str(out),'/dev/null'})
    assert str(out) in paths and not unexpected,(variant,paths)
    checked=json.loads(subprocess.check_output([sys.executable,R/'scripts/hash_arrow.py',out]))
    assert checked['rows']==60
    results.append(dict(variant=variant,command=list(map(str,cmd)),writable_paths=sorted(set(paths)),artifact_bytes=out.stat().st_size,intermediate_media_files_bytes=0,method='LD_PRELOAD libc writable-open audit; descendant sentinel self-test passed',scope='30-second dynamic-libc process tree; direct syscalls/static binaries can bypass interception; excluded from timing',strace_status='Blocked by environment: PTRACE_TRACEME Operation not permitted'))
    out.unlink()
(R/'evidence/disk-write-audit.json').write_text(json.dumps(results,indent=2))
print('PASS 3 dynamic-libc writable-open audits; descendant sentinel detected; no observed intermediate media files')
