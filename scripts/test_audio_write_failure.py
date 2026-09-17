#!/usr/bin/env python3
"""Real EFBIG write errors must keep the original error and clean output in all audio modes."""
import hashlib,json,pathlib,resource,signal,subprocess,tempfile
R=pathlib.Path(__file__).resolve().parents[1];O=pathlib.Path(tempfile.mkdtemp(prefix='audio-write-failure-'));old=R/'reference/bin/tenzor-v0.1.9-linux-x86_64';new=R/'target/release/tenzor';rows=[]
def limit():
 signal.signal(signal.SIGXFSZ,signal.SIG_IGN);resource.setrlimit(resource.RLIMIT_FSIZE,(10000,10000))
for name in ['audio-only.mp4','high-bframes.mp4','wav-48000-2ch.wav']:
 for mode,args in [('threaded',[]),('inline',['--no-audio-decode-thread']),('N4',['--video-workers','4','--queue-mib','0']),('sequential',['--execution','sequential'])]:
  errors=[]
  for binary,opts in [(old,[x for x in args if x!='--no-audio-decode-thread']),(new,args)]:
   out=O/'failed.tenzor'
   p=subprocess.run(list(map(str,[binary,'-i',R/'fixtures'/name,'-o',out,'--batch-epochs','1',*opts])),capture_output=True,text=True,timeout=30,preexec_fn=limit)
   assert p.returncode>0 and not out.exists() and 'panicked' not in p.stderr,(name,mode,p.stderr)
   error=p.stderr[p.stderr.index('Error:'):].strip();assert 'File too large' in error,error;errors.append(error)
  assert errors[0]==errors[1],(name,mode,errors)
  rows.append(dict(file=name,mode=mode,error=errors[1],same_full_error=True,clean_exit=True));print('PASS',name,mode,flush=True)
(R/'evidence/audio-write-failure.json').write_text(json.dumps(dict(binary_sha256=hashlib.sha256(new.read_bytes()).hexdigest(),cases=rows),indent=2))
print('PASS',len(rows),'paired write-failure cases')
