#!/usr/bin/env python3
"""A/B two or more tenzor binaries: median wall time, peak RSS and output bytes.

    python bench/ab_binaries.py --bin release=reference/bin/tenzor-v0.2.0-linux-x86_64 --bin new=target/release/tenzor \
        --clip ~/.tenzor-bench/clips/synthetic-1080p30-h264-20s-video-only.mp4 --workers 1,0 --reps 5

Runs are interleaved (A B A B ...) so thermal/background drift hits every binary equally.
Outputs go to /dev/shm. "same bytes" compares each binary's artifact with the first binary's,
so every binary must report the same version string for a byte-identical result.
"""
import argparse
import hashlib
import os
import re
import statistics
import subprocess
import time
from pathlib import Path


def run(binary, clip, workers, extra, out):
    out.unlink(missing_ok=True)
    cmd = [binary, "-i", clip, "-o", str(out), *(["--video-workers", workers] if workers != "default" else []), *extra]
    t0 = time.perf_counter()
    # stderr goes to a file so a chatty child can never block on a full pipe before wait4.
    with open("/dev/shm/ab-stderr.txt", "w+") as errf:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errf)
        _, status, rus = os.wait4(p.pid, 0)  # per-child rusage: exact peak RSS of this run
        wall = time.perf_counter() - t0
        errf.seek(0)
        err = errf.read()
    ok = os.waitstatus_to_exitcode(status) == 0
    digest = hashlib.sha256(out.read_bytes()).hexdigest() if ok and out.exists() else None
    out.unlink(missing_ok=True)
    mode = re.search(r"video_mode=\S+(?: workers=\d+)?", err)
    skip = re.search(r"skipped_nonref=(\d+)", err)
    return wall, rus.ru_maxrss / 1024, digest, mode.group(0) if mode else "single", int(skip.group(1)) if skip else 0, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", action="append", required=True, help="label=path")
    ap.add_argument("--clip", required=True)
    ap.add_argument("--workers", default="1,0", help="comma list; 'default' passes no flag")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--extra", default="", help="extra CLI args, space separated")
    a = ap.parse_args()
    bins = [b.split("=", 1) for b in a.bin]
    extra = a.extra.split()
    for workers in a.workers.split(","):
        stats = {label: [] for label, _ in bins}
        for label, path in bins:  # warm-up, untimed
            run(path, a.clip, workers, extra, Path("/dev/shm/ab-warm.tenzor"))
        for _ in range(a.reps):
            for label, path in bins:
                stats[label].append(run(path, a.clip, workers, extra, Path(f"/dev/shm/ab-{label}.tenzor")))
        ref_label = bins[0][0]
        ref_digest = stats[ref_label][0][2]
        ref_med = statistics.median(s[0] for s in stats[ref_label])
        print(f"\n--video-workers {workers} ({Path(a.clip).name}, {a.reps} interleaved reps)")
        for label, _ in bins:
            s = stats[label]
            med = statistics.median(x[0] for x in s)
            digests = {x[2] for x in s}
            same = digests == {ref_digest} and ref_digest is not None
            print(f"  {label:10s} median {med:6.3f}s  (min {min(x[0] for x in s):.3f})  "
                  f"x{ref_med / med:4.2f} vs {ref_label}  maxrss {max(x[1] for x in s):6.1f} MiB  "
                  f"{s[0][3]:28s} skipped_nonref={s[0][4]:<5d} same bytes as {ref_label}: {same}")
            if s[0][2] is None:
                print("    ERROR:", s[0][5].strip().splitlines()[-1:])


if __name__ == "__main__":
    main()
