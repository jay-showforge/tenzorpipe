#!/usr/bin/env python3
"""Compare every logical Arrow value with the prior release; test sparse/VFR timing."""
import os
import argparse, hashlib, json, pathlib, re, subprocess, tempfile
import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc

ROOT = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--old-binary', required=True, type=pathlib.Path)
p.add_argument('--video-workers', default=1, type=int, choices=[1,2,4,8])
a = p.parse_args()
old = a.old_binary.resolve()
new = ROOT / 'target/release/tenzor'
BASE=pathlib.Path(os.environ.get('TENZOR_OUTPUT_ROOT',ROOT/'out'));BASE.mkdir(parents=True,exist_ok=True)
OUT = pathlib.Path(tempfile.mkdtemp(prefix='selection-', dir=BASE))

def digest(path):
    reader = ipc.open_file(pa.memory_map(str(path)))
    hashes = {name: hashlib.sha256() for name in reader.schema.names}
    pts = []
    for batch in (reader.get_batch(i) for i in range(reader.num_record_batches)):
        pts.extend(batch.column('video_timestamp_ms').to_pylist())
        for name in reader.schema.names:
            col = batch.column(name)
            values = col.values if pa.types.is_fixed_size_list(col.type) else col
            arr = values.to_numpy(zero_copy_only=True)
            assert np.isfinite(arr).all()
            hashes[name].update(arr.tobytes())
    return {name: h.hexdigest() for name, h in hashes.items()}, pts

def ff(name, args):
    path = ROOT / 'fixtures' / name
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*args,str(path)], check=True)
    return path

# Low frame rate exercises midpoint ties and one picture serving several epochs.
ff('selection-sparse.mp4', ['-f','lavfi','-i','testsrc2=size=320x240:rate=1:duration=3',
    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=4.27',
    '-c:v','libx264','-threads','2','-bf','2','-c:a','aac'])
# Irregular frame gaps and B-frame presentation reordering, including a short tail.
ff('selection-vfr.mp4', ['-f','lavfi','-i','testsrc2=size=320x240:rate=30:duration=3.27',
    '-vf', "select='eq(mod(n,7),0)+eq(mod(n,11),0)'", '-fps_mode','vfr',
    '-c:v','libx264','-threads','2','-bf','3','-an'])
cases = [(n, r, w) for n in ['high-bframes.mp4','short.mp4','selection-sparse.mp4','selection-vfr.mp4']
    for r in [160,224,336] for w in [0.05,0.33,0.5]]
cases += [(n,224,0.5) for n in ['baseline720.mp4','silent-video.mp4','color709.mp4','fullrange.mp4','sync-pulse.mp4']]
results = []
for i,(name,res,window) in enumerate(cases):
    hashes = []
    for label,binary in [('old',old),('new',new)]:
        out = OUT / f'{i}-{label}.tenzor'
        cmd = [str(binary),'-i',str(ROOT/'fixtures'/name),'-o',str(out),
            '--resolution',str(res),'--window-sec',str(window),'--batch-epochs','2']
        if label == 'new': cmd += ['--video-workers',str(a.video_workers)]
        proc = subprocess.run(cmd,capture_output=True,text=True,check=True)
        values,pts = digest(out)
        hashes.append(values)
        if label == 'new':
            m = re.search(r'decoded (\d+) access units, resized (\d+) selected pictures',proc.stderr)
            assert m, proc.stderr
            decoded,resized = map(int,m.groups())
            assert resized == len(set(pts)), (name,resized,pts)
        out.unlink()
    assert hashes[0] == hashes[1], (name,res,window)
    results.append(dict(file=name,resolution=res,window_sec=window,decoded=decoded,
        resized=resized,epochs=len(pts),all_columns_bit_identical=True,column_sha256=hashes[1]))
    print('PASS',name,res,window,decoded,resized,flush=True)
report = dict(old_binary_sha256=hashlib.sha256(old.read_bytes()).hexdigest(),
    new_binary_sha256=hashlib.sha256(new.read_bytes()).hexdigest(),cases=results,video_workers=a.video_workers)
(ROOT/'evidence/epoch-selection-regression.json').write_text(json.dumps(report,indent=2))
print('PASS',len(results),'old/new exact-value comparisons')
