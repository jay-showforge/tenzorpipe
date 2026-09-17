#!/usr/bin/env python3
"""Generate the reproducible benchmark clip: tests/data/benchmark_1080p.mp4.

1080p30 H.264 (High profile, B-frames, 2 s GOP) with a frame-counter overlay and moving
geometry, plus a 44.1 kHz stereo AAC track whose 1.0 s reference tone pulses start exactly on
each whole second, so audio and video alignment is visible in the demo.

FFmpeg is a test-asset tool only; the engine never uses it at runtime. Encoder settings are
pinned (single-threaded, fixed preset/CRF) so the same FFmpeg build reproduces the same bytes.

    python scripts/generate_benchmark_assets.py [--out tests/data/benchmark_1080p.mp4]
                                                [--seconds 10] [--fps 30] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/mnt/c/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/consola.ttf",
]
PULSE_HZ = 1000.0       # reference tone
PULSE_LENGTH_S = 0.12   # pulse duration at the start of every second
BACKGROUND = "0x0B1220"  # brand slate


def font_path() -> str:
    for candidate in FONTS:
        if pathlib.Path(candidate).exists():
            return candidate
    sys.exit("no usable TTF font found for the frame-counter overlay; install fonts-dejavu")


def video_filters(font: str) -> str:
    """Moving geometry plus frame/time overlays, all deterministic functions of t and n."""
    # drawbox exposes iw/ih (drawtext's W/H are not defined here); positions are functions of t.
    cyan_box = ("drawbox=x='(iw-360)/2+720*sin(2*PI*t/5)':y='(ih-360)/2+360*cos(2*PI*t/3)'"
                ":w=360:h=360:color=0x22D3EE@0.85:t=fill")
    amber_box = ("drawbox=x='(iw-220)/2-620*cos(2*PI*t/4)':y='(ih-220)/2+300*sin(2*PI*t/2.5)'"
                 ":w=220:h=220:color=0xF97316@0.80:t=fill")
    sweep = ("drawbox=x='mod(t*384\\,iw)':y=0:w=6:h=ih:color=0xE2E8F0@0.55:t=fill")
    grid = "drawgrid=w=160:h=160:t=1:color=0x1E293B@0.9"
    # Seeded grain: flat synthetic colour compresses to ~0.2 Mb/s, which makes decoding
    # unrealistically cheap. This lifts the clip to the ~8 Mb/s range of real 1080p footage
    # while staying byte-reproducible (fixed seed, single-threaded encode).
    grain = "noise=alls=10:allf=t+u:all_seed=12345"
    # The pulse marker lights up for exactly the audio pulse window of each second.
    pulse = (f"drawbox=x=0:y=0:w=iw:h=18:color=0x22C55E@1.0:t=fill"
             f":enable='lt(mod(t\\,1)\\,{PULSE_LENGTH_S})'")
    counter = (f"drawtext=fontfile={font}:text='frame %{{n}}':x=64:y=64:fontsize=72"
               ":fontcolor=0xF8FAFC:box=1:boxcolor=0x0F172A@0.72:boxborderw=18")
    timecode = (f"drawtext=fontfile={font}:text='%{{pts\\:hms}}':x=64:y=168:fontsize=48"
                ":fontcolor=0x94A3B8:box=1:boxcolor=0x0F172A@0.72:boxborderw=14")
    label = (f"drawtext=fontfile={font}:text='TenzorPipe benchmark 1080p30':x=w-tw-64:y=64"
             ":fontsize=44:fontcolor=0x22D3EE:box=1:boxcolor=0x0F172A@0.72:boxborderw=14")
    return ",".join([grid, cyan_box, amber_box, sweep, grain, pulse, counter, timecode, label])


def build(out: pathlib.Path, seconds: float, fps: int) -> None:
    font = font_path()
    frames = round(seconds * fps)
    # Pulses start on every whole second; both channels carry the same reference tone.
    tone = (f"0.6*sin(2*PI*{PULSE_HZ}*t)*lt(mod(t\\,1)\\,{PULSE_LENGTH_S})")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c={BACKGROUND}:s=1920x1080:r={fps}:d={seconds}",
        "-f", "lavfi", "-i", f"aevalsrc={tone}|{tone}:s=44100:d={seconds}",
        "-vf", video_filters(font),
        "-frames:v", str(frames),
        # 8 Mb/s matches the 1080p clip used in BENCHMARKS.md, so demo timings are comparable.
        "-c:v", "libx264", "-preset", "medium", "-profile:v", "high",
        "-b:v", "8M", "-maxrate", "10M", "-bufsize", "16M",
        "-bf", "3", "-g", str(fps * 2), "-keyint_min", str(fps * 2), "-sc_threshold", "0",
        "-pix_fmt", "yuv420p", "-threads", "1",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ]
    subprocess.run(cmd, check=True)


def probe(path: pathlib.Path) -> dict:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-count_packets", "-show_streams",
                                   "-show_format", "-of", "json", str(path)], text=True)
    info = json.loads(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    return {
        "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "duration_s": float(info["format"]["duration"]),
        "video": {"codec": video["codec_name"], "profile": video.get("profile"),
                  "size": f"{video['width']}x{video['height']}", "fps": video["avg_frame_rate"],
                  "frames": int(video["nb_read_packets"]), "pix_fmt": video["pix_fmt"]},
        "audio": {"codec": audio["codec_name"], "sample_rate": int(audio["sample_rate"]),
                  "channels": audio["channels"]},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "tests/data/benchmark_1080p.mp4")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--force", action="store_true", help="overwrite an existing asset")
    a = ap.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("ffmpeg and ffprobe must be on PATH (see bench/setup_wsl.sh)")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    if a.out.exists() and not a.force:
        print(f"{a.out} exists; use --force to regenerate")
    else:
        build(a.out, a.seconds, a.fps)
    info = probe(a.out)
    expected_frames = round(a.seconds * a.fps)
    assert info["video"]["frames"] == expected_frames, (info["video"]["frames"], expected_frames)
    assert info["video"]["size"] == "1920x1080" and info["video"]["codec"] == "h264"
    assert info["audio"]["sample_rate"] == 44100 and info["audio"]["channels"] == 2
    assert info["audio"]["codec"] == "aac"
    (ROOT / "tests/data").mkdir(parents=True, exist_ok=True)
    (ROOT / "tests/data/benchmark_1080p.json").write_text(json.dumps(info, indent=2))
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
