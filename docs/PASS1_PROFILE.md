# First-pass ingest: where the time actually goes

Measured against released v0.3.1 (`222e5b4`), before any production patch. The short
version: **93% of first-pass instructions are inside the H.264 decoder**, and the three
optimisations the directive proposes — a fused SIMD YUV→CHW kernel, arena-allocated NAL
pruning, and leaner Arrow serialisation — together account for **under 1% of instructions
and approximately none of the wall clock**, because they already run inside worker threads
or on a collector thread that is idle 97% of the time.

The gap against FFmpeg is two separate things, neither of which is TenzorPipe's own Rust:

1. **Per-frame decode cost.** Single-threaded, OpenH264 is 6–10% slower than libavcodec on
   the same stream. Measured: 3038 ms vs 2860 ms on the benchmark clip, 1068 ms vs 972 ms
   on a normal x264 encode.
2. **Parallel scaling.** Chunked decode splits at keyframes, so the worker count is capped
   by the number of keyframes in the file. FFmpeg's frame-level threading has no such cap.
   On the repo's benchmark clip that means 5 workers; on a normal 10-second x264 encode it
   means **2**, whatever the core count.

One lever is both measured and bit-identical: **letting the compiler use AVX2 on the
vendored decoder's C++**, worth 3.6–4.1% of single-worker wall time with byte-identical
output. Everything else that looks big either is not (the early-stop idea below) or needs
a decoder that TenzorPipe does not have.

---

## How this was measured

| | |
|---|---|
| Machine | 2× Intel Xeon @ 2.10 GHz (2 cores), Linux 6.18 |
| Binary | `target/release/tenzor` from `222e5b4`, `cargo build --release --locked` |
| Baseline | FFmpeg 6.1.1-3ubuntu5, same box, same clips |
| Clips | `tests/data/benchmark_1080p.mp4` (1920×1080, 300 frames, 10 s, H.264 High, **5 keyframes**); a 10 s and a 30 s re-encode at x264's default GOP (`-g 250`, **2 and 4 keyframes**); 60 s and 180 s concatenations; repo fixtures |
| Timing | 4 timed repetitions after one warm-up, medians, **interleaved** so drift hits both sides equally |
| Instructions | `valgrind --tool=callgrind` on a 2 s cut at `--video-workers 1`, 5,165,912,197 Ir total |
| Memory | `VmHWM` sampled every 2 ms for the whole process tree |

Absolute numbers drift between runs on this shared box — v0.3.1 at `--video-workers 2` on
the benchmark clip measures anywhere from 1833 to 1992 ms across the runs below. Every
comparison is interleaved *within* one run, so the deltas inside a single table are
meaningful and numbers should not be carried between tables.

Every comparison below reports the artifact digest. Any lever that changed a digest would
have been discarded; none did.

---

## Instruction attribution

`callgrind_annotate` self cost, aggregated by component. This sums to exactly 100% of the
5,165,912,197 instructions executed.

| Component | Instructions | Share |
|---|---:|---:|
| OpenH264, C++ | 4,294,947,128 | **83.14%** |
| OpenH264, x86 assembly | 522,071,241 | **10.11%** |
| libc (`memset`/`memcpy`/`malloc`) | 190,897,754 | 3.70% |
| Rust core/std, inlined | 48,585,836 | 0.94% |
| `rustfft` (Log-Mel) | 41,012,468 | 0.79% |
| `rust_h264` NAL parsing | 24,358,983 | 0.47% |
| **TenzorPipe YUV→CHW resize + colour** | 17,863,247 | **0.35%** |
| `rusty_aac` | 16,107,612 | 0.31% |
| TenzorPipe audio resample + mel | 8,022,545 | 0.16% |
| Arrow serialisation, `mp4io` demux | 46,837 | **0.00%** |

The ten hottest functions, all in the decoder:

| Function | Share |
|---|---:|
| `DecodeBinCabac` | 21.43% |
| `GetInterPred` | 9.10% |
| `ParseSignificantCoeffCabac` | 5.26% |
| `ParseSignificantMapCabac` | 4.68% |
| `GetInterBPred` | 3.57% |
| `ParseResidualBlockCabac8x8` | 3.19% |
| `IdctResAddPred8x8_c` | 3.12% (+1.18% inlined from `macros.h`) |
| `ParseResidualBlockCabac` | 2.41% |
| `__memset_avx2_unaligned_erms` | 2.22% |
| `WelsDeblockingMb` | 1.95% |

Roughly 40% of all instructions are CABAC entropy decoding, which is a serial
bit-at-a-time state machine — not SIMD work, and not something a different memory
allocator touches.

---

## Wall-clock stages

`--profile` on the benchmark clip at `--video-workers 2`, wall 1992 ms (median of 3). Stage timers are
sums across threads and overlap, so they do not add up to wall time.

| Stage | ms | What it means |
|---|---:|---|
| `video_decode` | 3240 | summed over 2 workers ≈ 1620 ms each ≈ the whole wall |
| `video_resize` | 14.1 | inside the workers, behind decode |
| `video_parse` | 8.7 | sample read + NAL scan, inside the workers |
| `arrow_pack` | 10.5 | on the collector thread |
| `arrow_write` | 4.4 | on the collector thread |
| `final_sync` | 8.8 | on the collector thread |
| `collector_receive_wait` | **1934.3** | the collector is **idle 97% of the wall clock** |
| `audio_source` / `resample` / `mel` | 60.0 / 20.3 / 10.1 | own thread, fully overlapped |
| `setup` | 4.6 | |

This is the decisive measurement for the directive's three proposals:

- **Fused SIMD YUV→CHW.** Target: 14.1 ms summed across 2 workers (0.35% of instructions).
  It runs *inside* the workers, interleaved with decode, so even making it free saves at
  most ~7 ms of wall — 0.4%. The kernel is also not obviously bit-identical: the current
  code does per-pixel `r/127.5 - 1.0` in scalar `f32`, and an AVX2 rewrite that lets the
  compiler contract a multiply-add into an FMA changes the last bit. That is a
  determinism risk taken on for 0.4%.
- **Arena-allocated NAL pruning.** Target: 8.7 ms of `video_parse` plus 0.47% of
  instructions in `rust_h264`. `avcc_to_annexb` does allocate one `Vec` per access unit,
  and `malloc`/`arena` code is 0.03% of instructions. Upper bound on the whole change:
  ~0.5% of wall.
- **Leaner Arrow serialisation.** Target: 0.00% of instructions and a thread that is idle
  97% of the time. Making Arrow packing and writing *instantaneous* saves nothing,
  because nothing is waiting on it. This one is not a small win, it is a zero win.

Combined ceiling if all three became free: **under 1.5% of wall clock**, against a
measured determinism risk on the first of them.

---

## Head-to-head with FFmpeg

Interleaved, medians of 4 timed runs. TenzorPipe does strictly more work than any FFmpeg
row: it also resizes, converts colour, computes Log-Mel audio and writes an Arrow file.

**`benchmark_1080p.mp4` — 5 keyframes, GOP 60**

| | wall |
|---|---:|
| `ffmpeg -threads 1 -f null` (decode only) | 2860 ms |
| TenzorPipe `--video-workers 1` | 3038 ms *(+6.2%)* |
| `ffmpeg -f null`, threads auto | 1812 ms |
| `ffmpeg -vf fps=2,scale=224:224 -f null` (nearest equivalent work) | 1851 ms |
| TenzorPipe `--video-workers 2` | 1892 ms *(+2.2% vs the equivalent-work row)* |
| TenzorPipe `--video-workers 4` | 1984 ms |

**A normal x264 encode, `-g 250` — 2 keyframes**

| | wall |
|---|---:|
| `ffmpeg -threads 1 -f null` | 972 ms |
| TenzorPipe `--video-workers 1` | 1068 ms *(+9.9%)* |
| `ffmpeg -f null`, threads auto | 730 ms |
| TenzorPipe `--video-workers 2` | 868 ms *(+18.9%)* |
| TenzorPipe `--video-workers 4` | 910 ms |

On two cores the two gaps are the same size, so they blur. On a 9-core EPYC they separate,
and the reported 631 ms vs 554 ms (+13.9%) is consistent with the +6% per-frame cost plus
5 chunks scaling less well than 9 frame threads.

### The keyframe ceiling

Chunk boundaries are IDR-aligned, so the worker count cannot exceed the keyframe count:

```
benchmark_1080p.mp4  (-g 60)   5 keyframes  --video-workers 8  ->  workers=5 chunks=5
real 10 s x264 clip  (-g 250)  2 keyframes  --video-workers 8  ->  workers=2 chunks=2
```

**The repo's benchmark clip has a 2-second GOP, which is unusual.** It is what lets chunked
decode reach 5 workers there. Real-world media at x264/x265 defaults gives one keyframe
every 250 frames, which on a 10-second clip is 2 chunks — so on the EPYC, seven of nine
cores have nothing to do, while FFmpeg's frame threading uses all of them.

Sub-dividing a GOP does not fix this. A worker that owns the second half of a GOP must
still decode the first half to get its reference frames, so its critical path is the whole
GOP: total CPU rises, wall time does not fall. Inside a GOP, H.264 decode is serial unless
the decoder itself pipelines entropy decoding against reconstruction, which is exactly
what libavcodec's frame threading does and what OpenH264 does not implement.

---

## Levers, measured and sized

### 1. AVX2 codegen for the vendored decoder — 3.6–4.1%, bit-identical, recommended

The vendored OpenH264 C++ is compiled for baseline x86-64. Compiling it for this machine
instead:

| | N=1 | N=2 | digest |
|---|---:|---:|---|
| v0.3.1, `benchmark_1080p` | 2878 ms | 1833 ms | `6e3db6e66b78c009` |
| `-march=native`, same | **2773 ms** *(−3.6%)* | 1832 ms | `6e3db6e66b78c009` |
| v0.3.1, real 10 s clip | 1051 ms | 825 ms | `0cd970806534dede` |
| `-march=native`, same | **1008 ms** *(−4.1%)* | 827 ms | `0cd970806534dede` |

Byte-identical output in all four cases, which is expected: the decoder's reconstruction
path is integer arithmetic exactly specified by the H.264 standard, so better codegen
cannot change the result. No gain at N=2 here because two cores on this box are already
memory-bound; on a 9-core EPYC each worker gets the per-worker gain.

This is consistent with the profile: `IdctResAddPred8x8` costs 4.30% of all instructions
and the vendored tree ships only a scalar `_c` version and a LoongArch `_lsx` one — there
is **no x86 assembly for it upstream**, so this function is exactly what `-march=native`
is auto-vectorising.

`-march=native` itself cannot ship (it would produce binaries that crash on older CPUs).
The shippable form is runtime dispatch: `__attribute__((target("avx2")))` clones of the
handful of hot integer kernels, selected by a CPUID check, mirroring how OpenH264 already
dispatches its assembly. Contained, testable, and provably bit-identical because the
output is spec-defined integers.

### 2. `-fstack-protector-all` → `-fstack-protector-strong` — no measurable gain, closed

The vendored build hardens every function in a decoder that makes millions of small calls,
so this looked promising. It is not: 3004→3030 ms at N=1 and 1930→1925 ms at N=2 on the
benchmark clip, 1070→1050 and 859→856 on the real clip. All inside run-to-run noise.
Not worth giving up the hardening.

### 3. Per-chunk early stop — 20% on this benchmark clip, 1–3% on real media

The idea is sound and bit-identical: once a chunk has produced every selected picture it
owns, no later access unit in it can be referenced by anything it still owes, so decoding
can stop. A prototype confirms the determinism claim completely — identical digests
everywhere, `test_arch_identity.py` unaffected, peak RSS within 1%:

| clip | v0.3.1 | early stop | |
|---|---:|---:|---|
| `benchmark_1080p`, N=2 | 1948 ms | **1661 ms** | −14.7% |
| `benchmark_1080p`, N=4 | 2035 ms | **1720 ms** | −15.5% |
| 60 s clip, N=2 | 9761 ms | 9655 ms | −1.1% (noise) |

And this is why it should not be sold as the headline win. Measuring the *static* bound —
the highest decode-order index among a chunk's selected pictures, everything after which
is unreachable — gives the true ceiling per clip:

| clip | GOP | unnecessary access units, 0.5 s windows |
|---|---:|---:|
| `benchmark_1080p.mp4` | 60 | **20.0%** (60 of 300) |
| `fixtures/baseline720.mp4` | short | 22.2% (22 of 99) |
| `fixtures/high-bframes.mp4` | short | 24.2% (24 of 99) |
| 60 s clip built from 6× the benchmark clip | 60 | **3.3%** (60 of 1800) |
| real 30 s x264 encode | 250 | **1.2%** (11 of 900) |

The win is the ratio *chunk tail slack / chunk length*. With a 2-second GOP and 0.5-second
epochs, up to a quarter of each chunk sits after its last selected picture. With a
250-frame GOP, the slack is at most one epoch out of seventeen. So early stop is worth 20%
**on the clip the benchmark is run against** and 1–3% on the media the engine is actually
for. It would flatter the benchmark number without making real ingest meaningfully faster.

Two further notes, if it is implemented anyway:

- The prototype's condition fires only when the last selected picture leaves the decoder's
  reorder buffer *before* the chunk's access units run out. On both long clips it never
  fired at all (0 of 1800 and 0 of 900), even though the static bound says 3.3% and 1.2%
  were available. A production version must compute the stop index up front from the
  selection list, not discover it at emit time.
- The prototype's accounting is wrong in a way that would ship a misleading log line: it
  reports `decoded 195 access units, skipped_nonref=105` where the stock binary reports
  `165` and `135`, because abandoned access units are attributed to neither counter. The
  invariant needs to become `produced + skipped + abandoned == range.len()`, with
  `abandoned` reported.

### 4. Dependency-closure decoding — small, and not where it looks

Today the engine decodes every *reference* frame and skips disposable ones it has not
selected: 165 of 300 on the benchmark clip, 160 of 300 on the real clip. Decoding only the
transitive reference closure of the selected pictures sounds like a further large cut, but
P-frames chain: every P frame between a keyframe and a selected picture is in the closure.
With selections spread evenly through each GOP, the closure is very nearly the full
reference set already. This needs full reference-list/RPS modelling — the largest and
riskiest change available — for the smallest remaining margin.

### 5. What would actually close the scaling gap

Frame-level threading inside the decoder, i.e. pipelining entropy decode of picture *n*+1
against reconstruction of picture *n*. OpenH264's decoder does not do it, and its
slice-based multithreading needs multiple slices per picture, which single-slice x264 and
x265 output does not provide. Closing this means a different H.264 decoder, and every
mature alternative is GPL or LGPL, which `DEPENDENCY_POLICY.md` excludes. This is a
strategic decision, not an optimisation, and it is the honest answer to "beat FFmpeg on
real media at high core counts".

---

## Invariants, as measured rather than assumed

- **Zero VRAM.** Nothing in the engine or its dependency graph allocates GPU memory; no
  lever above would change that.
- **Bit-identical determinism.** Every variant measured here — 1/2/4/8 workers,
  `-march=native`, `-fstack-protector-strong`, the early-stop prototype — produced
  byte-identical artifacts (`6e3db6e66b78c009` on the benchmark clip,
  `0cd970806534dede` on the real clip, `14f9f182dfad659a` on the 60 s clip).
  `scripts/test_arch_identity.py` passes unchanged.
- **Subsequent-pass reads.** `pyarrow.memory_map` + `open_file` + first batch: 0.13–0.68 ms
  (median 0.16 ms); per-batch 0.033 ms. Because the artifacts are byte-identical, no lever
  here can change this.
- **Host memory — the "~58 MiB flat ceiling" needs restating.** Peak RSS is bounded by
  construction (`window × max_chunk_tensor_bytes`, printed in the header), but it is not
  58 MiB at 1080p. Measured:

  | clip | N=1 | N=2 | N=4 |
  |---|---:|---:|---:|
  | 720p, 3.3 s | 29.4 MiB | 42.7 MiB | 42.7 MiB |
  | 1080p, 10 s, GOP 60 | 76.2 MiB | 120.0 MiB | 206.4 MiB |
  | 1080p, 10 s, GOP 250 | 68.1 MiB | 105.0 MiB | 105.2 MiB |
  | 1080p, 60 s, GOP 60 | 101.1 MiB | 145.7 MiB | 234.3 MiB |

  It scales with resolution, epochs per chunk (so with GOP length) and worker count. It is
  close to flat in clip *length*: 119 → 146 → 161 MiB at N=2 for 10 s → 60 s → 180 s, a 35%
  rise across an 18× length increase, with the artifact growing from 12 MiB to 218 MiB. So
  the bound holds; the number 58 does not, above 720p.

---

## Recommendation

1. **Ship runtime-dispatched AVX2/NEON codegen for the vendored decoder's hot integer
   kernels.** Measured 3.6–4.1% per worker, byte-identical output, and it is the only
   lever here that attacks the 93% of instructions that matter.
2. **Do not build the fused SIMD YUV→CHW kernel, the NAL arena, or the Arrow changes.**
   Together they are worth under 1.5% of wall clock, one of them carries a real
   determinism risk, and the Arrow one is worth exactly zero because the collector thread
   is already idle 97% of the time.
3. **Treat early stop as optional.** It is bit-identical and cheap, and it is worth 15% on
   the benchmark clip — but only 1–3% on real long-GOP media. Worth having; not worth
   reporting as the answer.
4. **Decide separately about frame-threaded decoding.** The keyframe ceiling, not
   TenzorPipe's Rust, is what caps throughput on real media at high core counts, and no
   amount of kernel work reaches it.
5. **Consider replacing `benchmark_1080p.mp4`'s 2-second GOP** with a default-GOP clip, or
   adding one beside it. The current asset makes chunked decode look 2.5× more scalable
   than it is on real files, which is the opposite of useful in a benchmark.
