#!/usr/bin/env python3
"""H.265 fidelity gate: TenzorPipe's HEVC tensors against an independent FFmpeg decode.

For each clip the script decodes the selected source frames with FFmpeg, applies the
documented nearest-neighbour resize and colour conversion in float32 NumPy, and compares
against the engine's tensors. The engine's decoder is a different implementation, so this
is a genuine cross-check of decode, selection, resize and colour.

    python3 scripts/test_hevc_fidelity.py [clip.mp4 ...]

Defaults to fixtures/hevc-*.mp4 (see scripts/gen_hevc_fixtures.sh).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc

ROOT = pathlib.Path(__file__).resolve().parents[1]
BINARY = ROOT / "target/release/tenzor"
# Selection may land on a neighbouring frame only if timestamps disagree; the engine and
# FFmpeg must agree exactly, so any real mismatch shows up as a large error.
MAX_ABS = 2e-5
# Clips the engine must refuse, and the phrase its message has to contain. They keep the
# refusal paths covered next to the decode paths.
EXPECTED_REFUSALS = {
    "hevc-main10.mp4": "bit depth",
    "hevc-intra.mp4": "profile",
}


def run(args) -> bytes:
    return subprocess.check_output([str(a) for a in args], stderr=subprocess.PIPE)


def read_tenzor(path: pathlib.Path):
    reader = ipc.open_file(pa.memory_map(str(path)))
    metadata = {k.decode(): v.decode() for k, v in reader.schema.metadata.items()}
    columns: dict[str, list] = {name: [] for name in reader.schema.names}
    for i in range(reader.num_record_batches):
        batch = reader.get_batch(i)
        for name in columns:
            column = batch.column(name)
            if pa.types.is_fixed_size_list(column.type):
                columns[name].append(
                    column.values.to_numpy(zero_copy_only=True).reshape(batch.num_rows, -1)
                )
            else:
                columns[name].append(column.to_numpy(zero_copy_only=True))
    return {k: np.concatenate(v) for k, v in columns.items()}, metadata


def expected_tensors(clip: pathlib.Path, data, metadata):
    """Decode the selected frames with FFmpeg and convert them the way the engine does."""
    streams = json.loads(
        run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", clip])
    )["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    width, height = int(video["width"]), int(video["height"])
    resolution = int(metadata["video_shape"].split(",")[1])
    frames = json.loads(
        run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", clip,
            ]
        )
    )["frames"]
    pts = np.array([float(f["best_effort_timestamp_time"]) for f in frames])
    wanted = np.floor(pts * 1000 + 0.5).astype("int64")
    selected = []
    for stamp in data["video_timestamp_ms"]:
        matches = np.nonzero(wanted == stamp)[0]
        assert len(matches), f"{clip.name}: engine timestamp {stamp} ms not in the container"
        selected.append(int(matches[0]))
    unique = sorted(set(selected))
    expression = "+".join(f"eq(n,{i})" for i in unique)
    raw = run(
        [
            "ffmpeg", "-v", "error", "-i", clip, "-an", "-vf", f"select='{expression}'",
            "-vsync", "0", "-pix_fmt", "yuv420p", "-f", "rawvideo", "-threads", "1", "pipe:1",
        ]
    )
    frame_bytes = width * height * 3 // 2
    assert len(raw) == frame_bytes * len(unique), "unexpected FFmpeg frame count"
    full = "full" in metadata["video_matrix"]
    bt709 = "709" in metadata["video_matrix"]
    kr, kb = (0.2126, 0.0722) if bt709 else (0.299, 0.114)
    kg = 1 - kr - kb
    chroma_scale = np.float32(1 if full else 255 / 224)
    sy = np.arange(resolution) * (height - 1) // (resolution - 1)
    sx = np.arange(resolution) * (width - 1) // (resolution - 1)
    converted = {}
    for j, index in enumerate(unique):
        f = np.frombuffer(raw[j * frame_bytes : (j + 1) * frame_bytes], np.uint8).astype(np.float32)
        y = f[: width * height].reshape(height, width)[sy][:, sx]
        u = f[width * height : width * height * 5 // 4].reshape(height // 2, width // 2)
        v = f[width * height * 5 // 4 :].reshape(height // 2, width // 2)
        u = u[sy // 2][:, sx // 2] - np.float32(128)
        v = v[sy // 2][:, sx // 2] - np.float32(128)
        c = y if full else np.maximum(y - np.float32(16), np.float32(0)) * np.float32(255 / 219)
        r = c + np.float32(2 - 2 * kr) * v * chroma_scale
        g = (
            c
            - np.float32(2 * kb * (1 - kb) / kg) * u * chroma_scale
            - np.float32(2 * kr * (1 - kr) / kg) * v * chroma_scale
        )
        b = c + np.float32(2 - 2 * kb) * u * chroma_scale
        rgb = np.stack([r, g, b]).clip(0, 255) / np.float32(127.5) - np.float32(1)
        converted[index] = rgb.astype(np.float32)
    return np.stack([converted[i] for i in selected]), resolution


def worker_identity(clip: pathlib.Path, out_dir: pathlib.Path) -> bool:
    """Every worker count, and skipping on or off, must produce the same bytes."""
    digests = {}
    for label, extra in [
        ("N=1", ["--video-workers", "1"]),
        ("N=2", ["--video-workers", "2"]),
        ("N=4", ["--video-workers", "4"]),
        ("N=4 chunk 500ms", ["--video-workers", "4", "--chunk-target-ms", "500"]),
        ("N=2 no-skip", ["--video-workers", "2", "--no-skip-nonref"]),
        ("sequential", ["--execution", "sequential"]),
    ]:
        out = out_dir / "workers.tenzor"
        out.unlink(missing_ok=True)
        proc = subprocess.run(
            [str(BINARY), "-i", str(clip), "-o", str(out), "-q", *extra],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"FAIL {clip.name} [{label}]: {proc.stderr.strip()[:120]}")
            return False
        digests[label] = hashlib.sha256(out.read_bytes()).hexdigest()
        out.unlink(missing_ok=True)
    ok = len(set(digests.values())) == 1
    print(
        f"{'PASS' if ok else 'FAIL'} {clip.name}: identical output across "
        f"{len(digests)} decode settings"
        + ("" if ok else f" :: {digests}")
    )
    return ok


def check(clip: pathlib.Path, out_dir: pathlib.Path) -> bool:
    out = out_dir / f"{clip.stem}.tenzor"
    out.unlink(missing_ok=True)
    proc = subprocess.run(
        [str(BINARY), "-i", str(clip), "-o", str(out), "-q"], capture_output=True, text=True
    )
    expected = EXPECTED_REFUSALS.get(clip.name)
    if expected is not None:
        ok = proc.returncode != 0 and expected in proc.stderr and not out.exists()
        print(
            f"{'PASS' if ok else 'FAIL'} {clip.name}: refused as expected"
            if ok
            else f"FAIL {clip.name}: expected a refusal mentioning {expected!r}, got "
            f"rc={proc.returncode} {proc.stderr.strip()[:120]}"
        )
        out.unlink(missing_ok=True)
        return ok
    if proc.returncode != 0:
        print(f"FAIL {clip.name}: {proc.stderr.strip().splitlines()[:1]}")
        return False
    data, metadata = read_tenzor(out)
    reference, resolution = expected_tensors(clip, data, metadata)
    actual = data["video_tensor"].reshape(-1, 3, resolution, resolution)
    assert actual.shape == reference.shape, (actual.shape, reference.shape)
    delta = np.abs(actual - reference)
    worst = float(delta.max())
    ok = worst <= MAX_ABS
    print(
        f"{'PASS' if ok else 'FAIL'} {clip.name}: {len(actual)} epochs, "
        f"max |delta| {worst:.3e}, mean {float(delta.mean()):.3e} ({metadata['video_matrix']})"
    )
    out.unlink(missing_ok=True)
    return ok


def main() -> int:
    clips = [pathlib.Path(a) for a in sys.argv[1:]]
    if not clips:
        clips = sorted((ROOT / "fixtures").glob("hevc-*.mp4"))
    if not clips:
        raise SystemExit("no HEVC clips; run scripts/gen_hevc_fixtures.sh first")
    if not BINARY.exists():
        raise SystemExit(f"missing {BINARY}")
    out_dir = pathlib.Path(tempfile.mkdtemp(prefix="hevc-fidelity-"))
    results = []
    for clip in clips:
        results.append(check(clip, out_dir))
        if clip.name not in EXPECTED_REFUSALS:
            results.append(worker_identity(clip, out_dir))
    print(
        f"\n{sum(results)}/{len(results)} checks passed (FFmpeg oracle within {MAX_ABS:g}, "
        f"decode-setting identity, and the expected refusals)"
    )
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
