#!/usr/bin/env python3
"""Extend the memory check to 22 minutes without retaining validation arrays in the sampler."""
import argparse,json,math,subprocess,sys
from benchmark_matrix import ROOT,OUT,measure
p=argparse.ArgumentParser();p.add_argument('--resume-log',type=__import__('pathlib').Path);args=p.parse_args()
media=ROOT/'fixtures/long-1320.mp4'
if args.resume_log:
    result=json.loads(args.resume_log.read_text().splitlines()[0]);out=ROOT/result['artifact_path']
else:
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-stream_loop','1',
        '-i',str(ROOT/'fixtures/long-660.mp4'),'-t','1320','-c','copy',str(media)],check=True)
    out=OUT/'tenzor-1320-b32.tenzor'
    result=measure('tenzor-1320-b32',[ROOT/'target/release/tenzor','-i',media,'-o',out,'--batch-epochs','32'],1320,out,32)
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries',
    'stream=duration','-of','json',str(media)]))
seconds=max(float(s['duration']) for s in probe['streams'])
expected_rows=math.ceil(seconds/.5)
result['nominal_target_seconds']=1320
result['media_seconds']=seconds
result['media_seconds_per_second']=seconds/result['wall_seconds']
result['epochs_per_second']=expected_rows/result['wall_seconds']
result['source_durations_seconds']=[float(s['duration']) for s in probe['streams']]
# Independent consumer in another process, after the timed run.
code='''import sys,json,math,pyarrow as pa,pyarrow.ipc as ipc,numpy as np
r=ipc.open_file(pa.memory_map(sys.argv[1]));rows=0
for i in range(r.num_record_batches):
 b=r.get_batch(i);ts=b.column('timestamp_ms').to_numpy();assert np.array_equal(ts,np.arange(rows,rows+b.num_rows)*500)
 for name in b.schema.names:
  col=b.column(name);values=col.values if pa.types.is_fixed_size_list(col.type) else col
  assert np.isfinite(values.to_numpy()).all()
 rows+=b.num_rows
assert rows==int(sys.argv[2]) and r.num_record_batches==math.ceil(rows/32)
print(json.dumps(dict(epochs=rows,batches=r.num_record_batches,all_finite=True)))
'''
result['verification']=json.loads(subprocess.check_output([sys.executable,'-c',code,str(out),str(expected_rows)]))
(ROOT/'evidence/epoch-memory-extension.json').write_text(json.dumps(result,indent=2))
print('PASS 22-minute artifact verification')
