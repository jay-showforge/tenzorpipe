#!/usr/bin/env python3
"""File-level batching scaffold: ingest many short clips concurrently.

Intra-file chunking cannot use more decoders than a clip has IDR-aligned chunks (a 10 s clip
with 2 s GOPs has 5), so datasets of short clips scale by running several files at once.
This script splits a core budget across concurrent `tenzor` processes and passes an explicit
`--video-workers` to each. That matters because the CLI default is now auto: N processes
launched without a flag would each claim every core and oversubscribe the machine.

    python python/tenzor_batch.py --out-dir /data/tenzor clips/*.mp4
    python python/tenzor_batch.py --out-dir out --jobs 4 --workers-per-file 4 --max-rss-mib 4096 a.mp4 b.mp4

Scaffold status: process-level scheduling only. Not yet done: chunk-aware planning (reading
IDR counts to give long files more workers), streaming many files into one Arrow dataset,
and an in-process multi-file mode that shares decoder instances. See docs/FILE_BATCHING.md.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Measured engine RSS at 1080p (BENCHMARKS.md): ~90 MiB for one decoder, ~48 MiB per extra worker.
BASE_RSS_MIB, PER_WORKER_RSS_MIB = 90, 48


def plan(files: int, cores: int, jobs: int | None, workers: int | None, max_rss_mib: int | None):
    """Pick (concurrent files, video workers per file) so jobs x workers <= cores."""
    if jobs is None and workers is None:
        # File-level parallelism first: on 16 short clips, 16 files x 1 worker (0.43 s) beat
        # 4 files x 4 workers (0.55 s). Leftover cores go to intra-file chunking.
        jobs = max(1, min(files, cores))
        workers = max(1, cores // jobs)
    elif jobs is None:
        jobs = max(1, cores // workers)
    elif workers is None:
        workers = max(1, cores // jobs)
    jobs = max(1, min(jobs, files))
    if max_rss_mib:
        per_file = BASE_RSS_MIB + PER_WORKER_RSS_MIB * (workers - 1)
        jobs = max(1, min(jobs, max_rss_mib // per_file))
    return jobs, workers


def ingest(binary: str, src: pathlib.Path, out_dir: pathlib.Path, workers: int, extra: list[str]):
    dst = out_dir / (src.stem + ".tenzor")
    started = time.perf_counter()
    proc = subprocess.run([binary, "-i", str(src), "-o", str(dst), "--video-workers", str(workers), *extra],
                          capture_output=True, text=True)
    return src, dst, proc.returncode, time.perf_counter() - started, proc.stderr.strip().splitlines()[-1:]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=pathlib.Path)
    ap.add_argument("--out-dir", required=True, type=pathlib.Path)
    ap.add_argument("--binary", default=str(ROOT / "target/release/tenzor"))
    ap.add_argument("--cores", type=int, default=os.cpu_count() or 1, help="total core budget")
    ap.add_argument("--jobs", type=int, help="files processed concurrently")
    ap.add_argument("--workers-per-file", type=int, help="--video-workers passed to each file")
    ap.add_argument("--max-rss-mib", type=int, help="cap concurrency by estimated engine RSS")
    ap.add_argument("--skip-existing", action="store_true", help="leave already-converted outputs alone")
    ap.add_argument("extra", nargs=argparse.REMAINDER, help="after --, extra tenzor arguments")
    a = ap.parse_args()
    extra = a.extra[1:] if a.extra[:1] == ["--"] else a.extra

    a.out_dir.mkdir(parents=True, exist_ok=True)
    stems = [p.stem for p in a.inputs]
    if len(set(stems)) != len(stems):
        sys.exit("input file names must be unique: outputs are named <stem>.tenzor")
    todo = [p for p in a.inputs if not (a.skip_existing and (a.out_dir / (p.stem + ".tenzor")).exists())]
    jobs, workers = plan(len(todo), a.cores, a.jobs, a.workers_per_file, a.max_rss_mib)
    print(f"{len(todo)} files: {jobs} concurrent x --video-workers {workers} (core budget {a.cores})", flush=True)

    started, failed = time.perf_counter(), 0
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        futures = [pool.submit(ingest, a.binary, p, a.out_dir, workers, extra) for p in todo]
        for future in concurrent.futures.as_completed(futures):
            src, dst, rc, seconds, tail = future.result()
            failed += rc != 0
            print(f"{'ok ' if rc == 0 else 'ERR'} {seconds:6.2f}s {src.name} -> {dst.name}"
                  + ("" if rc == 0 else f"  {tail}"), flush=True)
    wall = time.perf_counter() - started
    print(f"done: {len(todo) - failed}/{len(todo)} files in {wall:.2f}s ({len(todo) / wall:.2f} files/s)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
