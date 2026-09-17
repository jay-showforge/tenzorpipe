import random, subprocess, hashlib, pathlib, time
ROOT=pathlib.Path(__file__).resolve().parents[1]; F=(ROOT/'fixtures/long-30.mp4').read_bytes(); import os,tempfile,json
OLD=os.environ['TENZOR_OLD_BINARY']; NEW=str(ROOT/'target/release/tenzor'); OUT=pathlib.Path(tempfile.mkdtemp(prefix='tenzor-fuzz-'));os.chdir(OUT)
def run(b, inp, extra):
    o=pathlib.Path('fz.tenzor'); o.unlink(missing_ok=True); t=time.time()
    try: p=subprocess.run([b,'-i',inp,'-o',str(o),*extra],capture_output=True,text=True,timeout=60)
    except subprocess.TimeoutExpired: return ('TIMEOUT',None,'',60)
    h=hashlib.sha256(o.read_bytes()).hexdigest() if p.returncode==0 and o.exists() else None
    left=o.exists() and p.returncode!=0
    o.unlink(missing_ok=True)
    err=[l for l in p.stderr.splitlines() if l.startswith('Error')]
    return (p.returncode,h,(err[0] if err else '')+(' LEFTOVER' if left else '')+(' PANIC' if 'panicked' in p.stderr else ''),time.time()-t)
random.seed(7); same=0; rows=[]
for i in range(24):
    b=bytearray(F); frac=0.05+0.9*i/23; off=int(len(b)*frac)
    for k in range(48): b[off+k*37]^=random.randrange(1,256)
    pathlib.Path('fz.mp4').write_bytes(b)
    o=run(OLD,'fz.mp4',['--execution','concurrent'])
    n=run(NEW,'fz.mp4',['--video-workers','4'])
    ok = o[0]==n[0] and o[1]==n[1] or (o[0]!=0 and n[0]!=0 and o[0]!='TIMEOUT' and n[0]!='TIMEOUT')
    assert o[0]!=0 and n[0]!=0 and ok and not any(word in o[2]+n[2] for word in ['LEFTOVER','PANIC']), (i,o,n)
    rows.append(dict(position=frac,old=o,new=n,pass_gate=True))
    same+=ok; print(f'{frac:.2f}', 'OK ' if ok else 'DIFF', 'old',o[0],o[2][:60],'| new',n[0],n[2][:60], f'{n[3]:.2f}s')
print('agree',same,'/24')

assert same==24
(ROOT/'evidence/v0.1.9-corruption-fuzz.json').write_text(json.dumps(rows,indent=2))
