# File-level batching (design note + scaffold)

## Why intra-file chunking is not enough

`--video-workers` decodes one file with independent OpenH264 instances over IDR-aligned
chunks. A file never uses more decoders than it has chunks, and each round of chunks waits
for its slowest member. On the 20 s 1080p benchmark clip (2 s GOPs, 10 chunks), 6 and 8
workers were equally fast, and 12–16 workers were clamped to 10 (see BENCHMARKS.md).

Training datasets are usually many short clips. A 10 s clip with 2 s GOPs has 5 chunks,
so on a 16-thread host most cores sit idle while one file decodes. Processing several files
at once uses those cores without depending on GOP structure.

## Scaffold: `python/tenzor_batch.py`

A process pool that splits a core budget across concurrent `tenzor` runs:

```sh
python python/tenzor_batch.py --out-dir out/ clips/*.mp4                 # auto: one file per core
python python/tenzor_batch.py --out-dir out/ --jobs 8 --workers-per-file 2 clips/*.mp4
python python/tenzor_batch.py --out-dir out/ --max-rss-mib 4096 clips/*.mp4 -- --resolution 160
```

- It always passes an explicit `--video-workers`. The CLI default is auto (all cores), so
  launching N processes without a flag would oversubscribe the CPU N-fold.
- `--max-rss-mib` caps concurrency with the measured 1080p engine footprint (~90 MiB for
  one decoder plus ~48 MiB per extra worker). Lower resolutions use less.
- Each file keeps TenzorPipe's per-file guarantees: an atomic output, no overwrite, and
  removal of the partial output on error.

## Measured on 16 short clips (10 s, 360p, A/V, 2 s GOP; i5-14400F, 16 threads)

| Mode | Wall | Notes |
|---|---:|---|
| Sequential, default (auto) workers | 1.95 s | one file at a time; 5 chunks cap each file at 5 decoders |
| 16 processes at once, no worker flag | 0.48 s | 80 decoders on 16 threads; at 1080p that is an estimated ~4 GiB of decoder RAM |
| `tenzor_batch.py --jobs 4 --workers-per-file 4` | 0.55 s | first run only; the scaffold's original heuristic, now replaced |
| `tenzor_batch.py` default (16 files × 1 worker) | 0.41 s | 4.8× sequential, lowest RAM |

All modes produced byte-identical outputs. Raw output of the second run:
`evidence/v0.3.0/file-batching-demo.txt`.
One synthetic clip type; long 1080p files shift the balance back toward more workers per file.

## Next steps (not implemented)

1. **Chunk-aware planning.** Read each MP4's sample table (cheap: the index only) to count
   IDR chunks, then give long files more workers and short files fewer, keeping the total at
   the core budget. This refines the current one-file-per-core default.
2. **One process, many files.** A `tenzor --input-list` mode with a shared worker pool would
   avoid per-process startup (~5–10 ms) and allocator/decoder duplication, and could write
   one Arrow dataset with a file-index column instead of one `.tenzor` per clip.
3. **Scheduling across P/E cores.** Hybrid CPUs finish chunks unevenly. Pool-wide scheduling
   of chunks from different files hides that better than per-file rounds do.
4. **Error policy.** Choose between skip-and-report and fail-fast for datasets with a few bad
   files; the scaffold currently reports every failure and exits nonzero.
