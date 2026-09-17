#!/usr/bin/env python3
"""Reopen all outputs after repeated runs to detect delayed publication failures."""
import argparse,hashlib,json,pathlib,subprocess,sys,tempfile
R=pathlib.Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--binary',type=pathlib.Path,default=R/'target/release/tenzor');p.add_argument('--output-root',type=pathlib.Path,default=R/'out');a=p.parse_args()
O=pathlib.Path(tempfile.mkdtemp(prefix='publication-',dir=a.output_root));B=a.binary
paths=[];checks=[]
for i in range(20):
    out=O/f'run-{i}.tenzor'
    subprocess.run([str(B),'-i',str(R/'fixtures/high-bframes.mp4'),'-o',str(out),'--resolution','160','--batch-epochs','2'],check=True,capture_output=True,timeout=30)
    checks.append(json.loads(subprocess.check_output([sys.executable,R/'scripts/hash_arrow.py',out])))
    paths.append(out)
for out,expected in zip(paths,checks):
    actual=json.loads(subprocess.check_output([sys.executable,R/'scripts/hash_arrow.py',out]));assert actual==expected
assert all(c==checks[0] for c in checks)
(R/'evidence/publication-stress.json').write_text(json.dumps(dict(binary_sha256=hashlib.sha256(B.read_bytes()).hexdigest(),runs=20,immediate_and_delayed_reopen=True,identical_columns=True),indent=2))
print('PASS 20 repeated publications; immediate and delayed independent rereads')
