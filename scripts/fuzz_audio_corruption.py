import random, subprocess, hashlib, pathlib, time, os, sys, tempfile, json
ROOT=pathlib.Path(__file__).resolve().parents[1]
OLD=os.environ.get('OLD',str(ROOT/'reference/bin/tenzor-v0.1.9-linux-x86_64')); NEW=os.environ.get('NEW',str(ROOT/'target/release/tenzor'))
import tomllib;NEW_VERSION=tomllib.loads((ROOT/'Cargo.toml').read_text())['package']['version'].encode()
def run(b, inp, extra):
    o=pathlib.Path(f'fz-{os.getpid()}.tenzor'); o.unlink(missing_ok=True); t=time.time()
    try: p=subprocess.run([b,'-i',inp,'-o',str(o),*extra],capture_output=True,text=True,timeout=120)
    except subprocess.TimeoutExpired: return ('TIMEOUT',None,'',False,120)
    h=None
    if p.returncode==0:
        d=o.read_bytes().replace(NEW_VERSION,b'0.1.9'); h=hashlib.sha256(d).hexdigest()
    left=o.exists() and p.returncode!=0; o.unlink(missing_ok=True)
    err=[l for l in p.stderr.splitlines() if l.startswith('Error')]
    return (p.returncode,h,err[0] if err else '',left or 'panicked' in p.stderr,time.time()-t)
sources=[pathlib.Path(x).resolve() for x in sys.argv[1:]]
os.chdir(tempfile.mkdtemp(prefix="tenzor-audio-fuzz-"))
random.seed(11); total=0; agree=0; exact=0; rows=[]
for src in sources:
    F=pathlib.Path(src).read_bytes()
    cases=[]
    for i in range(20):
        b=bytearray(F); off=int(len(b)*(0.03+0.94*i/19))
        for k in range(24): b[min(off+k*53,len(b)-1)]^=random.randrange(1,256)
        cases.append(('flip%.2f'%(0.03+0.94*i/19),b))
    for frac in (0.5,0.9,0.99): cases.append(('trunc%.2f'%frac,bytearray(F[:int(len(F)*frac)])))
    for name,b in cases:
        pathlib.Path('fz.mp4').write_bytes(b)
        o=run(OLD,'fz.mp4',[]); n=run(NEW,'fz.mp4',[])
        same_outcome = o[0]==n[0] and o[1]==n[1]
        same_error = same_outcome and o[2]==n[2]
        clean = n[0]!='TIMEOUT' and o[0]!='TIMEOUT' and not n[3] and not o[3]
        rows.append(dict(file=src.name,case=name,old=o,new=n,pass_gate=bool(same_error and clean)))
        total+=1; agree+=same_outcome and clean; exact+=same_error and clean
        print(pathlib.Path(src).name,name,'OK' if same_outcome and clean else 'DIFF','old',o[0],o[2][:55],'| new',n[0],n[2][:55],'| same_msg',same_error,f'{n[4]:.2f}s',flush=True)
print('cases',total,'same_outcome',agree,'same_error_text',exact)

(ROOT/"evidence/v0.3.0/corruption-fuzz.json").write_text(json.dumps(rows,indent=2))
assert total==agree==exact, f"corruption agreement failure: {total}/{agree}/{exact}"
