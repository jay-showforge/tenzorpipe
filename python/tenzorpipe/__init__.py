"""TenzorPipe: MP4 H.264/AAC-LC and WAV to synchronized video and Log-Mel tensors.

    import tenzorpipe as tp
    tp.ingest("clip.mp4", "clip.tenzor")           # decode once into Arrow IPC
    with tp.load("clip.tenzor") as data:           # memory-mapped, zero-copy tensors
        for batch in data.iter_batches():
            video, mel = batch["video"], batch["audio"]

Options left as ``None`` use the engine defaults (the same as the ``tenzor`` CLI).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from typing import Optional, Union

from .dataset import TenzorDataset

__all__ = ["TenzorDataset", "TenzorError", "ingest", "load", "__version__"]

PathLike = Union[str, "os.PathLike[str]"]


def _native():
    # Imported lazily so the pure-Python loader works from a source checkout without the extension.
    return importlib.import_module("._engine", __name__)


def __getattr__(name: str):
    if name == "__version__":
        return _native().__version__
    if name == "TenzorError":
        return _native().TenzorError
    raise AttributeError(name)


def ingest(
    input: PathLike,
    output: PathLike,
    *,
    resolution: Optional[int] = None,
    window_sec: Optional[float] = None,
    batch_epochs: Optional[int] = None,
    video_workers: Optional[int] = None,
    execution: Optional[str] = None,
    queue_mib: Optional[int] = None,
    queue_depth: Optional[int] = None,
    chunk_target_ms: Optional[int] = None,
    video_buffer_mib: Optional[int] = None,
    skip_nonref: bool = True,
    audio_decode_thread: bool = True,
    profile: bool = False,
    verbose: bool = False,
) -> dict:
    """Convert ``input`` (MP4 or WAV) into a new ``.tenzor`` Arrow IPC file at ``output``.

    ``output`` must not exist. On failure a ``tenzorpipe.TenzorError`` is raised and any
    partial output is removed. Returns ``{"output", "epochs", "seconds"}``, plus ``"profile"``
    (a dict of stage timers) when ``profile=True``.

    The engine is silent unless ``verbose=True``, which prints its diagnostics (decoder mode,
    per-video summary and any profile JSON) on stderr, as the ``tenzor`` command does.
    """
    argv = ["-i", os.fspath(input), "-o", os.fspath(output)]
    options = {
        "--resolution": resolution,
        "--window-sec": window_sec,
        "--batch-epochs": batch_epochs,
        "--video-workers": video_workers,
        "--execution": execution,
        "--queue-mib": queue_mib,
        "--queue-depth": queue_depth,
        "--chunk-target-ms": chunk_target_ms,
        "--video-buffer-mib": video_buffer_mib,
    }
    for flag, value in options.items():
        if value is not None:
            argv += [flag, str(value)]
    if not skip_nonref:
        argv.append("--no-skip-nonref")
    if not audio_decode_thread:
        argv.append("--no-audio-decode-thread")
    if profile:
        argv.append("--profile")
    if not verbose:
        argv.append("--quiet")
    epochs, seconds, profile_json = _native().convert(argv)
    result = {"output": os.fspath(output), "epochs": epochs, "seconds": seconds}
    if profile_json is not None:
        result["profile"] = json.loads(profile_json)
        if verbose:
            print(f"TENZOR_PROFILE {profile_json}", file=sys.stderr, flush=True)
    return result


def load(path: PathLike, *, copy: bool = False) -> TenzorDataset:
    """Open a ``.tenzor`` file. ``copy=False`` aliases read-only Arrow buffers: never mutate them."""
    return TenzorDataset(os.fspath(path), copy=copy)
