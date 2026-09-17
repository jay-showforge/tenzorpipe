#!/usr/bin/env python3
"""Generate v0.2.0 EPYC reports only after the pinned-binary gates are complete."""
import hashlib,json,pathlib,statistics,tomllib,re
R=pathlib.Path(__file__).resolve().parents[1];E=R/'evidence'
load=lambda n:json.loads((E/n).read_text())
def write(n,s):
 lines=[];code=False
 for line in s.splitlines():
  if line.startswith('```'):code=not code
  if not code and not line.startswith('```'):
   line=re.sub(r'(?<=[,;:])(?=\d)', ' ',line)
   line=re.sub(r'(?<=\d)(?=MiB|KiB|ms\b|s\b|dB\b|kHz\b|kbps\b|epochs\b)', ' ',line)
   line=re.sub(r'\b(and|with|within|through|per|of|at|from|to|for|in|is|are|has|have|all|All|uses|batch)(?=\d)',r'\1 ',line)
   line=re.sub(r'(?<=[a-z)])\.(?=\d)', '. ',line)
  lines.append(line)
 (R/n).write_text('\n'.join(lines)+'\n')
sha=hashlib.sha256((R/'target/release/tenzor').read_bytes()).hexdigest()
version=tomllib.loads((R/'Cargo.toml').read_text())['package']['version'];assert version=='0.2.0'
s=load('verification-summary.json');matrix=load('matrix.json');reg=load('epoch-selection-regression.json');con=load('concurrency-tests.json');boundary=load('chunk-boundary-tests.json')
audio=load('v0.2.0/audio-identity-generated-media.json');repo=load('v0.2.0/identity-repo-fixtures.json');fuzz=load('v0.2.0/corruption-fuzz.json');writes=load('audio-write-failure.json')
bench=load('epyc-benchmarks-v020.json');retained=load('epyc-retained-v020.json');verified=load('epyc-benchmark-verification-v020.json');memory=load('memory-release.json');pick=load('worker-recommendation-v020.json')['recommended'];machine=load('machine-v020.json');disk=load('disk-write-audit.json');pub=load('publication-stress.json')
assert sha==s['binary_sha256']==con['binary_sha256']==boundary['binary_sha256']==reg['new_binary_sha256']==writes['binary_sha256']==pub['binary_sha256']==machine['binary_sha256']
assert s['media_cases']==16 and s['failure_cases']==12 and s['pytorch_all_batch_checks']
assert len(reg['cases'])==41 and len(con['cases'])==38 and len(boundary['cases'])==30
assert len(audio)==168 and all(x['ok'] and x['old_rc']==x['new_rc']==0 for x in audio)
assert len(repo)==396 and all(x['ok'] for x in repo) and sum(x['old_rc']==0 for x in repo)==336
assert len(fuzz)==92 and all(x['pass_gate'] for x in fuzz)
assert len(writes['cases'])==12 and all(x['same_full_error'] and x['clean_exit'] for x in writes['cases'])
assert len(bench)==80 and len(retained)==len(verified['artifacts'])==25 and len(memory)==4
assert memory[-1]['logical_columns']==memory[-2]['logical_columns']
assert '30 passed; 0 failed' in (E/'unit-v020.log').read_text()
for n in ['aac-unit-v020.log','aac-unit-no-default-v020.log']:assert '94 passed; 0 failed; 4 ignored' in (E/n).read_text()
assert 'licenses ok' in (E/'licenses-v020.log').read_text()
assert not (E/'fmt-v020.log').read_text().strip() and 'error:' not in (E/'clippy-v020.log').read_text()
assert len(disk)==3 and pub['runs']==20 and pub['immediate_and_delayed_reopen']
assert all(x['engine_binary_sha256']==sha for x in bench if x.get('engine_binary_sha256') and not x['variant'].startswith('old-'))
mib=lambda n:n/1048576
variants=['old-N2','N1','N2','N3','N4','N6','N8','baseline','old-audio','audio-inline','audio-thread']
groups={k:[x for x in bench if x['variant']==k and 1<=x['repetition']<=5] for k in variants}
assert all(len(xs)==5 for xs in groups.values())
med=lambda k,field='wall_seconds':statistics.median(x[field] for x in groups[k])
find=lambda k:next(x for x in bench if x['variant']==k)
commands='''```sh
# Linux x86-64: extract the complete ZIP (or both split ZIPs), then cd tenzorpipe-v0.2.0.
# Install a C/C++ compiler, NASM, FFmpeg, Python 3.12 and Rustup.
rustup toolchain install 1.98.1 --profile minimal --component rustfmt,clippy
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r python/requirements-test.txt
python -m pip install torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu
cargo install cargo-deny --version 0.20.2 --locked
bash scripts/reproduce.sh
```

The script contains every exact build, fixture, correctness, benchmark and audit
command in execution order. It uses CARGO_BUILD_JOBS=2 and /tmp output by default.
Allow about 15 GiB free for generated media, retained outputs and build caches.
Reference binaries are included in the binaries archive. Large audio fixtures and
long video are regenerated. The normal engine has no FFmpeg runtime dependency.
'''
limits='''- Tested Linux x86-64 CPU release; Windows, macOS, ARM, GPU and older-glibc compatibility are unverified.
- Progressive 8-bit YUV420 H.264 Baseline/High plus AAC-LC in MP4, and tested WAV PCM16/PCM24/float32. No WebM, AV1, VP9, HEVC, FLAC or MP3 claim.
- One audio/video track maximum. Unsupported color formats/HDR/changing SPS and complex edit lists are rejected. General fragmented MP4, rotation and arbitrary VFR conformance are not established.
- Millisecond timestamps, nearest-neighbor square resizing and arithmetic downmix; established sinc, STFT and Mel semantics remain unchanged.
- Chunking falls back for insufficient clean IDR cuts, duplicate PTS, insufficient buffer budget or excessive planning metadata. Input must remain immutable while mapped.
- MP4 index allocation is capped at 16 MiB and planning vector payload at 32 MiB. Arrow footer/index metadata grows with duration/batch count. Queue bounds exclude native decoder state, allocator overhead and working/batch tensors.
- Rust errors/panics cancel and join workers; native crashes, hung native calls, forced termination and storage faults remain process-level limitations. Consumers wait for successful exit; a hard kill can leave a partial file.
- Nonzero internal OpenH264 threading is experimental and disabled in the normal binary. The tested parallelism uses independent decoder instances.
- Zero-copy-compatible loading refers to CPU Arrow/PyTorch buffer aliasing, not decode/preprocessing, serialization or GPU transfer. Use copy=True before mutating tensors.
- Byte identity is demonstrated against the pinned v0.1.9 Linux binary on these fixtures, not guaranteed across every architecture/compiler/codec input. Four vendor tests are explicitly ignored, not passes.
- BUSL-1.1 owner-specific license/change/use terms remain unresolved. No project commercial rights or codec patent determination is implied by dependency license checks.
'''
repairs='''The production Rust engine and audio math are unchanged from the supplied v0.2.0.
The release was rebuilt with pinned Rust 1.98.1 and NASM. Repairs were to verification
and packaging: update the root-only license exception from =0.1.9 to =0.2.0; make
identity/fuzz failures return nonzero; require generated valid media to actually
decode; stream artifact hashing; test real write-limit failure; restore the full
PyTorch gate; replace stale reports and packaging assumptions with current evidence.

The initial generated noise fixture lacked a moov box. Both engines rejected it,
which was agreement but not decode success. It was regenerated through /tmp,
FFprobe-checked and all 168 generated-media comparisons were rerun successfully.
The first EFBIG limit was above the small WAV artifact size; it was lowered so all
chosen files actually encounter the error. These failed attempts are retained under
evidence/v020-review/. No production validation or numerical tolerance was relaxed.
'''
write('BUILD_STATUS.md',f'''# BUILD_STATUS — TenzorPipe {version}, independently verified EPYC release

**PASS within the documented scope.** Binary SHA-256: `{sha}`.
The bundled current binary is rebuilt with Rust 1.98.1, not the supplied Rust 1.95 binary.
The v0.1.9 comparator is the earlier Rust 1.98.1 release with SHA-256
`{machine['old_binary_sha256']}`. Both builds use source OpenH264 with NASM.

{repairs}

## Completed gates

- `cargo build --release --locked`, format, Clippy with warnings denied, cargo-deny licenses: PASS.
- 30 engine tests. Vendored AAC: 94 pass and 4 ignored with default features, and 94 pass / 4 ignored with the engine's no-default-features configuration. These are the same 94 tests, not 188 distinct tests.
- 16 independent real-media cases,12 invalid-input cases and the full PyArrow/PyTorch loader gate.
- 41 exact epoch-selection,38 concurrency and30 chunk-boundary/failure checks.
- 168 successful generated-audio comparisons;336 successful repository comparisons +60 expected-error agreements.92 corruption/truncation agreements and12 paired EFBIG write failures.
- 80 timed and independently checked artifacts:11 warm-ups,55 repeated unprofiled measurements and14 separate diagnostic/short runs.25 retained artifacts pass delayed rereads and the independent baseline/source comparison.
- Four memory runs through 22 minutes with exact old/new logical-column identity,20 immediate/delayed publication rereads and three dynamic-libc writable-open audits.

CLI defaults remain one video worker and concurrent audio processing with the new
bounded decode thread. On this host, `--video-workers {pick}` is the smallest measured
worker count within 5% of the fastest median. This is an explicit workload/resource
recommendation; automatic core-count selection is not universally optimal.

Host: {machine['platform']}; {machine['cpuinfo']}; {len(machine['affinity'])} allowed
logical CPUs. Compiler/lockfile, third-party notices, source patches, exact input
inventory and machine-readable evidence are included. Historical Claude and v0.1.9
reports are under docs/releases; their timings are not current EPYC measurements.

## Reproduce

{commands}

Earlier workspace delayed-truncation behavior remains an environment observation,
not a resolved engine defect. Current benchmark outputs use /tmp and pass independent
immediate/delayed reads. Instrumentation cannot establish a universal storage guarantee.

## Known limitations

{limits}''')
rows=[]
for x in matrix:
 a=x.get('audio');rows.append(f"| {x['file']} | {x['resolution']} | {x['epochs']} | {x['record_batches']} | {max((f['mae_255'] for f in x.get('visual',[])),default=0):.6f} | {a['active_logmel_mae'] if a else 0:.6f} |")
write('TEST_REPORT.md',f'''# TEST_REPORT — TenzorPipe {version}, EPYC rerun

**PASS for the tested scope.** Binary SHA-256: `{sha}`.

## Source-media fidelity and independent loading

| Fixture | Resolution | Epochs | Batches | Worst image MAE (0–255) | Active Log-Mel MAE |
|---|---:|---:|---:|---:|---:|
'''+ '\n'.join(rows)+f'''

Absent-modality zero cells are not applicable. FFmpeg decodes the source independently;
NumPy computes the documented resampling/STFT/Mel and color/resize reference.
Image MAE remains <3 intensity levels; energetic Log-Mel MAE <0.08 and p99 <0.5.
Visual tensors were reconstructed and inspected; source colors, pattern and motion
remain recognizable. Baseline/high-profile H.264 includes B frames; AVCC SPS/PPS,
decode order and presentation-frame selection retain the previously verified path.

The matrix covers 44.1/48 kHz, mono/stereo/six channels, AAC/WAV, audio-only,
video-only, tiny tails, non-divisible durations and 160/224/336 output. The flash/tone
aligns at 1,000 ms. 330ms epochs contain 33 audio hops. Rust DSP tests retain sinc
anti-alias and streaming-continuity gates. PyArrow visits all batches, and PyTorch
checks alias pointers, full iteration, dynamic shapes, indexing and writable copies.
The loader section was executed here; it is not inferred from earlier output identity.

Independent30-second baseline: worst image MAE
{verified['baseline_worst_frame_mae_255']:.6f}/255, minimum PSNR
{verified['baseline_min_frame_psnr_db']:.3f}dB, energetic Log-Mel MAE
{verified['baseline_active_audio_logmel_mae']:.6f}. Every output is finite with the
expected row count, timestamps and shape metadata. Selected long-file frames also
pass the source oracle.

## Audio change verification

- 30 engine unit tests pass, including source error/panic, early consumer exit, consumer panic, cancellation and exact threaded/inline output.
- 94 AAC tests pass with default features and again with engine-matching no-default-features;4 explicitly ignored tests are not counted as passes. Huffman table/scan, bit reads, dequantization and gain tables are exercised. Existing inverse-transform correctness tests remain enabled.
- 14 generated files ×4 settings ×3 execution variants =168 successful version-normalized byte comparisons against v0.1.9. Content includes music-like tones/transients, deterministic-seed white noise, pink noise and silence;8/22.05/44.1/48/96kHz; mono/stereo/5.1;24–384kbps; float32 and24-bit WAV; H.264+AAC. PNS/TNS/intensity/M-S encoder tools were requested; flags alone are not proof that every tool was active in every packet.
- The repository matrix uses 33 unique files ×12 settings/variants =396 checks:336 successful artifact comparisons and 60 matching expected errors. The supplied 432-row matrix repeated three generated video fixtures twice. Deduplication is not a dropped unique test case.
- Total audio/repository matrix:504 successful artifacts and 60 expected-error agreements. Only the two expected schema-version strings are normalized; numerical values, metadata apart from version, and serialized tensor bytes must match.
- 92 deterministic corruption/truncation cases agree with v0.1.9 in outcome and top-level error text, without hangs, Rust panics or leftover failed outputs. This is not complete error-chain equality for that fuzz script.
- 12 paired RLIMIT_FSIZE cases (3 media ×4 modes) hit EFBIG, compare the full error diagnostic, and clean partial outputs. This exercises write-failure cancellation; it does not claim the physical disk was filled or ENOSPC was separately injected.

## Retained video/concurrency and long-file gates

41 epoch-selection comparisons,38 pipeline/queue/failure checks and30 chunk-boundary
checks pass. Clean-IDR, off-grid, sparse, VFR, open-GOP fallback, audio tails and real
write failures remain covered. B-frame chunk-boundary images match the source oracle.

All 80 benchmark artifacts are checked outside their timed process.25 retained outputs
are checked again, with exact full-video logical-column identity across worker counts,
batch sizes and v0.1.9. Audio-only variants also match exactly. Four memory artifacts
are checked; the 22-minute old/new file has{memory[-1]['logical_columns']['rows']}epochs
with exact column hashes.20 publications pass immediate/delayed rereads. Dynamic-libc
open instrumentation finds no intermediate decoded-media file on the three audited
30-second pipelines; direct syscalls can bypass that instrumentation.

{repairs}

## Reproduce

{commands}

Current evidence: verification-summary.json, matrix.json, python-loader.json,
epoch-selection-regression.json, concurrency-tests.json, chunk-boundary-tests.json,
v0.2.0/audio-identity-generated-media.json, v0.2.0/identity-repo-fixtures.json,
v0.2.0/corruption-fuzz.json, audio-write-failure.json, epyc-benchmarks-v020.json,
epyc-benchmark-verification-v020.json and memory-release.json.
Suites overlap; these counts are not additive proof of distinct independent cases.
This is fixture-based verification, not codec conformance certification, a security
audit or formal proof of leak freedom. BUILD_STATUS lists the remaining limits.
''')
medianrows=[]
for k in variants:
 xs=groups[k];medianrows.append(f"| {k} | {med(k):.3f} | {min(x['wall_seconds'] for x in xs):.3f}–{max(x['wall_seconds'] for x in xs):.3f} | {mib(med(k,'peak_tree_rss_sampled_bytes')):.2f} | {med(k,'cpu_percent_one_core'):.1f}% | {330/med(k):.2f} | {660/med(k):.2f} |")
memrows=[f"| {x['variant']} / N{x['workers']} | {x['media_seconds']:.6f} | {x['wall_seconds']:.3f} | {mib(x['peak_tree_rss_sampled_bytes']):.2f} | {mib(x['peak_RssAnon_bytes']):.2f} | {mib(x['peak_RssFile_bytes']):.2f} | {mib(x['artifact_bytes']):.2f} |" for x in memory]
profilerows=[]
for x in bench:
 if x['profiled']:
  p=x['profile']['stages_seconds'];profilerows.append(f"| {x['variant']} | {x['wall_seconds']:.3f} | {p['video_decode']:.3f} | {p['video_resize']:.3f} | {p['audio_source']:.3f} | {p['audio_resample']:.3f} | {p['audio_mel']:.3f} | {p.get('audio_source_wait',0):.3f} |")
allrows=[f"| {x['name']} | {x['wall_seconds']:.3f} | {mib(x['peak_tree_rss_sampled_bytes']):.2f} | {x['cpu_percent_one_core']:.1f}% | {mib(x['artifact_bytes']):.2f} | {mib(x['sampled_tree_write_bytes']):.2f} | {x['media_seconds_per_second']:.2f} |" for x in bench if not x['warmup']]
write('BENCHMARK_REPORT.md',f'''# BENCHMARK_REPORT — TenzorPipe {version}, EPYC rerun

Audio-only median improves from **{med('old-audio'):.3f}s to {med('audio-thread'):.3f}s**:
**{med('old-audio')/med('audio-thread'):.2f}× faster**, with exact output identity.
At two video workers, full A/V improves from **{med('old-N2'):.3f}s to {med('N2'):.3f}s**.
The recommended **N{pick} median is {med(f'N{pick}'):.3f}s**, versus
**{med('baseline'):.3f}s** for the documented FFmpeg/Python/Arrow baseline:
**{med('baseline')/med(f'N{pick}'):.2f}× throughput** on this workload/host.
The faster parallel setting uses more CPU cores than that baseline; this is not
an equal-core decoder comparison or proof of universal superiority.

## Five paired unprofiled runs

| Variant | Median wall s | Min–max s | Median peak RSS MiB | CPU (100%=one core) | Media s/s | Epochs/s |
|---|---:|---:|---:|---:|---:|---:|
'''+ '\n'.join(medianrows)+f'''

N1/2/3/4/6/8 are current video-worker counts. old-N2 and old-audio use the pinned
v0.1.9 binary. audio-inline uses `--no-audio-decode-thread`; audio-thread is the
current concurrent default. Each repeated variant has one excluded warm-up and
five measured runs; order reverses on alternate rounds. No profile timer is enabled
in these repeated runs. Profile and short/batch experiments below are separate.

Selection rule: choose the smallest measured worker count within 5% of the fastest
median. This yields **{pick}**. The CLI default remains 1 as a conservative resource
policy; recommended deployment command for this measured workload:

```sh
./bin/tenzor-linux-x86_64 -i input.mp4 -o output.tenzor --video-workers {pick}
```

## Workload, baseline and attribution

Synthetic 330-second 640×360/30fps H.264 High Profile/B-frame video with 48 kHz stereo
AAC; 224×224 RGB/CHW float32, 50×64 Log-Mel per 0.5s epoch, batch32, uncompressed Arrow
IPC. Output has 660 epochs / 21 batches and {mib(find('N2')['artifact_bytes']):.2f}MiB;
baseline output is {mib(find('baseline')['artifact_bytes']):.2f}MiB.720p clips are
correctness fixtures; these speeds are not 720p/1080p throughput claims.

Baseline: reference/ffmpeg_arrow_baseline.py, concurrent FFmpeg video/audio pipes,
NumPy STFT/Mel and PyArrow batches. FFmpeg decoder and BLAS thread settings are 1.
This is the complete documented pipeline including process startup, not standalone
FFmpeg or the best possible tuned FFmpeg implementation. The Rust engine uses
independent video decoders, one audio decode worker, one resample/Mel worker and a
collector. More cores explain part of the full-pipeline advantage. The inline-audio
comparison separately demonstrates the benefit of the audio inner-loop changes.

Host: {machine['platform']}, {machine['cpuinfo']}, {len(machine['affinity'])} allowed
logical CPUs. Both Rust versions use 1.98.1 with the same locked runtime dependencies;
OpenH264 is source-built with NASM 2.16.01. Wall time includes startup and finished
serialization. wait4 supplies process-tree CPU and rusage; /proc/psutil samples RSS
and physical writes every 10 ms with PID-namespace mapping. Sampling can miss brief
peaks. Validation runs in a separate process after timing. Tests/compilation were
finished before serial benchmarks; shared-host/cache/filesystem variance remains.
The reported five-run ranges are not statistical confidence intervals.

## Memory across duration

| Version/workers | Actual media s | Wall s | Peak RSS MiB | Peak anonymous MiB | Peak file-backed MiB | Artifact MiB |
|---|---:|---:|---:|---:|---:|---:|
'''+ '\n'.join(memrows)+f'''

At N{pick}, fourfold duration grows peak RSS from
{mib(memory[0]['peak_tree_rss_sampled_bytes']):.2f} to
{mib(memory[2]['peak_tree_rss_sampled_bytes']):.2f}MiB, while output grows to
{mib(memory[2]['artifact_bytes']):.2f}MiB. The stream-copy loop is actually
{memory[2]['media_seconds']:.6f}s and includes {memory[2]['logical_columns']['rows']}
epochs, including its partial tail. Every logical column matches v0.1.9 exactly.
Anonymous/file-backed maxima can occur at different instants and are not additive.
This supports bounded tensor streaming with modest metadata growth, not a formal
zero-leak or constant-RSS claim. The old N2 memory row is a reference, not a same-worker
memory comparison; repeated old/new N2 RSS appears in the table above.

The extra decoded-audio queue is 32 chunks: at most 128 KiB queued mono PCM for supported
AAC access units,512KiB for WAV, plus in-flight chunks. The contiguous input buffer
releases consumed samples in 32,768-sample blocks. Video chunk/epoch/batch budgets and
native decoder state remain separate. More video workers increase total RSS.

## Profile diagnostics

| Separate run | Wall s | Sum video decode s | Sum resize s | Audio source s | Sinc s | Mel s | Audio source wait s |
|---|---:|---:|---:|---:|---:|---:|---:|
'''+ '\n'.join(profilerows)+'''

Timers overlap across threads and are wall durations, not additive CPU accounting.
`audio_source` includes read/decode/downmix on the producer; `audio_source_wait` is
consumer waiting and is excluded from the resample timer. The source separates
Huffman lookup, contiguous sinc access, FFT buffer reuse and exact formula tables;
this release accepts those changes unchanged from the supplied source.

The smaller-IMDCT experiment remains deferred: it would change numerical results.
No new floating-point tolerance was silently approved. The next performance phase
can evaluate it separately with a concrete waveform/Log-Mel error budget. The current
release retains exact identity and its tested fallback for inline audio decoding.

## Artifact verification and disk writes

80 outputs were checked for all-batch finite values, timestamps, dimensions, counts
and logical hashes outside timing:11 warm-ups,55 paired measurements and14 separate
experiments.25 retained artifacts pass delayed rereads and full baseline/source
fidelity checks. The other repetitions are deleted only after validation to bound
test-disk use; their hashes and measurements remain in the JSON evidence.

A separate LD_PRELOAD libc writable-open audit checks single/chunked/baseline30-second
pipelines. Its descendant sentinel confirms interception works. Only final output
and /dev/null are observed:0 bytes of intermediate decoded-media files in the observed
calls. This can miss direct syscalls/static executables and is not an exhaustive
syscall audit. strace was previously blocked by this environment; instrumentation is
excluded from throughput timing. Sampled physical writes below include output/logs
and filesystem effects, not an exact intermediate-write count.

## All non-warm-up measured runs

| Run | Wall s | Peak RSS MiB | CPU | Artifact MiB | Sampled writes MiB | Media s/s |
|---|---:|---:|---:|---:|---:|---:|
'''+ '\n'.join(allrows)+f'''

## Reproduce

{commands}

Current evidence: epyc-benchmarks-v020.json, epyc-retained-v020.json,
epyc-benchmark-verification-v020.json, machine-v020.json, memory-release.json and
disk-write-audit.json. Binary SHA-256:`{sha}`. Historical two-core Claude numbers
are archived separately and not mixed into the EPYC measurements.
''')
write('CONCURRENCY_REPORT.md',f'''# CONCURRENCY_REPORT — TenzorPipe {version}

Concurrent mode now has an audio decode/downmix producer feeding the existing
resample/Mel worker through 32 ordered PCM chunks. `--no-audio-decode-thread` retains
inline decode; sequential mode also uses inline decode. Video still uses the repaired
v0.1.9 ordered IDR-chunk scheduler with independent OpenH264 instances. The collector
alone creates/writes Arrow batches.

The new queue's supported AAC payload bound is 128 KiB and WAV bound 512 KiB, excluding
producer/consumer working chunks and allocator state. The contiguous source buffer
is drained in 32,768-sample blocks. Neither allocation grows with total media duration.
MP4/planner/footer metadata and native decoder state have their separately documented
bounds/limits; source files remain immutable while mapped.

The producer catches Rust source panics and sends the error in order. Dropping the
receiver releases a producer blocked in send. A consumer-gone guard runs during normal
return, errors and unwinding; a timed blocked send checks it every 20 ms before scoped
joins. Pipeline cancellation is checked before further decode/epoch work. The engine
also retains v0.1.9 cancellation guards and emitted-chunk accounting. Rust unwind
recovery does not cover native crashes or indefinitely blocked native calls.

30 engine tests include source error/panic, consumer stop/error/panic, pipeline
cancellation and bit-identical threaded/inline epochs.38 concurrency checks,30 video
boundary checks,92 malformed-input comparisons and12 paired EFBIG failures pass.
The extended audio matrices have 504 successful byte comparisons and 60 matching
expected errors. No floating-point order or output contract was relaxed.

Audio-only median is {med('audio-thread'):.3f}s threaded versus
{med('audio-inline'):.3f}s inline and {med('old-audio'):.3f}s in v0.1.9. Recommended
video workers on this host: **{pick}**, chosen as the smallest count within 5% of the
best five-run median. CLI default remains 1; auto(core count minus 2) is opt-in and
is not claimed optimal. See BENCHMARK_REPORT for CPU/RAM tradeoffs and long-run RSS.

Profile timers overlap. In threaded mode `audio_source` measures producer work and
`audio_source_wait` measures the consumer wait; the resample timer subtracts only its
own source-wait duration. Summed video decode/window-wait times can exceed wall time.
''')
write('DEPENDENCY_POLICY.md',f'''# DEPENDENCY_POLICY — TenzorPipe {version}

**cargo deny check licenses: PASS** on the pinned runtime lockfile. The unused
BSD-3-Clause/ISC allowance warnings are retained. The supplied deny.toml still named
root v0.1.9; its exact root-only BUSL exception now names=0.2.0. The permitted dependency
license list and runtime dependency versions were not widened or changed.

Allowed: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, Unicode-3.0, Zlib and CC0-1.0.
Unlisted dependencies fail closed. GPL, LGPL, AGPL, SSPL and other prohibited/copyleft
dependencies are not authorized. BUSL-1.1 is not a dependency allowlist entry.

- Source-built OpenH264 wrapper/sys 0.9.8: BSD-2-Clause; retained NASM assembly and source notices. No downloaded codec binary or FFmpeg engine linkage.
- rusty_aac 0.5.0: Apache-2.0. Vendored Huffman, bit-reader, table and FFT-buffer patches retain the original references/tests and license.
- RustFFT 6.4.1: MIT OR Apache-2.0. No smaller-FFT algorithm or new DSP dependency was introduced.
- rust_h264 0.4.0: MIT OR Apache-2.0; used for patched SPS/VUI metadata, not its decoder.
- mp4io 0.1.2: MIT OR Apache-2.0; hound 3.5.1: Apache-2.0; crossbeam-channel 0.5.17: MIT OR Apache-2.0.
- The vendor AAC test-only rusty_alloc/rusty_alloc-api 0.3.2 dependencies declare MIT; they are not in the production engine graph. The vendor test lockfile is retained.
- Arrow defaults are disabled; IPC is explicit. CC0 covers transitive tiny-keccak.

THIRD_PARTY_NOTICES and vendor license files accompany the repository/binaries.
FFmpeg and Python are independent external test/baseline tools, not bundled engine
executables. Media is synthetic. The binary archive includes notices for redistribution.

```sh
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check licenses
cargo metadata --locked --format-version 1 > evidence/cargo-metadata.json
```

**Project license remains incomplete.** BUSL-1.1 was supplied without finalized
owner-specific licensor, Change Date or Additional Use Grant. The earlier proposed
revenue/change terms were examples, not an owner decision; none were invented here.
`publish=false` remains. Dependency compliance is not a commercial-rights or codec
patent determination. This limitation remains before a commercial publication.
''')
write('V0.2.0_REPORT.md',f'''# TenzorPipe v0.2.0 — independently verified EPYC release

Claude's audio changes survived the pinned Rust 1.98.1 rebuild and independent tests.
The production engine source was accepted unchanged. I repaired the license gate,
failing-exit assertions, successful-input checks, bounded hashing, reproduction
scripts and stale release packaging/reports.

|330-second workload|v0.1.9 median s|v0.2.0 median s|
|---|---:|---:|
|Audio only|{med('old-audio'):.3f}|{med('audio-thread'):.3f}|
|Full A/V,2 video workers|{med('old-N2'):.3f}|{med('N2'):.3f}|

Recommended **{pick} video workers**: **{med(f'N{pick}'):.3f}s**, versus
**{med('baseline'):.3f}s** for the documented baseline. This uses more CPU cores;
read the benchmark report for all five repetitions, RSS and CPU. CLI default remains 1.
The 22-minute run peaks at {mib(memory[2]['peak_tree_rss_sampled_bytes']):.2f}MiB and
matches every v0.1.9 tensor column exactly.

Passed:30 engine tests;94 AAC tests in each of two feature configurations(4 ignored);
16 independent media cases/12 invalid-input cases; full PyTorch loader checks;
41 epoch/38 concurrency/30 boundary checks;504 successful version-normalized artifact
comparisons and 60 expected-error agreements;92 corruptions;12 paired EFBIG failures;
80 measured/verified artifacts, four long-memory artifacts and20 publication rereads.
License scan and the scoped libc disk-write audit pass. The matrix count deduplicates
three fixtures repeated in the supplied report; malformed generated media was repaired
and required to decode successfully before counting a pass.

Use the complete ZIP, or unzip both split archives into the same parent directory. Build/test
commands are in scripts/reproduce.sh and BUILD_STATUS.md. Exact float identity remains
the requirement; the smaller-IMDCT experiment is deferred. Project-specific BUSL terms
remain unresolved. All measured claims and coverage limits are in the five reports.

Binary SHA-256:`{sha}`.
''')
print('Generated six current EPYC reports for v'+version)
