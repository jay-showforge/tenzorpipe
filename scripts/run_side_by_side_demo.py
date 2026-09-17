#!/usr/bin/env python3
"""Side-by-side benchmark: legacy decoders vs TenzorPipe, measured live on one clip.

Every number printed here is measured in this run, except the bit-exact parity row, which is
read from the recorded release-gate evidence (evidence/v0.3.0/skip-identity.json) and checked
against the engine binary's SHA-256. Re-run that matrix with scripts/test_skip_identity.py.

    python scripts/run_side_by_side_demo.py [--clip tests/data/benchmark_1080p.mp4]
                                            [--iters 5] [--epochs-iters 10] [--json benchmark_results.json]

Contenders
  TenzorPipe        engine ingest to Arrow IPC, then memory-mapped re-reads (CPU only)
  TorchCodec CUDA   NVDEC decode to 224x224 tensors on the GPU, re-decoded every epoch
  FFmpeg pipe       ffmpeg subprocess to raw frames into NumPy, re-decoded every epoch
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import platform
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = ROOT / "bin" / "tenzor-linux-x86_64"
EVIDENCE = ROOT / "evidence" / "v0.3.0" / "skip-identity.json"
RES, EPOCH_S = 224, 0.5

C = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m", "cyan": "\033[38;5;44m",
     "amber": "\033[38;5;214m", "red": "\033[38;5;203m", "green": "\033[38;5;77m",
     "slate": "\033[38;5;245m", "white": "\033[38;5;255m"}


def median(xs):
    return statistics.median(xs) if xs else float("nan")


# ----------------------------------------------------------------- measurement helpers

class Vram:
    """Peak GPU memory in use, sampled around a block of work (device-wide: WSL2 has no
    per-process accounting). The idle baseline is read before any CUDA context exists."""

    def __init__(self):
        self.nvml = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml, self.handle = pynvml, pynvml.nvmlDeviceGetHandleByIndex(0)
            self.idle = self.used()
        except Exception:
            self.idle = 0

    def used(self):
        if not self.nvml:
            return 0
        return self.nvml.nvmlDeviceGetMemoryInfo(self.handle).used

    def delta_mib(self):
        return max(0.0, (self.used() - self.idle) / 1048576) if self.nvml else float("nan")


def frame_indices(frames: int, fps: float, epochs: int):
    return [min(int(math.floor(k * EPOCH_S * fps + 0.5 - 1e-9)), frames - 1) for k in range(epochs)]


def probe(clip: pathlib.Path):
    info = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-count_packets", "-show_streams", "-show_format", "-of", "json", str(clip)],
        text=True))
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in v["avg_frame_rate"].split("/"))
    duration = float(info["format"]["duration"])
    return {"frames": int(v["nb_read_packets"]), "fps": num / den, "duration_s": duration,
            "width": v["width"], "height": v["height"],
            "bitrate_mbps": round(int(info["format"]["bit_rate"]) / 1e6, 2),
            "epochs": math.ceil(duration / EPOCH_S)}


# ----------------------------------------------------------------- contenders

def tenzorpipe_ingest(clip, out):
    """Engine ingest. Uses the Python package when installed, else the CLI binary."""
    out.unlink(missing_ok=True)
    try:
        import tenzorpipe as tp
        tp.ingest(clip, out)
        return "tenzorpipe python package"
    except ImportError:
        subprocess.run([str(ENGINE), "-i", str(clip), "-o", str(out), "--quiet"], check=True)
        return "tenzor binary"


def tenzorpipe_reread(path):
    """One training epoch: memory-map the artifact and materialise every batch as tensors.

    Same work as the BENCHMARKS.md re-read figure: zero-copy PyTorch views over the Arrow
    buffers, with a full checksum so every byte is actually touched.
    """
    import tenzorpipe as tp
    rows = 0
    checksum = 0.0
    with tp.load(path) as data:
        for batch in data.iter_batches():
            rows += batch["timestamp_ms"].numel()
            checksum += float(batch["video"].sum()) + float(batch["audio"].sum())
    return rows, checksum


def torchcodec_decode(clip, indices, device):
    import torch
    from torchcodec.decoders import VideoDecoder
    decoder = VideoDecoder(str(clip), device=device, dimension_order="NCHW")
    total = 0.0
    for start in range(0, len(indices), 32):
        frames = decoder.get_frames_at(indices=indices[start:start + 32]).data
        sy = torch.linspace(0, frames.shape[2] - 1, RES, device=frames.device).long()
        sx = torch.linspace(0, frames.shape[3] - 1, RES, device=frames.device).long()
        tensor = frames.index_select(2, sy).index_select(3, sx).float().div_(127.5).sub_(1.0)
        total += float(tensor.sum())
    return total


def ffmpeg_decode(clip, indices):
    import numpy as np
    select = "+".join(f"eq(n\\,{i})" for i in sorted(set(indices)))
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(clip), "-an",
           "-vf", f"select='{select}',scale={RES}:{RES}:flags=neighbor,format=rgb24",
           "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
    raw = subprocess.run(cmd, check=True, stdout=subprocess.PIPE).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, RES, RES, 3)
    return float((frames.astype(np.float32) / 127.5 - 1.0).sum())


# ----------------------------------------------------------------- terminal UI

def bar(value, best, width=34, color="cyan", invert=False):
    """Bar length is proportional to advantage: shorter time / lower memory fills less."""
    if not math.isfinite(value) or best <= 0:
        return C["slate"] + "-" * 4 + C["reset"]
    ratio = value / best if not invert else best / max(value, 1e-9)
    filled = max(1, min(width, round(width / max(ratio, 1e-9))))
    return C[color] + "█" * filled + C["dim"] + "·" * (width - filled) + C["reset"]


def render(results, clip_info, parity):
    w = C["white"]; b = C["bold"]; r = C["reset"]
    print(f"\n{b}{w}  TenzorPipe vs legacy decoders{r}  {C['slate']}"
          f"{clip_info['width']}x{clip_info['height']} @ {clip_info['fps']:.0f} fps, "
          f"{clip_info['duration_s']:.1f}s, {clip_info['bitrate_mbps']} Mb/s, "
          f"{clip_info['epochs']} epochs{r}\n")
    rows = [
        ("First-pass ingest (median)", "ingest_ms", "ms", False),
        ("Each later epoch (median)", "epoch_ms", "ms", False),
        ("GPU memory during decode", "vram_mib", "MiB", False),
        ("CPU cores used (ingest, lower is better)", "cpu_cores", "", False),
    ]
    for title, key, unit, invert in rows:
        values = {name: res[key] for name, res in results.items() if math.isfinite(res.get(key, float("nan")))}
        if not values:
            continue
        best = min(v for v in values.values() if v > 0) if any(v > 0 for v in values.values()) else 0
        print(f"{b}{w}{title}{r}")
        for name, res in results.items():
            value = res.get(key, float("nan"))
            color = "cyan" if name.startswith("TenzorPipe") else ("amber" if "FFmpeg" in name else "red")
            if not math.isfinite(value):
                shown = "n/a"
                line = C["slate"] + "-" * 4 + r
            else:
                shown = f"{value:,.1f} {unit}".strip()
                line = bar(value, best, color=color, invert=invert) if value > 0 else \
                    C["green"] + "█" + C["dim"] + "·" * 33 + r
            speed = ""
            if math.isfinite(value) and best > 0 and value > 0 and key.endswith("_ms"):
                speed = f"  {C['slate']}{value / best:>5.1f}x{r}" if value > best else f"  {C['green']}fastest{r}"
            print(f"  {name:<22} {line} {shown:>12}{speed}")
        print()
    status = f"{C['green']}PASS{r}" if parity["verified"] else f"{C['red']}UNVERIFIED{r}"
    print(f"{b}{w}Bit-exact parity{r}  {status}  {C['slate']}{parity['detail']}{r}\n")


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip", type=pathlib.Path, default=ROOT / "tests/data/benchmark_1080p.mp4")
    ap.add_argument("--iters", type=int, default=5, help="measured first-pass ingests per contender")
    ap.add_argument("--epochs-iters", type=int, default=10, help="training epochs to simulate")
    ap.add_argument("--json", type=pathlib.Path, default=ROOT / "benchmark_results.json")
    ap.add_argument("--work", type=pathlib.Path, default=pathlib.Path("/dev/shm"))
    a = ap.parse_args()
    if not a.clip.exists():
        sys.exit(f"{a.clip} not found; run scripts/generate_benchmark_assets.py first")

    clip_info = probe(a.clip)
    indices = frame_indices(clip_info["frames"], clip_info["fps"], clip_info["epochs"])
    artifact = a.work / "tenzorpipe_demo.tenzor"
    results = {}

    # ---- TenzorPipe: ingest once, then memory-mapped epochs
    api = tenzorpipe_ingest(a.clip, artifact)          # warm-up, also reports the API used
    ingest_times, cpu0 = [], os.times()
    for _ in range(a.iters):
        t0 = time.perf_counter()
        tenzorpipe_ingest(a.clip, artifact)
        ingest_times.append((time.perf_counter() - t0) * 1e3)
    cpu1 = os.times()
    wall = sum(ingest_times) / 1e3
    tenzorpipe_reread(artifact)                         # warm-up
    epoch_times = []
    for _ in range(a.epochs_iters):
        t0 = time.perf_counter()
        rows, _ = tenzorpipe_reread(artifact)
        epoch_times.append((time.perf_counter() - t0) * 1e3)
    results["TenzorPipe"] = {
        "ingest_ms": median(ingest_times), "epoch_ms": median(epoch_times), "vram_mib": 0.0,
        "cpu_cores": ((cpu1.children_user + cpu1.children_system + cpu1.user + cpu1.system)
                      - (cpu0.children_user + cpu0.children_system + cpu0.user + cpu0.system)) / max(wall, 1e-9),
        "epoch_rows": rows, "api": api, "artifact_mib": artifact.stat().st_size / 1048576,
        "note": "decoded once; later epochs memory-map Apache Arrow tensors",
    }

    # ---- TorchCodec CUDA: decode every epoch, measure GPU memory
    vram = Vram()
    try:
        import torch
        from torchcodec.decoders import VideoDecoder  # noqa: F401
        if not torch.cuda.is_available():
            raise RuntimeError("no CUDA device")
        torchcodec_decode(a.clip, indices, "cuda")     # warm-up builds the CUDA context
        peak_before = vram.delta_mib()
        times = []
        for _ in range(max(a.iters, a.epochs_iters)):
            t0 = time.perf_counter()
            torchcodec_decode(a.clip, indices, "cuda")
            times.append((time.perf_counter() - t0) * 1e3)
        results["TorchCodec CUDA"] = {
            "ingest_ms": median(times), "epoch_ms": median(times),
            "vram_mib": max(peak_before, vram.delta_mib()), "cpu_cores": float("nan"),
            "note": "GPU decode; every epoch decodes the clip again",
        }
    except Exception as exc:
        results["TorchCodec CUDA"] = {"ingest_ms": float("nan"), "epoch_ms": float("nan"),
                                      "vram_mib": float("nan"), "cpu_cores": float("nan"),
                                      "skipped": f"{type(exc).__name__}: {exc}"}

    # ---- FFmpeg subprocess pipe: decode every epoch on the CPU
    try:
        ffmpeg_decode(a.clip, indices)                 # warm-up
        times, cpu0 = [], os.times()
        for _ in range(max(a.iters, a.epochs_iters)):
            t0 = time.perf_counter()
            ffmpeg_decode(a.clip, indices)
            times.append((time.perf_counter() - t0) * 1e3)
        cpu1 = os.times()
        results["FFmpeg pipe"] = {
            "ingest_ms": median(times), "epoch_ms": median(times), "vram_mib": 0.0,
            "cpu_cores": ((cpu1.children_user + cpu1.children_system)
                          - (cpu0.children_user + cpu0.children_system)) / max(sum(times) / 1e3, 1e-9),
            "note": "CPU decode; every epoch decodes the clip again",
        }
    except Exception as exc:
        results["FFmpeg pipe"] = {"ingest_ms": float("nan"), "epoch_ms": float("nan"),
                                  "vram_mib": float("nan"), "cpu_cores": float("nan"),
                                  "skipped": f"{type(exc).__name__}: {exc}"}

    # ---- Parity: recorded 1,056-case matrix, tied to this binary
    parity = {"verified": False, "detail": "evidence not found"}
    if EVIDENCE.exists():
        import hashlib
        data = json.loads(EVIDENCE.read_text())
        binary_sha = hashlib.sha256(ENGINE.read_bytes()).hexdigest() if ENGINE.exists() else ""
        matches = binary_sha == data.get("new_sha256")
        parity = {
            "verified": bool(matches and data.get("failures") == 0),
            "cases": data.get("cases"), "failures": data.get("failures"), "kinds": data.get("kinds"),
            "binary_sha256": data.get("new_sha256"), "binary_matches_bin_dir": matches,
            "source": str(EVIDENCE.relative_to(ROOT)),
            "detail": (f"{data.get('cases')} recorded cases, {data.get('failures')} failures, "
                       f"binary {'matches' if matches else 'DIFFERS from'} bin/tenzor-linux-x86_64"),
        }

    render(results, clip_info, parity)
    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
        "clip": {**clip_info, "path": str(a.clip.relative_to(ROOT)) if a.clip.is_relative_to(ROOT) else str(a.clip)},
        "settings": {"resolution": RES, "epoch_seconds": EPOCH_S, "ingest_iterations": a.iters,
                     "epoch_iterations": a.epochs_iters},
        "results": results,
        "parity": parity,
        "measurement_notes": [
            "Latencies are medians of live runs on this host.",
            "TenzorPipe's epoch time re-reads the Arrow artifact; the others decode the clip again.",
            "GPU memory is device-wide NVML delta (WSL2 exposes no per-process accounting).",
            "Parity is read from recorded gate evidence, not re-run here.",
        ],
    }
    a.json.write_text(json.dumps(payload, indent=2))
    print(f"{C['slate']}metrics written to {a.json}{C['reset']}")


if __name__ == "__main__":
    main()
