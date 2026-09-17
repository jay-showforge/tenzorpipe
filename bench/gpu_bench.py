#!/usr/bin/env python3
"""TenzorPipe vs NVIDIA DALI vs TorchCodec/torchvision vs FFmpeg-subprocess ingestion benchmark.

Every contender performs the same job so latencies are comparable:

    1080p H.264 MP4 -> the source frame nearest each 0.5 s epoch start
                    -> 224x224 nearest-neighbour (aligned corners, square stretch)
                    -> float32 RGB CHW in [-1, 1]
    (A/V scenario)  -> plus 16 kHz mono 64-band HTK Log-Mel, 400-sample Hann, 160 hop,
                       50 rows per epoch -- when the library can decode AAC.

Tensors land where the library naturally leaves them (host for TenzorPipe/FFmpeg/CPU
decoders, GPU for DALI/TorchCodec-CUDA); a checksum over every output tensor ends each
iteration and forces GPU synchronisation.

Each contender runs in a fresh worker process: imports, pipeline/decoder construction and
warm-up iterations are excluded from latency, then --iters iterations are measured.
Latency is the completion-to-completion interval inside the measured loop, so work a
library prefetches between calls (DALI's async executor) is still counted.

Usage (inside WSL2/Linux, after `bash bench/setup_wsl.sh` and `. ~/.tenzor-bench/env.sh`):
    python bench/gpu_bench.py                  # prepare clips, run everything, write BENCHMARKS.md
    python bench/gpu_bench.py --iters 10 --only tenzorpipe-w1,dali-nvdec
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH_HOME = Path(os.environ.get("BENCH_HOME", Path.home() / ".tenzor-bench"))
RELEASE_BIN = ROOT / "bin" / "tenzor-linux-x86_64"  # untouched v0.2.0 release binary
# Binary under test; --tenzor-bin overrides (worker processes inherit it via this variable).
TENZOR_BIN = Path(os.environ.get("TENZOR_BIN", RELEASE_BIN))
RES = 224
EPOCH_S = 0.5
MEL_ROWS = 50

# name -> (description, device that decodes, supports AAC audio)
CONTENDERS = {
    "tenzorpipe-default": ("TenzorPipe under test, no worker flag (CLI default)", "CPU (OpenH264)", True),
    "tenzorpipe-w1": ("TenzorPipe under test, --video-workers 1", "CPU (OpenH264)", True),
    "tenzorpipe-w6": ("TenzorPipe under test, --video-workers 6", "CPU (OpenH264)", True),
    "tenzorpipe-auto": ("TenzorPipe under test, --video-workers 0", "CPU (OpenH264)", True),
    "tenzorpipe-v020-auto": ("Unmodified v0.2.0 release binary, --video-workers 0", "CPU (OpenH264)", True),
    "dali-nvdec": ("NVIDIA DALI fn.readers.video(device='gpu')", "GPU (NVDEC)", False),
    "torchcodec-cuda": ("TorchCodec VideoDecoder(device='cuda') + GPU Log-Mel", "GPU (NVDEC)", True),
    "torchcodec-cpu": ("TorchCodec VideoDecoder(device='cpu')", "CPU (libavcodec)", True),
    "torchvision-read_video": ("torchvision.io.read_video (whole clip into RAM)", "CPU (PyAV)", False),
    "ffmpeg-pipe": ("ffmpeg subprocess -> rawvideo/f32le pipes -> NumPy", "CPU (libavcodec)", True),
}


# ----------------------------------------------------------------------------- clips

def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw).stdout


def prepare_clips(seconds: int, force: bool) -> dict:
    clip_dir = BENCH_HOME / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    av = clip_dir / f"synthetic-1080p30-h264-aac-{seconds}s.mp4"
    vo = clip_dir / f"synthetic-1080p30-h264-{seconds}s-video-only.mp4"
    if force or not av.exists():
        print(f"[prepare] encoding {av.name} (libx264 High, B-frames, 2 s GOP, BT.709, AAC 48 kHz stereo)")
        # testsrc2 + temporal noise keeps the bitrate near real camera footage (~8 Mb/s) instead of
        # the few hundred kb/s a static test pattern compresses to, which would flatter every decoder.
        sh(["ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={seconds},noise=alls=10:allf=t+u",
            "-f", "lavfi", "-i", f"aevalsrc=0.4*sin(2*PI*(220+40*t)*t)|0.4*sin(2*PI*330*t)+0.05*(random(0)-0.5):s=48000:d={seconds}",
            "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-b:v", "8M", "-maxrate", "10M", "-bufsize", "16M", "-bf", "3", "-g", "60", "-keyint_min", "60",
            "-sc_threshold", "0", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-color_range", "tv", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(av)])
    if force or not vo.exists():
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(av), "-map", "0:v", "-c", "copy", "-movflags", "+faststart", str(vo)])

    clips = {}
    for scenario, path in (("video", vo), ("av", av)):
        probe = json.loads(sh(["ffprobe", "-v", "error", "-count_packets", "-show_streams", "-show_format", "-of", "json", str(path)]))
        v = next(s for s in probe["streams"] if s["codec_type"] == "video")
        # Ask TenzorPipe how many epochs the clip has (movie duration incl. AAC tail); everyone matches it.
        out = Path("/dev/shm") / f"tzb-prepare-{os.getpid()}.tenzor"
        out.unlink(missing_ok=True)
        sh([str(TENZOR_BIN), "-i", str(path), "-o", str(out)])
        sys.path.insert(0, str(ROOT / "python"))
        from tenzor import TenzorDataset
        with TenzorDataset(str(out)) as ds:
            epochs = len(ds)
        out.unlink()
        clips[scenario] = {
            "path": str(path), "width": v["width"], "height": v["height"],
            "frames": int(v["nb_read_packets"]), "fps": v["avg_frame_rate"], "profile": v.get("profile"),
            "bit_rate_kbps": round(int(probe["format"]["bit_rate"]) / 1000),
            "duration_s": float(probe["format"]["duration"]), "epochs": epochs,
            "has_audio": any(s["codec_type"] == "audio" for s in probe["streams"]),
        }
    return clips


# ----------------------------------------------------------------------------- shared math

def frame_indices(clip):
    """Nearest-presentation-frame index for each epoch start (CFR clip, earlier frame wins ties)."""
    num, den = (int(x) for x in clip["fps"].split("/"))
    fps = num / den
    return [min(int(math.floor(k * EPOCH_S * fps + 0.5 - 1e-9)), clip["frames"] - 1) for k in range(clip["epochs"])]


def aligned_nearest(n_in, n_out=RES):
    return [i * (n_in - 1) // (n_out - 1) for i in range(n_out)]


def mel_filterbank_np():
    import numpy as np
    points = 700 * (10 ** (np.linspace(0, 2595 * np.log10(1 + 8000 / 700), 66) / 2595) - 1) * 400 / 16000
    k = np.arange(201)[None, :]
    l, c, r = (points[i:i + 64, None] for i in range(3))
    return np.maximum(0, np.minimum((k - l) / (c - l), (r - k) / (r - c))).astype("float32")


def log_mel_numpy(pcm, epochs):
    import numpy as np
    need = epochs * 8000 + 400
    x = np.zeros(need, np.float32)
    x[:min(len(pcm), need)] = pcm[:need]
    frames = np.lib.stride_tricks.sliding_window_view(x, 400)[::160][:epochs * MEL_ROWS] * np.hanning(400).astype("float32")
    power = (np.abs(np.fft.rfft(frames)) ** 2).astype("float32")
    return np.log10(power @ mel_filterbank_np().T + 1e-10).reshape(epochs, MEL_ROWS, 64)


def log_mel_torch(pcm, epochs):
    import numpy as np
    import torch
    dev = pcm.device
    need = epochs * 8000 + 400
    x = torch.zeros(need, dtype=torch.float32, device=dev)
    n = min(pcm.numel(), need)
    x[:n] = pcm[:n]
    frames = x.unfold(0, 400, 160)[:epochs * MEL_ROWS] * torch.from_numpy(np.hanning(400).astype("float32")).to(dev)
    power = torch.fft.rfft(frames).abs().pow(2)
    fb = torch.from_numpy(mel_filterbank_np()).to(dev)
    return torch.log10(power @ fb.T + 1e-10).view(epochs, MEL_ROWS, 64)


# ----------------------------------------------------------------------------- measurement

class Sampler(threading.Thread):
    """Samples process-tree RSS (worker + children, e.g. tenzor/ffmpeg) and NVML device memory."""

    def __init__(self, interval=0.005):
        super().__init__(daemon=True)
        import psutil
        self.psutil = psutil
        self.proc = psutil.Process()
        self.exe = self.proc.exe()
        self.interval = interval
        self.stop_evt = threading.Event()
        self.lock = threading.Lock()
        self.tree_peak = 0
        self.child_peak = 0
        self.vram_peak = 0
        self.nvml = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml = pynvml
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            pass

    def vram_used(self):
        return self.nvml.nvmlDeviceGetMemoryInfo(self.handle).used if self.nvml else 0

    def reset(self):
        with self.lock:
            self.tree_peak = self.child_peak = self.vram_peak = 0

    def sample(self):
        try:
            own = self.proc.memory_info().rss
        except self.psutil.Error:
            return
        kids = 0
        for c in self.proc.children(recursive=True):
            try:
                if c.exe() == self.exe:  # pre-exec fork/vfork of this worker: same pages, not a real child
                    continue
                kids += c.memory_info().rss
            except self.psutil.Error:
                pass
        vram = self.vram_used()
        with self.lock:
            self.tree_peak = max(self.tree_peak, own + kids)
            self.child_peak = max(self.child_peak, kids)
            self.vram_peak = max(self.vram_peak, vram)

    def run(self):
        while not self.stop_evt.is_set():
            self.sample()
            time.sleep(self.interval)


def cpu_seconds():
    s, c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return s.ru_utime + s.ru_stime + c.ru_utime + c.ru_stime


def mib(b):
    return b / 1048576


# ----------------------------------------------------------------------------- contenders

def make_contender(name, clip, scenario, tmp):
    """Return (setup_fn -> run_fn). run_fn() -> (video [E,3,224,224], audio [E,50,64] | None, extra timings)."""
    want_audio = scenario == "av"
    idx = frame_indices(clip)
    E = clip["epochs"]
    sy, sx = aligned_nearest(clip["height"]), aligned_nearest(clip["width"])

    if name.startswith("tenzorpipe"):
        workers = {"tenzorpipe-default": None, "tenzorpipe-w1": 1, "tenzorpipe-w6": 6,
                   "tenzorpipe-auto": 0, "tenzorpipe-v020-auto": 0}[name]
        binary = RELEASE_BIN if name == "tenzorpipe-v020-auto" else TENZOR_BIN
        sys.path.insert(0, str(ROOT / "python"))
        import torch  # noqa: F401  (loader dependency; CPU only, no CUDA context is created)
        from tenzor import TenzorDataset
        out = Path(tmp) / f"tzb-{os.getpid()}.tenzor"
        cmd = [str(binary), "-i", clip["path"], "-o", str(out)]
        if workers is not None:
            cmd += ["--video-workers", str(workers)]

        def run():
            out.unlink(missing_ok=True)
            t0 = time.perf_counter()
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            t1 = time.perf_counter()
            with TenzorDataset(str(out)) as ds:
                vids, auds = [], []
                for b in ds.iter_batches():
                    vids.append(b["video"])
                    auds.append(b["audio"])
                video = torch.cat(vids) if len(vids) > 1 else vids[0]
                audio = (torch.cat(auds) if len(auds) > 1 else auds[0]) if want_audio else None
                if len(vids) == 1:  # keep tensors valid after the mmap closes
                    video = video.clone()
                    audio = audio.clone() if audio is not None else None
            return video, audio, {"engine_cli_ms": (t1 - t0) * 1e3, "arrow_load_ms": (time.perf_counter() - t1) * 1e3}

        def profile():
            out.unlink(missing_ok=True)
            r = subprocess.run(cmd + ["--profile"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            line = next((l for l in r.stderr.splitlines() if "PROFILE {" in l), None)
            return json.loads(line[line.index("{"):]) if line else None

        run.profile = profile
        return run

    if name == "ffmpeg-pipe":
        import numpy as np
        import torch
        n_sel = len(idx)
        # select=eq(n,..) keeps exactly the epoch frames; swscale does 1080p->224 NN + BT.709->RGB in one pass.
        sel = "+".join(f"eq(n\\,{i})" for i in sorted(set(idx)))
        vcmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", clip["path"], "-an",
                "-vf", f"select='{sel}',scale={RES}:{RES}:flags=neighbor:in_color_matrix=auto,format=rgb24",
                "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
        acmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", clip["path"], "-vn", "-af", "pan=mono|c0=0.5*c0+0.5*c1", "-ar", "16000", "-f", "f32le", "pipe:1"]
        uniq = sorted(set(idx))
        pos = [uniq.index(i) for i in idx]

        def run():
            pa = subprocess.Popen(acmd, stdout=subprocess.PIPE) if want_audio else None
            box = {}
            if pa:
                th = threading.Thread(target=lambda: box.__setitem__("pcm", pa.stdout.read()))
                th.start()
            raw = subprocess.run(vcmd, check=True, stdout=subprocess.PIPE).stdout
            frames = np.frombuffer(raw, np.uint8).reshape(-1, RES, RES, 3)
            assert len(frames) == len(uniq), (len(frames), len(uniq))
            video = torch.from_numpy((frames[pos].transpose(0, 3, 1, 2).astype(np.float32) / 127.5) - 1.0)
            audio = None
            if pa:
                th.join()
                assert pa.wait() == 0
                audio = torch.from_numpy(log_mel_numpy(np.frombuffer(box["pcm"], "<f4"), E))
            return video, audio, {}
        _ = n_sel
        return run

    if name.startswith("torchcodec"):
        import torch
        from torchcodec.decoders import AudioDecoder, VideoDecoder
        device = "cuda" if name.endswith("cuda") else "cpu"
        ty, tx = torch.tensor(sy, device=device), torch.tensor(sx, device=device)
        uniq = sorted(set(idx))
        pos = torch.tensor([uniq.index(i) for i in idx], device=device)

        def run():
            dec = VideoDecoder(clip["path"], device=device, dimension_order="NCHW")
            parts = []
            for s in range(0, len(uniq), 32):  # 32-frame batches bound the 1080p uint8 working set
                fr = dec.get_frames_at(indices=uniq[s:s + 32]).data
                parts.append(fr.index_select(2, ty).index_select(3, tx).float().div_(127.5).sub_(1.0))
            video = torch.cat(parts).index_select(0, pos)
            audio = None
            if want_audio:
                ad = AudioDecoder(clip["path"], sample_rate=16000)
                pcm = ad.get_all_samples().data.to(device).mean(0)
                audio = log_mel_torch(pcm, E)
            return video, audio, {}
        return run

    if name == "torchvision-read_video":
        import torch
        import torchvision
        from torchvision.io import read_video
        bytes_needed = clip["frames"] * clip["width"] * clip["height"] * 3
        import psutil
        if bytes_needed > 0.4 * psutil.virtual_memory().total:
            raise RuntimeError(f"read_video would materialise {mib(bytes_needed):.0f} MiB of 1080p frames")
        ty, tx, sel = torch.tensor(sy), torch.tensor(sx), torch.tensor(idx)

        def run():
            frames, _, _ = read_video(clip["path"], pts_unit="sec", output_format="TCHW")
            video = frames.index_select(0, sel.clamp(max=len(frames) - 1)).index_select(2, ty).index_select(3, tx)
            return video.float().div_(127.5).sub_(1.0), None, {}
        run.version = torchvision.__version__
        return run

    if name == "dali-nvdec":
        import torch
        from nvidia.dali import fn, pipeline_def, types
        from nvidia.dali.plugin.pytorch import feed_ndarray
        num, den = (int(x) for x in clip["fps"].split("/"))
        step = round(EPOCH_S * num / den)  # CFR: frame k*step is the nearest frame to epoch k
        n = len(range(0, clip["frames"], step))

        @pipeline_def(batch_size=n, num_threads=4, device_id=0)
        def pipe():
            v = fn.readers.video(device="gpu", filenames=[clip["path"]], sequence_length=1, step=step,
                                 random_shuffle=False, pad_last_batch=True, initial_fill=n, name="reader",
                                 image_type=types.RGB, dtype=types.UINT8, skip_vfr_check=True)
            v = fn.resize(v, resize_x=RES, resize_y=RES, interp_type=types.INTERP_NN, antialias=False)
            return fn.crop_mirror_normalize(v, dtype=types.FLOAT, output_layout="FCHW",
                                            mean=[127.5] * 3, std=[127.5] * 3)
        p = pipe()
        p.build()
        pad = torch.tensor([min(k, n - 1) for k in range(E)], device="cuda")

        def run():
            (out,) = p.run()
            t = out.as_tensor()
            dst = torch.empty(t.shape(), dtype=torch.float32, device="cuda")
            feed_ndarray(t, dst, cuda_stream=torch.cuda.current_stream())
            return dst.view(n, 3, RES, RES).index_select(0, pad), None, {}
        return run

    raise ValueError(name)


def worker(args):
    spec = json.loads(args.spec)
    name, scenario, clip = spec["contender"], spec["scenario"], spec["clip"]
    sampler = Sampler()
    vram_idle = sampler.vram_used()
    rss_start = sampler.proc.memory_info().rss
    sampler.start()
    result = {"contender": name, "scenario": scenario, "iters": spec["iters"], "warmup": spec["warmup"]}
    uses_cuda = name in ("dali-nvdec", "torchcodec-cuda")
    try:
        t_setup = time.perf_counter()
        run = make_contender(name, clip, scenario, spec["tmp"])
        import torch
        if uses_cuda:
            torch.cuda.init()
        if hasattr(run, "version"):
            result["library_version"] = run.version

        def iteration():
            video, audio, extra = run()
            checksum = float(video.sum())  # also synchronises CUDA work
            if audio is not None:
                checksum += float(audio.sum())
            return video, audio, extra, checksum

        video, audio, _, _ = iteration()  # first call is the cold one: count it in setup
        result["setup_plus_first_s"] = time.perf_counter() - t_setup
        for _ in range(spec["warmup"] - 1):
            video, audio, _, _ = iteration()
        if hasattr(run, "profile"):
            result["engine_profile"] = run.profile()

        # Shape/value checks plus a saved copy for the cross-contender agreement check.
        E = clip["epochs"]
        assert tuple(video.shape) == (E, 3, RES, RES), video.shape
        assert bool(torch.isfinite(video).all()) and float(video.min()) >= -1.001 and float(video.max()) <= 1.001
        tensors = BENCH_HOME / "tensors"
        tensors.mkdir(parents=True, exist_ok=True)
        import numpy as np
        np.save(tensors / f"{scenario}-{name}-video.npy", video.detach().cpu().to(torch.float16).numpy())
        if audio is not None:
            assert tuple(audio.shape) == (E, MEL_ROWS, 64), audio.shape
            np.save(tensors / f"{scenario}-{name}-audio.npy", audio.detach().cpu().numpy())
        del video, audio

        import gc
        gc.collect()
        if uses_cuda:
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
        rss_ready = sampler.proc.memory_info().rss
        sampler.reset()
        cpu0, lat, extras = cpu_seconds(), [], {}
        loop_start = prev = time.perf_counter()
        for _ in range(spec["iters"]):
            _, _, extra, _ = iteration()
            now = time.perf_counter()
            lat.append((now - prev) * 1e3)
            prev = now
            for k, v in extra.items():
                extras.setdefault(k, []).append(v)
        wall = time.perf_counter() - loop_start
        cpu = cpu_seconds() - cpu0
        sampler.sample()
        sampler.stop_evt.set()
        sampler.join()

        own_max = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        lat_sorted = sorted(lat)
        pct = lambda q: lat_sorted[min(len(lat_sorted) - 1, int(round(q * (len(lat_sorted) - 1))))]
        med = statistics.median(lat)
        result.update({
            "status": "ok",
            "latency_ms": {"median": med, "mean": statistics.fmean(lat), "p5": pct(0.05), "p95": pct(0.95),
                           "min": lat_sorted[0], "max": lat_sorted[-1], "stdev": statistics.pstdev(lat)},
            "latencies_ms": lat,
            "source_fps": clip["frames"] * 1e3 / med,
            "epochs_per_s": clip["epochs"] * 1e3 / med,
            "throughput_source_fps_total": clip["frames"] * spec["iters"] / wall,
            "throughput_epochs_per_s_total": clip["epochs"] * spec["iters"] / wall,
            "cpu_cores": cpu / wall,
            "rss_start_mib": mib(rss_start),
            "rss_ready_mib": mib(rss_ready),
            "rss_peak_measured_mib": mib(sampler.tree_peak),
            "rss_peak_lifetime_mib": mib(max(own_max, sampler.tree_peak)),
            "child_rss_peak_mib": mib(sampler.child_peak) if name.startswith(("tenzorpipe", "ffmpeg")) else None,
            "vram_torch_peak_mib": mib(torch.cuda.max_memory_allocated()) if uses_cuda else 0.0,
            "vram_torch_reserved_mib": mib(torch.cuda.max_memory_reserved()) if uses_cuda else 0.0,
            "vram_nvml_delta_mib": max(0.0, mib(sampler.vram_peak - vram_idle)) if sampler.nvml else None,
            "extras_median_ms": {k: statistics.median(v) for k, v in extras.items()},
        })
    except Exception as exc:  # report, don't crash the suite
        import traceback
        sampler.stop_evt.set()
        result.update({"status": "skipped", "reason": f"{type(exc).__name__}: {exc}".strip()[:400],
                       "traceback": traceback.format_exc()[-2000:]})
    print("@@RESULT@@" + json.dumps(result))


# ----------------------------------------------------------------------------- reporting

def agreement(scenario, name, ref="tenzorpipe-w1"):
    import numpy as np
    t = BENCH_HOME / "tensors"
    out = {}
    for kind in ("video", "audio"):
        a, b = t / f"{scenario}-{name}-{kind}.npy", t / f"{scenario}-{ref}-{kind}.npy"
        if a.exists() and b.exists():
            x, y = np.load(a).astype("float32"), np.load(b).astype("float32")
            if x.shape == y.shape:
                out[kind] = float(np.abs(x - y).mean())
    return out


def fmt(v, spec=".0f", none="—"):
    return none if v is None else format(v, spec)


def build_rows(results, clips):
    rows = []
    for r in results:
        desc, dev, has_audio = CONTENDERS[r["contender"]]
        if r["status"] != "ok":
            rows.append({"scenario": r["scenario"], "name": r["contender"], "skipped": r["reason"]})
            continue
        agree = agreement(r["scenario"], r["contender"])
        audio = "n/a" if r["scenario"] == "video" else ("Log-Mel" if has_audio else "none (unsupported)")
        L = r["latency_ms"]
        rows.append({
            "scenario": r["scenario"], "name": r["contender"], "device": dev, "audio": audio,
            "lat": f"{L['median']:.1f}", "lat_range": f"{L['p5']:.1f}–{L['p95']:.1f}",
            "fps": f"{r['throughput_source_fps_total']:.0f}", "eps": f"{r['throughput_epochs_per_s_total']:.1f}",
            "rss": f"{r['rss_peak_measured_mib']:.0f}", "ws": f"{max(0.0, r['rss_peak_measured_mib'] - r['rss_ready_mib']):.0f}", "rss_life": f"{r['rss_peak_lifetime_mib']:.0f}",
            "child": fmt(r["child_rss_peak_mib"]),
            "vram_t": fmt(r["vram_torch_peak_mib"]) if r["vram_torch_peak_mib"] else "0",
            "vram_n": fmt(r["vram_nvml_delta_mib"]) if r["contender"] in ("dali-nvdec", "torchcodec-cuda") else "0 (no CUDA)",
            "cpu": f"{r['cpu_cores']:.2f}", "setup": f"{r['setup_plus_first_s']:.2f}",
            "mae_v": "ref" if r["contender"] == "tenzorpipe-w1" else fmt(agree.get("video"), ".4f"),
            "mae_a": ("ref" if r["contender"] == "tenzorpipe-w1" and r["scenario"] == "av" else fmt(agree.get("audio"), ".3f")),
        })
    return rows


COLS = [("name", "Contender"), ("device", "Decode"), ("audio", "Audio"), ("lat", "Latency ms (median)"),
        ("lat_range", "p5–p95 ms"), ("fps", "Source FPS"), ("eps", "Epochs/s"), ("rss", "Peak RSS MiB"), ("ws", "RSS growth MiB"),
        ("child", "Child RSS MiB"), ("vram_t", "VRAM torch MiB"), ("vram_n", "VRAM NVML Δ MiB"),
        ("cpu", "CPU cores"), ("setup", "Setup+cold s"), ("mae_v", "Video MAE"), ("mae_a", "Mel MAE")]


def terminal_table(rows, scenario):
    sel = [r for r in rows if r["scenario"] == scenario]
    ok = [r for r in sel if "skipped" not in r]
    widths = {k: max(len(h), *(len(str(r[k])) for r in ok)) if ok else len(h) for k, h in COLS}
    line = "  ".join(h.ljust(widths[k]) for k, h in COLS)
    print("\n" + line + "\n" + "-" * len(line))
    for r in ok:
        print("  ".join(str(r[k]).ljust(widths[k]) for k, _ in COLS))
    for r in sel:
        if "skipped" in r:
            print(f"{r['name']}: SKIPPED — {r['skipped']}")


def md_table(rows, scenario):
    sel = [r for r in rows if r["scenario"] == scenario]
    out = ["| " + " | ".join(h for _, h in COLS) + " |", "|" + "|".join("---" if k in ("name", "device", "audio") else "---:" for k, _ in COLS) + "|"]
    for r in sel:
        if "skipped" in r:
            out.append(f"| {r['name']} | *skipped:* {r['skipped'].split(' (/')[0].replace('|', '/')} |" + " |" * (len(COLS) - 2))
        else:
            out.append("| " + " | ".join(f"**{r[k]}**" if k == "name" and r[k].startswith("tenzorpipe") else str(r[k]) for k, _ in COLS) + " |")
    return "\n".join(out)


def environment():
    info = {"os": platform.platform(), "python": platform.python_version(), "cpu": None, "logical_cpus": os.cpu_count()}
    try:
        info["cpu"] = next(l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name"))
    except Exception:
        pass
    try:
        info["gpu"] = sh(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]).strip()
    except Exception:
        info["gpu"] = None
    try:
        import psutil
        info["ram_gib"] = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:
        pass
    for mod in ("torch", "torchvision", "torchcodec", "nvidia.dali", "numpy", "pyarrow"):
        try:
            m = __import__(mod, fromlist=["__version__"])
            info[mod] = getattr(m, "__version__", "?")
        except Exception:
            info[mod] = None
    try:
        info["ffmpeg"] = sh(["ffmpeg", "-hide_banner", "-version"]).splitlines()[0]
    except Exception:
        info["ffmpeg"] = None
    try:
        import torch
        info["torch_cuda"] = torch.version.cuda
    except Exception:
        pass
    info["tenzor_bin"] = str(TENZOR_BIN)
    info["tenzor_sha256"] = sh(["sha256sum", str(TENZOR_BIN)]).split()[0]
    info["release_sha256"] = sh(["sha256sum", str(RELEASE_BIN)]).split()[0]
    return info


def write_markdown(path, rows, results, clips, env, args):
    prof = {(r["scenario"], r["contender"]): r["engine_profile"] for r in results
            if r["contender"].startswith("tenzorpipe") and r.get("engine_profile")}
    splits = {(r["scenario"], r["contender"]): r["extras_median_ms"] for r in results
              if r.get("extras_median_ms")}
    md = [
        "# BENCHMARKS — TenzorPipe vs DALI vs TorchCodec vs FFmpeg (1080p ingestion)",
        "",
        f"Generated {time.strftime('%Y-%m-%d %H:%M')} by `bench/gpu_bench.py` — "
        f"{args.warmup} warm-up + **{args.iters} measured iterations** per contender, each contender in a fresh process.",
        "",
        "## Task (identical for every contender)",
        "",
        "1080p30 H.264 MP4 → source frame nearest each 0.5 s epoch start → 224×224 nearest-neighbour "
        "(aligned corners) → float32 RGB CHW in [-1, 1]. In the **A/V** scenario, contenders that can decode AAC "
        "also produce the 64-band Log-Mel (16 kHz mono, 400-sample Hann, 160 hop, 50 rows/epoch). "
        "An iteration ends when every output tensor has been checksummed (forces CUDA sync). "
        "TenzorPipe's iteration = run the CLI to `/dev/shm` **and** load the Arrow file into PyTorch tensors.",
        "",
        "| Clip | Resolution | Frames | Bitrate | Profile | Epochs |",
        "|---|---|---:|---:|---|---:|",
    ]
    for sc, c in clips.items():
        md.append(f"| `{Path(c['path']).name}` ({sc}) | {c['width']}×{c['height']} @ {c['fps']} | {c['frames']} | "
                  f"{c['bit_rate_kbps']} kb/s | {c['profile']} | {c['epochs']} |")
    for sc, title in (("video", "Scenario 1 — video only (all contenders)"), ("av", "Scenario 2 — video + AAC audio")):
        if any(r["scenario"] == sc for r in rows):
            md += ["", f"## {title}", "", md_table(rows, sc)]
    md += ["", "### Column definitions", "",
           "- **Latency** — completion-to-completion time per clip in the measured loop (p5–p95 alongside).",
           "- **Source FPS** / **Epochs/s** — measured-loop totals (iterations × frames ÷ loop wall), robust to DALI's bimodal async latencies. All decoders must decode "
           "every frame (P/B-frame dependencies) even though only one per 0.5 s is kept. **Epochs/s** counts output rows.",
           "- **Peak RSS** — sampled every 5 ms over the measured loop: worker Python process + child processes "
           "(tenzor / ffmpeg). Includes each library's resident baseline (torch, DALI, CUDA context). "
           "**RSS growth** = peak minus RSS after imports/pipeline setup (the per-clip working set). **Child RSS** is the sampled engine subprocess alone (children's `ru_maxrss` is unusable: it keeps the forking parent's size across exec).",
           "- **VRAM torch** — `torch.cuda.max_memory_allocated()` (PyTorch caching allocator only; DALI's and NVDEC's "
           "own pools are invisible to it). **VRAM NVML Δ** — peak device memory minus the idle reading before the "
           "worker imported anything; it captures CUDA context + NVDEC surfaces + DALI pools, but is device-wide, so "
           "Windows desktop activity adds noise (up to ~100 MiB observed), so CPU-only contenders, which never create a CUDA context, are shown as 0. WSL2 does not expose per-process NVML accounting.",
           "- **CPU cores** — (user+sys CPU of worker and children) ÷ loop wall time.",
           "- **Setup+cold** — imports, decoder/pipeline construction and the first (cold) iteration.",
           "- **Video/Mel MAE** — mean absolute difference from `tenzorpipe-w1` tensors (video in [-1,1] units; Mel in "
           "log10 units). Small values confirm contenders did equivalent work; `bench/check_dali_alignment.py` confirms DALI returns exactly the expected frame for every epoch; differences come from colour-matrix "
           "rounding, resize sampling, and each library's 48→16 kHz resampler.",
           ]
    if prof or splits:
        md += ["", "## TenzorPipe internals (for bottleneck analysis)", ""]
        for (sc, name), s in sorted(splits.items()):
            if name.startswith("tenzorpipe") and s:
                md.append(f"- `{name}` / {sc}: engine CLI {s.get('engine_cli_ms', 0):.1f} ms + Arrow→torch load "
                          f"{s.get('arrow_load_ms', 0):.1f} ms (medians)")
        if prof:
            keys = sorted(prof)
            stages = sorted({k for p in prof.values() for k in p.get("stages_seconds", {})},
                            key=lambda k: -max(p.get("stages_seconds", {}).get(k, 0) for p in prof.values()))
            md += ["", "`--profile` from one warm run per variant — wall-clock stage timers overlap across threads, "
                   "so they are not additive; `*_wait` rows are idle time.", "",
                   "| Stage (s) | " + " | ".join(f"{n} / {sc}" for sc, n in keys) + " |",
                   "|---|" + "---:|" * len(keys)]
            for st in stages:
                vals = [prof[k].get("stages_seconds", {}).get(st, 0) for k in keys]
                if max(vals) >= 0.0005:
                    md.append(f"| {st} | " + " | ".join(f"{v:.3f}" for v in vals) + " |")
            md.append("| **wall** | " + " | ".join(f"**{prof[k].get('wall_seconds', 0):.3f}**" for k in keys) + " |")
    md += ["", "## Environment", "", "```json", json.dumps(env, indent=2), "```", "",
           "Raw per-iteration latencies: `bench/results/latest.json`. Reproduce inside WSL2/Linux:", "",
           "```sh", "bash bench/setup_wsl.sh && . ~/.tenzor-bench/env.sh",
           "cargo build --release --locked && mkdir -p target/release  # CARGO_TARGET_DIR may differ",
           f"python bench/gpu_bench.py --iters {args.iters} --warmup {args.warmup} --seconds {args.seconds}"
           + (f" --tenzor-bin {os.path.relpath(TENZOR_BIN, ROOT)}" if TENZOR_BIN != RELEASE_BIN else ""), "```", "",
           "<!-- ANALYSIS -->", ""]
    old = path.read_text() if path.exists() else ""
    if "<!-- ANALYSIS -->" in old:  # keep hand-written analysis across reruns
        md.append(old.split("<!-- ANALYSIS -->", 1)[1].lstrip("\n"))
    path.write_text("\n".join(md))


# ----------------------------------------------------------------------------- orchestration

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("worker")
    w.add_argument("--spec", required=True)
    ap.add_argument("--iters", type=int, default=50)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--seconds", type=int, default=20, help="clip duration")
    ap.add_argument("--scenarios", default="video,av")
    ap.add_argument("--only", default=",".join(CONTENDERS))
    ap.add_argument("--tmp", default="/dev/shm", help="TenzorPipe output dir (tmpfs keeps disk I/O out of the timing)")
    ap.add_argument("--regen-clips", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "BENCHMARKS.md"))
    ap.add_argument("--tenzor-bin", help="TenzorPipe binary under test (default: bin/tenzor-linux-x86_64)")
    ap.add_argument("--merge", action="store_true",
                    help="keep other contenders' results from bench/results/latest.json and replace only those re-run")
    args = ap.parse_args()
    if args.cmd == "worker":
        return worker(args)
    global TENZOR_BIN
    if args.tenzor_bin:
        TENZOR_BIN = Path(args.tenzor_bin).resolve()
        os.environ["TENZOR_BIN"] = str(TENZOR_BIN)

    if not TENZOR_BIN.exists() or not shutil.which("ffmpeg"):
        sys.exit("need bin/tenzor-linux-x86_64 and ffmpeg on PATH (run bench/setup_wsl.sh, then . ~/.tenzor-bench/env.sh)")
    for binary in {TENZOR_BIN, RELEASE_BIN}:
        os.chmod(binary, 0o755)
    clips = prepare_clips(args.seconds, args.regen_clips)
    for sc, c in clips.items():
        print(f"[clip] {sc}: {Path(c['path']).name} {c['width']}x{c['height']} {c['frames']} frames "
              f"{c['bit_rate_kbps']} kb/s, {c['epochs']} epochs")

    results_dir = ROOT / "bench" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    results = []
    previous = []
    if args.merge and (results_dir / "latest.json").exists():
        previous = json.loads((results_dir / "latest.json").read_text())["results"]
    order = [n for n in CONTENDERS if n in args.only.split(",")]
    for sc in args.scenarios.split(","):
        # tenzorpipe-w1 first: it is the agreement reference.
        for name in order:
            spec = {"contender": name, "scenario": sc, "clip": clips[sc], "iters": args.iters,
                    "warmup": max(1, args.warmup), "tmp": args.tmp}
            print(f"[run] {sc:5s} {name:24s} ...", end="", flush=True)
            t0 = time.time()
            p = subprocess.run([sys.executable, __file__, "worker", "--spec", json.dumps(spec)],
                               capture_output=True, text=True)
            line = next((l for l in p.stdout.splitlines() if l.startswith("@@RESULT@@")), None)
            if line is None:
                r = {"contender": name, "scenario": sc, "status": "skipped",
                     "reason": f"worker exited {p.returncode}: {(p.stderr.strip().splitlines() or ['?'])[-1][:300]}"}
            else:
                r = json.loads(line[len("@@RESULT@@"):])
            results.append(r)
            if r["status"] == "ok":
                print(f" {r['latency_ms']['median']:8.1f} ms  ({time.time() - t0:.0f}s)")
            else:
                print(f" skipped: {r['reason'][:150]}")
            time.sleep(2)  # let the GPU/allocator settle between contenders

    rerun = {(r["scenario"], r["contender"]) for r in results}
    kept = [r for r in previous if (r["scenario"], r["contender"]) not in rerun]
    rank = {n: i for i, n in enumerate(CONTENDERS)}
    results = sorted(kept + results, key=lambda r: (r["scenario"] != "video", rank[r["contender"]]))
    args.scenarios = ",".join(dict.fromkeys(r["scenario"] for r in results))
    env = environment()
    (results_dir / "latest.json").write_text(json.dumps({"env": env, "clips": clips, "args": vars(args), "results": results}, indent=1))
    rows = build_rows(results, clips)
    for sc in args.scenarios.split(","):
        print(f"\n=== {sc.upper()} scenario: {clips[sc]['width']}x{clips[sc]['height']}, {clips[sc]['frames']} frames, "
              f"{args.iters} measured iterations ===")
        terminal_table(rows, sc)
    write_markdown(Path(args.out), rows, results, clips, env, args)
    print(f"\nwrote {args.out} and {results_dir / 'latest.json'}")


if __name__ == "__main__":
    main()
