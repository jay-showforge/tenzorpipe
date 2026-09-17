"""Diagnostic: which source frame does DALI's readers.video return for each epoch?

Decodes the benchmark clip with DALI (no resize) and TorchCodec-CUDA, then for every DALI
sample finds the TorchCodec frame index with the smallest mean absolute difference.
"""
import sys, torch
from nvidia.dali import fn, pipeline_def, types
from nvidia.dali.plugin.pytorch import feed_ndarray
from torchcodec.decoders import VideoDecoder

clip, step = sys.argv[1], 15
ref = VideoDecoder(clip, device="cuda", dimension_order="NHWC")
n_frames = len(ref)
n = len(range(0, n_frames, step))

@pipeline_def(batch_size=n, num_threads=2, device_id=0)
def pipe():
    return fn.readers.video(device="gpu", filenames=[clip], sequence_length=1, step=step, random_shuffle=False,
                            pad_last_batch=True, image_type=types.RGB, dtype=types.UINT8, skip_vfr_check=True)
p = pipe(); p.build()
t = p.run()[0].as_tensor()
dali = torch.empty(t.shape(), dtype=torch.uint8, device="cuda"); feed_ndarray(t, dali)
dali = dali[:, 0].float()
offsets = {}
for k in range(n):
    cand = [i for i in range(k * step - 3, k * step + 4) if 0 <= i < n_frames]
    frames = ref.get_frames_at(indices=cand).data.float()
    err = (frames - dali[k]).abs().mean(dim=(1, 2, 3))
    best = cand[int(err.argmin())]
    offsets[best - k * step] = offsets.get(best - k * step, 0) + 1
    if k < 4 or k == n - 1:
        print(f"epoch {k:2d}: expected frame {k*step:3d}, best match {best:3d} (MAE {float(err.min()):.2f}, at expected {float(err[cand.index(k*step)]):.2f})")
print("offset histogram (DALI frame - expected):", offsets)
