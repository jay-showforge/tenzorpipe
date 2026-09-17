"""Zero-copy-ish PyTorch reader for TenzorPipe Arrow IPC files.

Each Arrow RecordBatch is memory-mapped and converted independently, so loading a long
file does not concatenate/copy the entire dataset into one giant tensor. PyTorch is imported
on first use, so Arrow-only consumers (`dataset.reader`) do not need it installed.
"""
from __future__ import annotations
import bisect
import warnings
import pyarrow as pa
import pyarrow.ipc as ipc


def _torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError("TenzorDataset batches are PyTorch tensors: pip install 'tenzorpipe[torch]'") from exc
    return torch


def _from_numpy(values):
    # Zero-copy views of read-only Arrow buffers are the documented contract (see the class
    # docstring), so PyTorch's generic non-writable-array warning is expected here.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="The given NumPy array is not writable")
        return _torch().from_numpy(values)


class TenzorDataset:
    def __init__(self, path: str, *, copy: bool = False):
        """copy=False aliases read-only Arrow buffers: NEVER mutate returned tensors.

        Set copy=True for training transforms/in-place operations. PyTorch cannot enforce
        read-only storage. Keep the dataset alive while using zero-copy batches.
        """
        self.copy = copy
        self.source = pa.memory_map(path, "r")
        self.reader = ipc.open_file(self.source)
        self.schema = self.reader.schema
        md = self.schema.metadata or {}
        self.has_audio = md.get(b"has_audio", b"true") == b"true"
        self.has_video = "video_tensor" in self.schema.names
        self.audio_shape = tuple(int(x) for x in md[b"audio_shape"].decode().split(","))
        self.video_shape = None
        if self.has_video:
            self.video_shape = tuple(int(x) for x in md[b"video_shape"].decode().split(","))

        self._batch_lengths = [self.reader.get_batch(i).num_rows for i in range(self.reader.num_record_batches)]
        self._ends = []
        total = 0
        for n in self._batch_lengths:
            total += n
            self._ends.append(total)
        self.length = total

    def __len__(self):
        return self.length

    def _locate(self, idx: int):
        if idx < 0:
            idx += self.length
        if idx < 0 or idx >= self.length:
            raise IndexError(idx)
        b = bisect.bisect_right(self._ends, idx)
        start = 0 if b == 0 else self._ends[b - 1]
        return b, idx - start

    @staticmethod
    def _tensor_from_fixed_list(column, rows: int, shape):
        width = column.type.list_size
        values = column.values.slice(column.offset * width, rows * width).to_numpy(zero_copy_only=True)
        # Arrow's NumPy buffer is read-only. torch.from_numpy still aliases it; do not mutate.
        return _from_numpy(values).view(rows, *shape)

    def get_batch(self, batch_index: int):
        rb = self.reader.get_batch(batch_index)
        out = {
            "timestamp_ms": _from_numpy(rb.column("timestamp_ms").to_numpy(zero_copy_only=True)),
            "audio": self._tensor_from_fixed_list(rb.column("audio_mel_tensor"), rb.num_rows, self.audio_shape),
        }
        if self.has_video and "video_timestamp_ms" in rb.schema.names:
            out["video_timestamp_ms"] = _from_numpy(rb.column("video_timestamp_ms").to_numpy(zero_copy_only=True))
        if "audio_valid_frames" in rb.schema.names:
            out["audio_valid_frames"] = _from_numpy(rb.column("audio_valid_frames").to_numpy(zero_copy_only=True))
        if self.has_video:
            out["video"] = self._tensor_from_fixed_list(rb.column("video_tensor"), rb.num_rows, self.video_shape)
        return {k: v.clone() for k, v in out.items()} if self.copy else out

    def __getitem__(self, idx: int):
        b, row = self._locate(idx)
        batch = self.get_batch(b)
        return {k: v[row] for k, v in batch.items()}

    def iter_batches(self):
        for i in range(self.reader.num_record_batches):
            yield self.get_batch(i)

    def close(self):
        self.source.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
