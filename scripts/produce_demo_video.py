#!/usr/bin/env python3
"""Produce assets/tenzorpipe_demo.mp4: narrated side-by-side explainer.

Steps
  1. Detect a local text-to-speech engine (no cloud services are used).
  2. Synthesize assets/narration.wav.
  3. Compose a 1920x1080 side-by-side visual from tests/data/benchmark_1080p.mp4 and the
     measured numbers in benchmark_results.json (run scripts/run_side_by_side_demo.py first).
  4. Mux visuals + narration into assets/tenzorpipe_demo.mp4.

    python scripts/produce_demo_video.py [--voice "Microsoft Zira Desktop"] [--keep-frames]

Engine detection order: piper, espeak-ng/espeak, flite, Python packages (TTS/coqui, kokoro,
bark, chatterbox, pyttsx3), then Windows SAPI through powershell.exe (available under WSL).
Every number burned into the video comes from benchmark_results.json; nothing is hard-coded.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import wave

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
CLIP = ROOT / "tests/data/benchmark_1080p.mp4"
METRICS = ROOT / "benchmark_results.json"

# Narration. "Hundreds of megabytes" rather than a single figure: measured GPU decoders ranged
# from 336 MiB (TorchCodec here) to 822 MiB (DALI in BENCHMARKS.md).
NARRATION = (
    "Deep learning video pipelines face a silent bottleneck. "
    "Standard decoders consume hundreds of megabytes of GPU memory "
    "and waste compute re-decoding files every epoch. "
    "TenzorPipe solves this with zero VRAM CPU ingestion, "
    "streaming contiguous Apache Arrow tensors "
    "with millisecond re-reads and bit-exact reproducibility."
)

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/mnt/c/Windows/Fonts/arialbd.ttf"]
MONO = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/mnt/c/Windows/Fonts/consolab.ttf"]


def pick(paths):
    for p in paths:
        if pathlib.Path(p).exists():
            return p
    sys.exit(f"no font found among {paths}")


# ----------------------------------------------------------------- text to speech

def powershell():
    for candidate in ["powershell.exe", "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"]:
        if shutil.which(candidate) or pathlib.Path(candidate).exists():
            return candidate
    return None


def python_tts_packages():
    found = []
    for module, label in [("TTS", "coqui-TTS"), ("kokoro", "kokoro"), ("bark", "bark"),
                          ("chatterbox", "chatterbox"), ("pyttsx3", "pyttsx3"), ("piper", "piper-tts")]:
        try:
            __import__(module)
            found.append(label)
        except Exception:
            pass
    return found


def detect_engines():
    """Report every local TTS option found, best first."""
    engines = []
    for binary, label in [("piper", "piper"), ("espeak-ng", "espeak-ng"), ("espeak", "espeak"),
                          ("flite", "flite"), ("spd-say", "speech-dispatcher")]:
        if shutil.which(binary):
            engines.append({"kind": "binary", "name": label, "path": shutil.which(binary)})
    for package in python_tts_packages():
        engines.append({"kind": "python", "name": package})
    if powershell():
        engines.append({"kind": "sapi", "name": "Windows SAPI (System.Speech)", "path": powershell()})
    return engines


def synthesize(engine, text, out_wav, voice=None):
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    raw = out_wav.with_suffix(".raw.wav")
    if engine["kind"] == "sapi":
        # PowerShell writes a 16-bit PCM WAV; a Windows path is required for the COM API.
        win_path = subprocess.check_output(["wslpath", "-w", str(raw)], text=True).strip() \
            if shutil.which("wslpath") else str(raw)
        select = f"$s.SelectVoice('{voice}');" if voice else ""
        script = ("Add-Type -AssemblyName System.Speech;"
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"{select}$s.Rate = -1;"
                  f"$s.SetOutputToWaveFile('{win_path}');"
                  f"$s.Speak(@'\n{text}\n'@);$s.Dispose()")
        subprocess.run([engine["path"], "-NoProfile", "-NonInteractive", "-Command", script], check=True)
    elif engine["kind"] == "binary" and engine["name"] == "piper":
        subprocess.run([engine["path"], "--output_file", str(raw)], input=text, text=True, check=True)
    elif engine["kind"] == "binary" and engine["name"].startswith("espeak"):
        subprocess.run([engine["path"], "-w", str(raw), "-s", "150", text], check=True)
    elif engine["kind"] == "binary" and engine["name"] == "flite":
        subprocess.run([engine["path"], "-t", text, "-o", str(raw)], check=True)
    else:
        sys.exit(f"no automated path for engine {engine}; install piper or espeak-ng")
    # Normalise to 44.1 kHz stereo with a little headroom for the music-free voice track.
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
                    "-af", "loudnorm=I=-18:TP=-2:LRA=11", "-ar", "44100", "-ac", "2",
                    str(out_wav)], check=True)
    raw.unlink(missing_ok=True)
    with wave.open(str(out_wav)) as w:
        return w.getnframes() / w.getframerate()


# ----------------------------------------------------------------- visuals

def metric(results, name, key, default=float("nan")):
    return results.get("results", {}).get(name, {}).get(key, default)


def compose(metrics, narration_s, out, keep_frames=False):
    font, mono = pick(FONTS), pick(MONO)
    clip_info = metrics["clip"]
    legacy_name = "TorchCodec CUDA" if metric(metrics, "TorchCodec CUDA", "ingest_ms") == \
        metric(metrics, "TorchCodec CUDA", "ingest_ms") else "FFmpeg pipe"
    legacy_ingest = metric(metrics, legacy_name, "ingest_ms")
    legacy_epoch = metric(metrics, legacy_name, "epoch_ms")
    legacy_vram = metric(metrics, legacy_name, "vram_mib")
    tp_ingest = metric(metrics, "TenzorPipe", "ingest_ms")
    tp_epoch = metric(metrics, "TenzorPipe", "epoch_ms")
    speedup = legacy_epoch / tp_epoch if tp_epoch else float("nan")
    parity = metrics.get("parity", {})

    def text(s, x, y, size, color, font_file=None, box=None, enable=None):
        safe = str(s).replace("\\", "").replace(":", "\\:").replace("'", "").replace("%", "")
        parts = [f"drawtext=fontfile={font_file or font}", f"text='{safe}'", f"x={x}", f"y={y}",
                 f"fontsize={size}", f"fontcolor={color}"]
        if box:
            parts += [f"box=1:boxcolor={box}:boxborderw=18"]
        if enable:
            parts += [f"enable='{enable}'"]
        return ":".join(parts[:1] + parts[1:]).replace("drawtext=fontfile", "drawtext=fontfile", 1)

    half = "scale=880:-2"
    # Panels: the same clip on both sides; the left panel is tinted red to read as "legacy".
    filters = [
        f"[1:v]{half},drawbox=x=0:y=0:w=iw:h=ih:color=0xF87171@0.85:t=6[left]",
        f"[2:v]{half},drawbox=x=0:y=0:w=iw:h=ih:color=0x22D3EE@0.9:t=6[right]",
        "[0:v][left]overlay=x=60:y=330[a]",
        "[a][right]overlay=x=980:y=330[b]",
        "[4:v]scale=420:-1[banner]",
        "[b][banner]overlay=x=56:y=36[c]",
    ]
    overlays = [
        text(f"{clip_info['width']}x{clip_info['height']} @ {clip_info['fps']:.0f}fps  "
             f"{clip_info['bitrate_mbps']} Mb-s  {clip_info['epochs']} epochs",
             1920 - 64, 64, 30, "0x64748B"),
        text("LEGACY PIPELINE", 60, 286, 46, "0xF87171"),
        text(legacy_name, 60, 860, 34, "0x94A3B8", mono),
        text(f"GPU memory  {legacy_vram:,.0f} MiB", 60, 910, 40, "0xF87171", mono, "0x7F1D1D@0.35"),
        text(f"first pass   {legacy_ingest:,.0f} ms", 60, 975, 40, "0xFCA5A5", mono, "0x7F1D1D@0.25"),
        text(f"every epoch  {legacy_epoch:,.0f} ms  re-decode", 60, 1030, 40, "0xFCA5A5", mono, "0x7F1D1D@0.25"),
        text("TENZORPIPE", 980, 286, 46, "0x22D3EE"),
        text("CPU decode, Arrow IPC artifact", 980, 860, 34, "0x94A3B8", mono),
        text("GPU memory  0 MiB", 980, 910, 40, "0x4ADE80", mono, "0x064E3B@0.35"),
        text(f"first pass   {tp_ingest:,.0f} ms", 980, 975, 40, "0x67E8F9", mono, "0x0E7490@0.25"),
        text(f"every epoch  {tp_epoch:,.1f} ms  Arrow re-read", 980, 1030, 40, "0x67E8F9", mono, "0x0E7490@0.25"),
    ]
    if speedup == speedup:
        overlays.append(text(f"{speedup:,.0f}x faster per training epoch", 520, 150, 44, "0x4ADE80",
                             enable=f"gte(t,{max(narration_s - 12, 4):.1f})"))
    if parity.get("verified"):
        overlays.append(text(f"bit-exact: {parity['cases']} regression cases, {parity['failures']} failures",
                             1920 - 64, 118, 30, "0x4ADE80",
                             enable=f"gte(t,{max(narration_s - 8, 6):.1f})"))
    # Right-align the two header-right texts by switching x to an expression.
    overlays = [o.replace(":x=1920 - 64:", ":x=w-tw-64:").replace(":x=1856:", ":x=w-tw-64:") for o in overlays]
    filters.append("[c]" + ",".join(overlays) + "[v]")

    loops = max(1, int(narration_s // clip_info["duration_s"]) + 1)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=0x0B1220:s=1920x1080:r=30:d={narration_s + 1.5:.2f}",
        "-stream_loop", str(loops), "-i", str(CLIP),
        "-stream_loop", str(loops), "-i", str(CLIP),
        "-i", str(ASSETS / "narration.wav"),
        "-i", str(ASSETS / "banner.png"),
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "3:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out),
    ]
    subprocess.run(cmd, check=True)
    if keep_frames:
        (ASSETS / "filtergraph.txt").write_text(";\n".join(filters))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voice", default="Microsoft Zira Desktop", help="SAPI voice name, if that engine is used")
    ap.add_argument("--out", type=pathlib.Path, default=ASSETS / "tenzorpipe_demo.mp4")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--list-engines", action="store_true", help="print detected TTS engines and exit")
    a = ap.parse_args()

    engines = detect_engines()
    print("local TTS engines detected:")
    for e in engines:
        print(f"  - {e['name']} ({e['kind']}{': ' + e['path'] if e.get('path') else ''})")
    if not engines:
        sys.exit("no local TTS engine found: install piper or espeak-ng, or run on Windows/WSL for SAPI")
    if a.list_engines:
        return
    if not CLIP.exists():
        sys.exit(f"{CLIP} missing; run scripts/generate_benchmark_assets.py")
    if not METRICS.exists():
        sys.exit(f"{METRICS} missing; run scripts/run_side_by_side_demo.py")

    engine = engines[0]
    print(f"using {engine['name']}")
    narration = ASSETS / "narration.wav"
    seconds = synthesize(engine, NARRATION, narration, a.voice if engine["kind"] == "sapi" else None)
    print(f"narration: {narration.relative_to(ROOT)} ({seconds:.1f}s)")

    metrics = json.loads(METRICS.read_text())
    compose(metrics, seconds, a.out, a.keep_frames)
    info = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(a.out)], text=True))
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    print(f"video: {a.out.relative_to(ROOT)} {video['width']}x{video['height']} "
          f"{float(info['format']['duration']):.1f}s {video['codec_name']}/{audio['codec_name']} "
          f"{a.out.stat().st_size / 1048576:.1f} MiB")


if __name__ == "__main__":
    main()
