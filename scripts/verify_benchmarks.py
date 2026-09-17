#!/usr/bin/env python3
"""Validate entire benchmark artifacts and compare independent pipeline outputs."""
import pathlib,json,hashlib,math,argparse
p=argparse.ArgumentParser();p.add_argument("--records",default="benchmarks.json");p.add_argument("--output",default="benchmark-verification.json");args=p.parse_args()
import pyarrow as pa,pyarrow.ipc as ipc
import numpy as np
from verify_media import visual
ROOT=pathlib.Path(__file__).resolve().parents[1]
records=json.loads((ROOT/'evidence'/args.records).read_text());results=[];digests={}
for rec in records:
    p=ROOT/rec['artifact_path'];r=ipc.open_file(pa.memory_map(str(p)));md={k.decode():v.decode() for k,v in r.schema.metadata.items()};rows=0;hashes={name:hashlib.sha256() for name in r.schema.names};selected={name:[] for name in r.schema.names};chosen=[0,int(rec['media_seconds']),int(rec['media_seconds']*2)-1]
    for i in range(r.num_record_batches):
        b=r.get_batch(i);ts=b.column('timestamp_ms').to_numpy(zero_copy_only=True)
        assert np.array_equal(ts,np.arange(rows,rows+b.num_rows)*500)
        for name in r.schema.names:
            c=b.column(name);v=c.values.to_numpy(zero_copy_only=True).reshape(b.num_rows,-1) if pa.types.is_fixed_size_list(c.type) else c.to_numpy(zero_copy_only=True)
            assert np.isfinite(v).all();hashes[name].update(v.tobytes())
            for idx in chosen:
                if rows<=idx<rows+b.num_rows:selected[name].append(v[idx-rows:idx-rows+1])
        rows+=b.num_rows
    assert rows==rec['media_seconds']*2
    assert r.num_record_batches==math.ceil(rows/rec['batch_epochs'])
    digests[rec['name']]={k:h.hexdigest() for k,h in hashes.items()}
    result={'name':rec['name'],'epochs':rows,'batches':r.num_record_batches,'all_finite':True,'tensor_hashes':digests[rec['name']]}
    if rec['name'].startswith('tenzor-330-b32-N4-r1'):
        data={name:np.concatenate(a) for name,a in selected.items()};result['selected_long_visual']=visual(ROOT/'fixtures'/'long-330.mp4',data,md)
    results.append(result)
longs=[r for r in records if r['name'].startswith('tenzor-330') and r.get('track','av')=='av']
for r in longs[1:]:assert digests[r['name']]==digests[longs[0]['name']],'batch-size-dependent tensors'
# Independent pipelines use different sinc kernels; compare energetic audio bins,
# and compare normalized visual tensors directly across every 30-second row.
a=next(r for r in records if r['name'].startswith('tenzor-30-'));b=next(r for r in records if r['name'].startswith('baseline-30-'))
ra=ipc.open_file(pa.memory_map(str(ROOT/a['artifact_path'])));rb=ipc.open_file(pa.memory_map(str(ROOT/b['artifact_path'])))
vmax=0;frame_mae_max=0;psnr_min=99.;audio_sum=0;audio_n=0
for i in range(ra.num_record_batches):
    ba,bb=ra.get_batch(i),rb.get_batch(i)
    x=ba.column('video_tensor').values.to_numpy();y=bb.column('video_tensor').values.to_numpy();vmax=max(vmax,float(abs(x-y).max()))
    delta=((x-y)*127.5).reshape(ba.num_rows,-1);frame_mae_max=max(frame_mae_max,float(abs(delta).mean(axis=1).max()));mse=(delta**2).mean(axis=1);psnr_min=min(psnr_min,float((-10*np.log10(np.maximum(mse,1e-15)/255**2)).min()))
    x=ba.column('audio_mel_tensor').values.to_numpy();y=bb.column('audio_mel_tensor').values.to_numpy();mask=(x>-5)&(y>-5);audio_sum+=float(abs(x[mask]-y[mask]).sum());audio_n+=int(mask.sum())
# Use the same per-frame source-image criterion as the media matrix.
assert frame_mae_max<3.,frame_mae_max
assert audio_sum/audio_n<.1,audio_sum/audio_n
result={'artifacts':results,'all_long_batch_sizes_identical':True,'baseline_visual_max_abs_normalized':vmax,'baseline_worst_frame_mae_255':frame_mae_max,'baseline_min_frame_psnr_db':psnr_min,'baseline_active_audio_logmel_mae':audio_sum/audio_n}
(ROOT/'evidence'/args.output).write_text(json.dumps(result,indent=2));print('PASS benchmark correctness and batch-size invariance')
