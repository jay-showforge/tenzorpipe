import random, subprocess, hashlib, pathlib, time
ROOT=pathlib.Path(__file__).resolve().parents[1]; F=(ROOT/'fixtures/long-30.mp4').read_bytes(); import os,tempfile,json
OLD=os.environ['TENZOR_OLD_BINARY']; NEW=str(ROOT/'target/release/tenzor'); OUT=pathlib.Path(tempfile.mkdtemp(prefix='tenzor-fuzz-'));os.chdir(OUT)
import tomllib;NEW_VERSION=tomllib.loads((ROOT/'Cargo.toml').read_text())['package']['version'].encode()
def run(b, inp, extra):
    o=pathlib.Path('fz.tenzor'); o.unlink(missing_ok=True); t=time.time()
    try: p=subprocess.run([b,'-i',inp,'-o',str(o),*extra],capture_output=True,text=True,timeout=60)
    except subprocess.TimeoutExpired: return ('TIMEOUT',None,'',60)
    data=o.read_bytes() if p.returncode==0 and o.exists() else None
    # Compare logical content with the v0.1.9 oracle: only the embedded version string differs.
    h=hashlib.sha256(data.replace(NEW_VERSION,b'0.1.9') if b==NEW else data).hexdigest() if data is not None else None
    left=o.exists() and p.returncode!=0
    o.unlink(missing_ok=True)
    err=[l for l in p.stderr.splitlines() if l.startswith('Error')]
    return (p.returncode,h,(err[0] if err else '')+(' LEFTOVER' if left else '')+(' PANIC' if 'panicked' in p.stderr else ''),time.time()-t)
pathlib.Path('clean.mp4').write_bytes(F)
clean=run(NEW,'clean.mp4',['--video-workers','4'])[1]
assert clean is not None and clean==run(OLD,'clean.mp4',[])[1], 'clean long-30 output differs from oracle'
random.seed(7); same=0; skipped_ok=0; rows=[]
for i in range(24):
    b=bytearray(F); frac=0.05+0.9*i/23; off=int(len(b)*frac)
    for k in range(48): b[off+k*37]^=random.randrange(1,256)
    pathlib.Path('fz.mp4').write_bytes(b)
    o=run(OLD,'fz.mp4',['--execution','concurrent'])
    # --no-skip-nonref decodes every picture, so it must reject every corruption like the oracle.
    strict=run(NEW,'fz.mp4',['--video-workers','4','--no-skip-nonref'])
    ok = o[0]==strict[0] and o[1]==strict[1] or (o[0]!=0 and strict[0]!=0 and o[0]!='TIMEOUT' and strict[0]!='TIMEOUT')
    assert o[0]!=0 and strict[0]!=0 and ok and not any(word in o[2]+strict[2] for word in ['LEFTOVER','PANIC']), (i,o,strict)
    # Default skipping may succeed only when every damaged byte sat in a skipped picture,
    # which is proven by output identical to the uncorrupted input's.
    n=run(NEW,'fz.mp4',['--video-workers','4'])
    skip_ok = n[0]!=0 and n[0]!='TIMEOUT' or n[1]==clean
    assert skip_ok and not any(word in n[2] for word in ['LEFTOVER','PANIC']), (i,o,n)
    skipped_ok+=n[0]==0
    rows.append(dict(position=frac,old=o,new_no_skip=strict,new_default=n,
                     default_succeeded_with_clean_output=n[0]==0,pass_gate=True))
    same+=ok; print(f'{frac:.2f}', 'OK ' if ok else 'DIFF', 'old',o[0],o[2][:50],'| no-skip',strict[0],strict[2][:50],
                    '| default', 'clean-identical' if n[0]==0 else f'{n[0]} {n[2][:40]}', f'{n[3]:.2f}s')
print('strict agree',same,'/24; default succeeded with clean-identical output',skipped_ok,'/24')

assert same==24
(ROOT/'evidence/v0.1.9-corruption-fuzz.json').write_text(json.dumps(rows,indent=2))
