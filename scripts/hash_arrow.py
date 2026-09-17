#!/usr/bin/env python3
"""Hash logical columns in a separate consumer, keeping the benchmark parent small."""
import hashlib,json,sys
import pyarrow as pa
import pyarrow.ipc as ipc
reader=ipc.open_file(pa.memory_map(sys.argv[1]));hashes={n:hashlib.sha256() for n in reader.schema.names};rows=0
for i in range(reader.num_record_batches):
    batch=reader.get_batch(i);rows+=batch.num_rows
    for name in reader.schema.names:
        col=batch.column(name);arr=col.values if pa.types.is_fixed_size_list(col.type) else col
        hashes[name].update(arr.to_numpy(zero_copy_only=True).tobytes())
print(json.dumps(dict(rows=rows,columns={n:h.hexdigest() for n,h in hashes.items()})))
