#!/usr/bin/env python3
"""Independent bounded-memory IPC correctness gate, outside benchmark timing."""
import hashlib,json,math,sys
import numpy as np
import pyarrow as pa,pyarrow.ipc as ipc
p,expected,batch_size=sys.argv[1],int(sys.argv[2]),int(sys.argv[3]);r=ipc.open_file(pa.memory_map(p));md={k.decode():v.decode() for k,v in r.schema.metadata.items()};rows=0;h={n:hashlib.sha256() for n in r.schema.names}
assert md['audio_shape']=='50,64' and md['window_ms']=='500'
if 'video_tensor' in r.schema.names:assert md['video_shape']=='3,224,224'
for i in range(r.num_record_batches):
 b=r.get_batch(i);assert np.array_equal(b.column('timestamp_ms').to_numpy(),np.arange(rows,rows+b.num_rows)*500)
 for name in r.schema.names:
  c=b.column(name);a=(c.values if pa.types.is_fixed_size_list(c.type) else c).to_numpy(zero_copy_only=True)
  assert np.isfinite(a).all(),name;h[name].update(a.tobytes())
 rows+=b.num_rows
assert rows==expected and r.num_record_batches==math.ceil(expected/batch_size)
print(json.dumps(dict(rows=rows,columns={n:v.hexdigest() for n,v in h.items()})))
