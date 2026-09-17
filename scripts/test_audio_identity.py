import subprocess, hashlib, pathlib, json, sys, os, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
OLD=os.environ.get('OLD', str(ROOT/'reference/bin/tenzor-v0.1.9-linux-x86_64')); NEW=os.environ.get('NEW', str(ROOT/'target/release/tenzor'))
OUT=pathlib.Path(tempfile.mkdtemp(prefix='id20-'))
def run(b, inp, extra, tag):
    o=OUT/f'{tag}.tenzor'; o.unlink(missing_ok=True)
    p=subprocess.run([b,'-i',str(inp),'-o',str(o),*extra],capture_output=True,text=True,timeout=900)
    h=None
    if p.returncode==0:
        digest=hashlib.sha256();tail=b'';count=0
        with o.open('rb') as stream:
            while chunk:=stream.read(1048576):
                data=tail+chunk
                if b==NEW:
                    count+=data.count(b'0.2.0');data=data.replace(b'0.2.0',b'0.1.9')
                digest.update(data[:-4]);tail=data[-4:]
            digest.update(tail)
        if b==NEW:assert count==2, 'unexpected version string count'
        h=digest.hexdigest()
    leftover=p.returncode!=0 and o.exists()
    assert not leftover, (inp,extra,p.stderr)

    o.unlink(missing_ok=True)
    err=[l for l in p.stderr.splitlines() if l.startswith('Error')]
    return p.returncode,h,(err[0] if err else ''),('panicked' in p.stderr)
files=[pathlib.Path(x) if os.path.isabs(x) else ROOT/'fixtures'/x for x in sys.argv[1].split(',')]
settings=[['--resolution','224'],['--resolution','160','--window-sec','0.33','--batch-epochs','2'],['--window-sec','0.05','--batch-epochs','7'],['--execution','sequential','--window-sec','1.0']]
variants=[[],['--no-audio-decode-thread'],['--video-workers','2']]
rows=[];bad=0
for f in files:
    for s in settings:
        r0=run(OLD,f,s,'old')
        if os.environ.get('REQUIRE_SUCCESS')=='1':assert r0[0]==0,(f,r0)
        for v in variants:
            r=run(NEW,f,s+v,'new')
            ok = r[0]==r0[0] and r[1]==r0[1] and r[2]==r0[2] and not r[3]
            bad+=not ok
            rows.append(dict(file=f.name,settings=' '.join(s),variant=' '.join(v),old_rc=r0[0],new_rc=r[0],same_bytes=r[1]==r0[1],old_error=r0[2],new_error=r[2],ok=ok))
            print('PASS' if ok else 'FAIL', f.name, ' '.join(s), ' '.join(v), 'rc',r0[0],r[0], r0[2][:50], '|', r[2][:50], flush=True)
out=os.environ.get('RESULT',str(ROOT/'evidence/v0.2.0/audio-identity.json')); json.dump(rows,open(out,'w'),indent=1)
print('TOTAL',len(rows),'FAIL',bad,'SUCCESS_ARTIFACTS',sum(1 for x in rows if x['old_rc']==0 and x['ok']),'ERROR_AGREEMENTS',sum(1 for x in rows if x['old_rc']!=0 and x['ok']))

assert bad==0, f"{bad} identity cases failed"
