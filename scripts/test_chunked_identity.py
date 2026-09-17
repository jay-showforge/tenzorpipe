import subprocess, hashlib, pathlib, itertools, json, sys, time, re
import os,tempfile; ROOT=pathlib.Path(__file__).resolve().parents[1]; FIX=ROOT/'fixtures'; OUT=pathlib.Path(tempfile.mkdtemp(prefix='v018-identity-'))
OLD=pathlib.Path(os.environ['TENZOR_OLD_BINARY']); NEW=ROOT/'target/release/tenzor'
def run(bin_, inp, extra, tag):
    o=OUT/f'{tag}.tenzor'; o.unlink(missing_ok=True)
    p=subprocess.run([str(bin_),'-i',str(inp),'-o',str(o),*extra],capture_output=True,text=True,timeout=600)
    if p.returncode==0:
        digest=hashlib.sha256();tail=b'';count=0
        with o.open('rb') as stream:
            while chunk:=stream.read(1048576):
                data=tail+chunk
                if bin_==NEW:
                    count+=data.count(b'0.1.9');data=data.replace(b'0.1.9',b'0.1.6')
                digest.update(data[:-4]);tail=data[-4:]
            digest.update(tail)
        if bin_==NEW:assert count==2, 'unexpected version bytes'
        h=digest.hexdigest()
    else: h=None
    o.unlink(missing_ok=True)
    mode=[l for l in p.stderr.splitlines() if l.startswith('video_mode=')]
    return p.returncode,h,(mode[0] if mode else ''),p.stderr.strip().splitlines()[-1:] 
files=sys.argv[1].split(',')
settings=[['--resolution','224'],['--resolution','160','--window-sec','0.33','--batch-epochs','2'],['--resolution','336','--window-sec','1.0'],['--resolution','224','--window-sec','0.05','--execution','sequential']]
rows=[];fails=0
for f in files:
    for s in settings:
        rc0,h0,_,err0=run(OLD,FIX/f,s,'old')
        for n in ['1','2','4','8']:
            for extra in ([[]] if n=='1' else [[],['--chunk-target-ms','250'],['--video-buffer-mib','1']]):
                rc,h,mode,err=run(NEW,FIX/f,s+['--video-workers',n]+extra,'new')
                if 'video_mode=chunked' in mode:
                    bound=int(re.search(r'bound_bytes=(\d+)',mode).group(1))
                    budget=int(extra[1])*1048576 if extra and extra[0]=='--video-buffer-mib' else 64*1048576
                    assert bound<=budget, (mode,budget)
                ok=(rc0==0)==(rc==0) and h==h0
                fails+= not ok
                rows.append(dict(file=f,settings=' '.join(s),workers=n,extra=' '.join(extra),old_rc=rc0,new_rc=rc,identical=h==h0,mode=mode,ok=ok))
                print(('PASS' if ok else 'FAIL'),f,' '.join(s),'N='+n,' '.join(extra),mode, '' if rc==0 else err, flush=True)
(ROOT/'evidence').mkdir(exist_ok=True); json.dump(rows,open(ROOT/'evidence/v0.1.9-identity-matrix.json','w'),indent=1)
print('TOTAL',len(rows),'FAIL',fails)

assert fails==0, f'{fails} identity cases failed'
