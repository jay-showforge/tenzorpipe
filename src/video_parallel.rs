//! Chunked N-worker H.264 decoding (v0.1.8).
//!
//! The clip is split only at verified IDR access units whose presentation times
//! strictly follow every earlier access unit. Each chunk is decoded by an
//! independent OpenH264 instance. Epoch-to-picture selection is computed once,
//! up front, from container timing with the exact v0.1.6 midpoint/tie rules, so
//! chunk boundaries cannot change which picture serves an epoch.
//!
//! Ordering and memory: a worker may only claim chunk `c` while
//! `c < next_chunk_to_emit + window`. Finished chunks wait in a reorder map and
//! are emitted strictly in order, so at most `window` decoded chunks (including
//! the one being forwarded) exist at once, independent of clip length.
use anyhow::{Context, Result, anyhow, bail};
use std::{
    cmp::Reverse,
    collections::{BTreeMap, BTreeSet, BinaryHeap},
    ops::Range,
    sync::{Condvar, Mutex},
    time::Duration,
};

use crate::concurrency::{CancelOnDrop, Cancellation, Cancelled, worker};
use crate::media::Timeline;
use crate::metrics::{Metrics, Stage};
use crate::video::{
    MAX_REORDER, PresentationTimes, VideoConfig, VideoInfo, avcc_to_annexb, decode_h264_mp4,
    epoch_end, is_disposable, video_color, yuv420_to_resized_chw,
};
use mp4io::{Codec, CodecConfig, Mp4, SampleEntry, TrackKind};
use openh264::{
    OpenH264API,
    decoder::{Decoder, DecoderConfig, Flush},
    formats::YUVSource,
};
use rust_h264::nal::parse_avcc_config;

#[derive(Clone, Copy, Debug)]
pub struct ParallelConfig {
    /// Requested decode workers (>= 2 to use this path).
    pub workers: usize,
    /// Merge consecutive IDR spans until a chunk covers at least this much media.
    pub chunk_target_ms: u64,
    /// Upper bound for decoded-but-unemitted chunk tensors.
    pub buffer_bytes: usize,
}

/// One selected picture: presentation time and the epoch range it serves.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Selection {
    pub pts: i64,
    pub epochs: (usize, usize),
}

/// Same selection as the v0.1.6 streaming loop, computed from sorted presentation times.
pub fn assign_epochs(presented: &[i64], window_ms: u64, total: usize) -> Vec<Selection> {
    let mut epoch = 0;
    let mut out = Vec::new();
    for (i, &pts) in presented.iter().enumerate() {
        let end = epoch_end(pts, presented.get(i + 1).copied(), window_ms, total);
        if epoch < end {
            out.push(Selection {
                pts,
                epochs: (epoch, end),
            });
            epoch = end;
        }
    }
    out
}

/// Split decode-order samples at valid boundaries. `split_ok[k]` says sample `k`
/// is an IDR access unit. A boundary is also required to be a clean presentation
/// cut: every earlier sample presents strictly before every later sample.
pub fn plan_chunks(pts: &[i64], split_ok: &[bool], target_ms: u64) -> Vec<Range<usize>> {
    let n = pts.len();
    if n == 0 {
        return Vec::new();
    }
    let mut suffix_min = vec![i64::MAX; n + 1];
    for k in (0..n).rev() {
        suffix_min[k] = suffix_min[k + 1].min(pts[k]);
    }
    let mut chunks = Vec::new();
    let mut start = 0;
    let mut prefix_max = i64::MIN;
    for k in 0..n {
        if k > start
            && split_ok[k]
            && prefix_max < suffix_min[k]
            && suffix_min[k].saturating_sub(suffix_min[start]) >= target_ms as i64
        {
            chunks.push(start..k);
            start = k;
        }
        prefix_max = prefix_max.max(pts[k]);
    }
    chunks.push(start..n);
    chunks
}

/// A sync flag alone is insufficient: validate every AVCC NAL header and require
/// all VCL NALs to be IDR slices. Malformed candidates fall back to normal decode,
/// where the strict access-unit validator reports the actual input error.
fn first_vcl_is_idr(mut data: &[u8], length_size: usize) -> bool {
    if !(1..=4).contains(&length_size) {
        return false;
    }
    let mut found = false;
    while !data.is_empty() {
        if data.len() < length_size {
            return false;
        }
        let n = data[..length_size]
            .iter()
            .fold(0usize, |v, b| (v << 8) | *b as usize);
        data = &data[length_size..];
        if n == 0 || n > data.len() || data[0] & 128 != 0 || data[0] & 31 == 0 {
            return false;
        }
        match data[0] & 31 {
            5 => found = true,
            1..=4 | 19..=21 => return false,
            _ => (),
        }
        data = &data[n..];
    }
    found
}

const PLAN_BUDGET_BYTES: usize = 32 * 1024 * 1024;
// Conservative simultaneous vector-payload allowance, including capacities and
// per-chunk containers. Allocator bookkeeping is additional bounded overhead.
const PLAN_BYTES_PER_SAMPLE: usize = 192;
fn plan_fits_budget(samples: usize) -> bool {
    samples
        .checked_mul(PLAN_BYTES_PER_SAMPLE)
        .is_some_and(|bytes| bytes <= PLAN_BUDGET_BYTES)
}
fn release_index_pages(mapping: Option<&memmap2::Mmap>) -> Result<()> {
    #[cfg(unix)]
    if let Some(mapping) = mapping {
        // Input must stay immutable, as with existing batch-level page release.
        unsafe {
            mapping.unchecked_advise(memmap2::UncheckedAdvice::DontNeed)?;
        }
    }
    #[cfg(not(unix))]
    let _ = mapping;
    Ok(())
}

/// Ordered, window-bounded parallel map. `work(c)` runs on worker threads;
/// `emit(c, value)` runs on the calling thread in strictly increasing `c`.
pub fn run_ordered<T, W, E>(
    count: usize,
    workers: usize,
    window: usize,
    cancel: &Cancellation,
    metrics: &Metrics,
    work: W,
    mut emit: E,
) -> Result<()>
where
    T: Send,
    W: Fn(usize) -> Result<T> + Sync,
    E: FnMut(usize, T) -> Result<()>,
{
    struct State<T> {
        next_claim: usize,
        next_emit: usize,
        done: BTreeMap<usize, T>,
        error: Option<anyhow::Error>,
    }
    let window = window.max(1);
    let state = Mutex::new(State {
        next_claim: 0,
        next_emit: 0,
        done: BTreeMap::new(),
        error: None,
    });
    let wake = Condvar::new();
    let tick = Duration::from_millis(20);
    let fail = |e: anyhow::Error| {
        let mut s = state.lock().unwrap_or_else(|p| p.into_inner());
        // Keep the first real failure rather than its cancellation consequences.
        if s.error
            .as_ref()
            .is_none_or(|old| old.is::<Cancelled>() && !e.is::<Cancelled>())
        {
            s.error = Some(e);
        }
        drop(s);
        cancel.cancel();
        wake.notify_all();
    };

    std::thread::scope(|scope| -> Result<()> {
        let mut guard = CancelOnDrop(Some(cancel));
        for index in 0..workers.max(1) {
            std::thread::Builder::new()
                .name(format!("tenzor-chunk-{index}"))
                .spawn_scoped(scope, || {
                    loop {
                        let started = metrics.start();
                        let claimed = {
                            let mut s = state.lock().unwrap_or_else(|p| p.into_inner());
                            loop {
                                if s.error.is_some()
                                    || cancel.check().is_err()
                                    || s.next_claim >= count
                                {
                                    break None;
                                }
                                if s.next_claim < s.next_emit + window {
                                    s.next_claim += 1;
                                    break Some(s.next_claim - 1);
                                }
                                s = wake
                                    .wait_timeout(s, tick)
                                    .unwrap_or_else(|p| p.into_inner())
                                    .0;
                            }
                        };
                        metrics.end(Stage::VideoWindowWait, started);
                        let Some(c) = claimed else { break };
                        let result =
                            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| work(c)))
                                .unwrap_or_else(|payload| {
                                    let message = payload
                                        .downcast_ref::<String>()
                                        .map(String::as_str)
                                        .or_else(|| payload.downcast_ref::<&str>().copied())
                                        .unwrap_or("unknown panic");
                                    Err(anyhow!("video decode worker panicked: {message}"))
                                });
                        match result {
                            Ok(value) => {
                                let mut s = state.lock().unwrap_or_else(|p| p.into_inner());
                                s.done.insert(c, value);
                                drop(s);
                                wake.notify_all();
                            }
                            Err(e) => {
                                fail(e);
                                break;
                            }
                        }
                    }
                })
                .context("spawn chunk decoder worker")?;
        }

        let emitted = worker(
            || -> Result<()> {
                for c in 0..count {
                    let started = metrics.start();
                    let value = {
                        let mut s = state.lock().unwrap_or_else(|p| p.into_inner());
                        loop {
                            if let Some(e) = s.error.take() {
                                return Err(e);
                            }
                            cancel.check()?;
                            if let Some(v) = s.done.remove(&c) {
                                break v;
                            }
                            s = wake
                                .wait_timeout(s, tick)
                                .unwrap_or_else(|p| p.into_inner())
                                .0;
                        }
                    };
                    wake.notify_all();
                    metrics.end(Stage::VideoReorderWait, started);
                    emit(c, value)?;
                    // Return the admission credit only after the emitted payload is
                    // dropped. The configured window now includes this chunk.
                    state.lock().unwrap_or_else(|p| p.into_inner()).next_emit = c + 1;
                    wake.notify_all();
                }
                Ok(())
            },
            cancel,
            "chunk emitter",
        );
        if let Err(e) = emitted {
            fail(e);
        }
        guard.0 = None;
        Ok(())
    })?;
    let mut s = state.into_inner().unwrap_or_else(|p| p.into_inner());
    match s.error.take() {
        Some(e) => Err(e),
        None => Ok(()),
    }
}

struct ChunkOutput {
    pictures: Vec<(Selection, Vec<f32>)>,
    resized: usize,
    skipped: usize,
}

/// Decode with `par.workers` independent decoders when the clip has valid IDR
/// cut points; otherwise fall back to the unchanged single-decoder path.
pub fn decode_h264_mp4_parallel<F>(
    mp4: &Mp4<'_>,
    config: VideoConfig,
    par: ParallelConfig,
    metrics: &Metrics,
    cancel: &Cancellation,
    input_mapping: Option<&memmap2::Mmap>,
    mut on_epoch: F,
) -> Result<VideoInfo>
where
    F: FnMut(usize, i64, &[f32]) -> Result<()>,
{
    let fallback = |reason: &str, on_epoch: F| {
        if config.diagnostics {
            eprintln!("video_mode=single_fallback reason=\"{reason}\"");
        }
        decode_h264_mp4(mp4, config, metrics, cancel, on_epoch)
    };
    if par.workers < 2 {
        return fallback("fewer than 2 workers requested", on_epoch);
    }
    if config.decoder_threads != 0 {
        return fallback("decoder threads are single-decoder only", on_epoch);
    }
    let Some(track) = mp4
        .tracks()
        .iter()
        .find(|t| t.kind() == TrackKind::Video && t.codec() == Some(Codec::H264))
    else {
        return fallback("no H.264 track", on_epoch);
    };
    let Ok(timeline) = Timeline::for_track(track, mp4.timescale()) else {
        return fallback("timeline rejected", on_epoch);
    };
    let Some(SampleEntry::Video(entry)) = track.sample_entry() else {
        return fallback("no video sample entry", on_epoch);
    };
    let CodecConfig::Avc(avc) = &entry.config else {
        return fallback("no avcC", on_epoch);
    };
    let (width, height, avcc_raw) = (entry.width as usize, entry.height as usize, avc.raw.clone());
    let Ok(avcc) = parse_avcc_config(&avcc_raw) else {
        return fallback("avcC parse failed", on_epoch);
    };
    if avcc.sps_nals.is_empty() || avcc.pps_nals.is_empty() {
        return fallback("avcC missing SPS/PPS", on_epoch);
    }
    let Ok(color) = video_color(mp4) else {
        return fallback("color validation failed", on_epoch);
    };

    // ---- Metadata pre-pass: timing, IDR flags, selection -------------------
    let plan_started = metrics.start();
    let n = track.sample_count();
    if !plan_fits_budget(n) {
        metrics.end(Stage::VideoParse, plan_started);
        return fallback("planning metadata exceeds 32 MiB allowance", on_epoch);
    }
    let duration_ms_f = timeline.duration_sec * 1000.;
    let mut pts = Vec::with_capacity(n);
    let mut idr = Vec::with_capacity(n);
    for i in 0..n {
        cancel.check()?;
        if i % 128 == 0 {
            release_index_pages(input_mapping)?;
        }
        let Ok(Some(sample)) = track.sample(i) else {
            metrics.end(Stage::VideoParse, plan_started);
            return fallback("sample table read failed", on_epoch);
        };
        pts.push((timeline.pts_sec(sample.cts, track.timescale()) * 1000.).round() as i64);
        idr.push(sample.is_sync && first_vcl_is_idr(sample.data, avcc.length_size));
    }
    release_index_pages(input_mapping)?;
    let in_range = |p: i64| p >= 0 && (p as f64) < duration_ms_f;
    let mut presentation =
        PresentationTimes::new(pts.iter().copied().filter(|p| in_range(*p)).map(Ok));
    let mut presented = Vec::new();
    loop {
        match presentation.next_pts() {
            Ok(Some(p)) => presented.push(p),
            Ok(None) => break,
            Err(_) => {
                metrics.end(Stage::VideoParse, plan_started);
                return fallback("presentation order outside lookahead", on_epoch);
            }
        }
    }
    if presented.is_empty() {
        metrics.end(Stage::VideoParse, plan_started);
        return fallback("no presented pictures", on_epoch);
    }
    if presented.windows(2).any(|w| w[0] == w[1]) {
        metrics.end(Stage::VideoParse, plan_started);
        return fallback("duplicate presentation timestamps", on_epoch);
    }
    let duration_ms = duration_ms_f.round() as u64;
    let total_epochs = duration_ms
        .max(config.audio_duration_ms)
        .div_ceil(config.window_ms) as usize;
    let selections = assign_epochs(&presented, config.window_ms, total_epochs);
    if selections.last().map(|s| s.epochs.1) != Some(total_epochs) {
        metrics.end(Stage::VideoParse, plan_started);
        return fallback("selection does not cover every epoch", on_epoch);
    }
    let chunks = plan_chunks(&pts, &idr, par.chunk_target_ms);
    if chunks.len() < 2 {
        metrics.end(Stage::VideoParse, plan_started);
        return fallback("fewer than 2 IDR chunks", on_epoch);
    }
    // Per chunk: sorted in-range presentation times and the selections inside them.
    let mut chunk_presented: Vec<Vec<i64>> = Vec::with_capacity(chunks.len());
    let mut chunk_selected: Vec<Range<usize>> = Vec::with_capacity(chunks.len());
    let mut sel_cursor = 0;
    for r in &chunks {
        let mut p: Vec<i64> = pts[r.clone()]
            .iter()
            .copied()
            .filter(|p| in_range(*p))
            .collect();
        p.sort_unstable();
        let begin = sel_cursor;
        if let Some(&last) = p.last() {
            while sel_cursor < selections.len() && selections[sel_cursor].pts <= last {
                sel_cursor += 1;
            }
        }
        chunk_selected.push(begin..sel_cursor);
        chunk_presented.push(p);
    }
    if sel_cursor != selections.len() {
        metrics.end(Stage::VideoParse, plan_started);
        return fallback("selection/chunk assignment mismatch", on_epoch);
    }
    let tensor_bytes = 3 * config.resolution * config.resolution * std::mem::size_of::<f32>();
    let max_chunk_bytes = chunk_selected.iter().map(|r| r.len()).max().unwrap_or(0) * tensor_bytes;
    let window = (par.workers + 1)
        .min(
            par.buffer_bytes
                .checked_div(max_chunk_bytes)
                .unwrap_or(par.workers + 1),
        )
        .max(1);
    let workers = par.workers.min(window).min(chunks.len());
    metrics.end(Stage::VideoParse, plan_started);
    if workers < 2 {
        return fallback("byte budget allows fewer than 2 in-flight chunks", on_epoch);
    }
    if config.diagnostics {
        eprintln!(
            "video_mode=chunked workers={workers} window={window} chunks={} max_chunk_tensor_bytes={max_chunk_bytes} bound_bytes={}",
            chunks.len(),
            window * max_chunk_bytes
        );
    }

    drop(presented);
    drop(idr);
    let parameter_sets = annexb_parameter_sets(&avcc_raw)?;

    // ---- Decode one chunk on a fresh decoder --------------------------------
    let decode_chunk = |c: usize| -> Result<ChunkOutput> {
        let range = chunks[c].clone();
        let expected = &chunk_presented[c];
        let wanted = &selections[chunk_selected[c].clone()];
        let mut decoder = Decoder::with_api_config(
            OpenH264API::from_source(),
            DecoderConfig::new().flush_after_decode(Flush::NoFlush),
        )?;
        decoder
            .decode(&parameter_sets)
            .context("initialize OpenH264 SPS/PPS")?;
        let mut heap = BinaryHeap::new();
        let mut out = ChunkOutput {
            pictures: Vec::with_capacity(wanted.len()),
            resized: 0,
            skipped: 0,
        };
        // Presentation times are unique here (duplicates fall back to the single path),
        // so a set identifies skipped in-range pictures exactly.
        let mut skipped_pts = BTreeSet::new();
        let mut skipped = 0usize;
        let mut produced = 0usize;
        let mut next_expected = 0usize;
        let mut next_wanted = 0usize;
        let mut emit = |frame: openh264::decoder::DecodedYUV<'_>,
                        heap: &mut BinaryHeap<Reverse<i64>>,
                        skipped_pts: &mut BTreeSet<i64>|
         -> Result<()> {
            let Reverse(p) = heap.pop().context("decoder produced extra picture")?;
            produced += 1;
            let (w, h) = frame.dimensions();
            anyhow::ensure!(
                w == width && h == height,
                "midstream H.264 resolution changes unsupported"
            );
            if in_range(p) {
                // Skipped slots presented before this picture were never decoded.
                while expected
                    .get(next_expected)
                    .is_some_and(|&e| e < p && skipped_pts.remove(&e))
                {
                    next_expected += 1;
                }
                anyhow::ensure!(
                    expected.get(next_expected) == Some(&p),
                    "decoded PTS disagrees with bounded presentation lookahead"
                );
                next_expected += 1;
                if wanted.get(next_wanted).is_some_and(|s| s.pts == p) {
                    let started = metrics.start();
                    let chw = yuv420_to_resized_chw(
                        frame.y(),
                        frame.u(),
                        frame.v(),
                        (w, h),
                        (config.resolution, config.resolution),
                        color,
                    );
                    metrics.end(Stage::VideoResize, started);
                    out.resized += 1;
                    out.pictures.push((wanted[next_wanted], chw));
                    next_wanted += 1;
                }
            }
            Ok(())
        };
        for index in range.clone() {
            cancel.check()?;
            let parse_started = metrics.start();
            let sample = track
                .sample(index)
                .context("read H.264 access unit")?
                .context("read H.264 access unit")?;
            // Never referenced and never selected: skip without decoding. Selections are
            // sorted by presentation time, so a binary search answers membership.
            if config.skip_nonref
                && is_disposable(sample.data, avcc.length_size)
                && wanted.binary_search_by_key(&pts[index], |s| s.pts).is_err()
            {
                skipped += 1;
                if in_range(pts[index]) {
                    skipped_pts.insert(pts[index]);
                }
                metrics.end(Stage::VideoParse, parse_started);
                continue;
            }
            heap.push(Reverse(pts[index]));
            anyhow::ensure!(
                heap.len() <= MAX_REORDER,
                "H.264 reorder queue exceeds 34 pictures"
            );
            let annexb = avcc_to_annexb(sample.data, avcc.length_size)?;
            for nal in rust_h264::nal::parse_avcc(sample.data, avcc.length_size) {
                let sets = match nal.nal_unit_type {
                    rust_h264::nal::NalUnitType::Sps => Some(&avcc.sps_nals),
                    rust_h264::nal::NalUnitType::Pps => Some(&avcc.pps_nals),
                    _ => None,
                };
                if let Some(sets) = sets {
                    anyhow::ensure!(
                        sets.iter().any(|s| s.rbsp == nal.rbsp),
                        "midstream H.264 parameter-set changes unsupported"
                    );
                }
            }
            metrics.end(Stage::VideoParse, parse_started);
            let decode_started = metrics.start();
            let frame = decoder
                .decode(&annexb)
                .with_context(|| format!("decode OpenH264 access unit {index}"))?;
            metrics.end(Stage::VideoDecode, decode_started);
            if let Some(frame) = frame {
                emit(frame, &mut heap, &mut skipped_pts)?;
            }
        }
        let decode_started = metrics.start();
        let remaining = decoder.flush_remaining().context("flush OpenH264")?;
        metrics.end(Stage::VideoDecode, decode_started);
        for frame in remaining {
            emit(frame, &mut heap, &mut skipped_pts)?;
        }
        out.skipped = skipped;
        // Skipped pictures presented after the chunk's last decoded picture.
        while expected
            .get(next_expected)
            .is_some_and(|e| skipped_pts.remove(e))
        {
            next_expected += 1;
        }
        anyhow::ensure!(
            produced + out.skipped == range.len() && skipped_pts.is_empty(),
            "H.264 picture count mismatch in chunk {c}: {produced} vs {} access units",
            range.len() - out.skipped
        );
        anyhow::ensure!(
            next_expected == expected.len() && next_wanted == wanted.len(),
            "incomplete H.264 presentation sequence in chunk {c}"
        );
        Ok(out)
    };

    let mut resized_count = 0;
    let mut skipped_count = 0;
    let mut next_epoch = 0usize;
    run_ordered(
        chunks.len(),
        workers,
        window,
        cancel,
        metrics,
        decode_chunk,
        |_, chunk: ChunkOutput| {
            resized_count += chunk.resized;
            skipped_count += chunk.skipped;
            for (sel, chw) in chunk.pictures {
                let (start, end) = sel.epochs;
                if start != next_epoch {
                    bail!("epoch order mismatch: expected {next_epoch}, got {start}");
                }
                for e in start..end {
                    on_epoch(e, sel.pts, &chw)?;
                }
                next_epoch = end;
            }
            Ok(())
        },
    )?;
    anyhow::ensure!(
        next_epoch == total_epochs,
        "incomplete H.264 presentation sequence"
    );
    Ok(VideoInfo {
        width,
        height,
        duration_ms,
        sample_count: n,
        resized_count,
        skipped_count,
    })
}

fn annexb_parameter_sets(avcc_raw: &[u8]) -> Result<Vec<u8>> {
    let mut out = Vec::new();
    let mut pos = 6;
    let get = |a: usize, b: usize| avcc_raw.get(a..b).context("truncated avcC");
    let sps = *avcc_raw.get(5).context("truncated avcC")? & 31;
    for _ in 0..sps {
        let len = get(pos, pos + 2)?;
        let n = u16::from_be_bytes([len[0], len[1]]) as usize;
        pos += 2;
        out.extend_from_slice(&[0, 0, 0, 1]);
        out.extend_from_slice(get(pos, pos + n)?);
        pos += n;
    }
    let pps = *avcc_raw.get(pos).context("truncated avcC")?;
    pos += 1;
    for _ in 0..pps {
        let len = get(pos, pos + 2)?;
        let n = u16::from_be_bytes([len[0], len[1]]) as usize;
        pos += 2;
        out.extend_from_slice(&[0, 0, 0, 1]);
        out.extend_from_slice(get(pos, pos + n)?);
        pos += n;
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn chunked_selection_matches_streaming_selection() {
        // Same streaming loop as v0.1.6 decode_h264_mp4.
        for window in [50, 330, 500, 1000] {
            let presented: Vec<i64> = (0..900)
                .map(|i| (i as f64 * 1000. / 30.).round() as i64)
                .collect();
            let total = 30_000usize.div_ceil(window as usize);
            let mut streaming = Vec::new();
            let mut epoch = 0;
            for (i, &p) in presented.iter().enumerate() {
                let end = epoch_end(p, presented.get(i + 1).copied(), window, total);
                while epoch < end {
                    streaming.push(p);
                    epoch += 1;
                }
            }
            let mut chunked = Vec::new();
            for s in assign_epochs(&presented, window, total) {
                chunked.extend(std::iter::repeat_n(s.pts, s.epochs.1 - s.epochs.0));
            }
            assert_eq!(chunked, streaming);
        }
    }

    #[test]
    fn chunks_cut_only_at_idr_with_clean_presentation_boundary() {
        // Decode order I P B B | I P B B, 4 pictures per GOP, 100ms spacing.
        let pts = [0, 300, 100, 200, 400, 700, 500, 600];
        let idr = [true, false, false, false, true, false, false, false];
        assert_eq!(plan_chunks(&pts, &idr, 0), vec![0..4, 4..8]);
        // Target larger than the clip keeps one chunk.
        assert_eq!(plan_chunks(&pts, &idr, 10_000), vec![0..8]);
        // An "IDR" whose predecessor presents later (open GOP-like) is not a cut.
        let pts_open = [0, 500, 100, 200, 400, 700, 450, 600];
        assert_eq!(plan_chunks(&pts_open, &idr, 0), vec![0..8]);
        // Sync-but-not-IDR never splits.
        let none = [true, false, false, false, false, false, false, false];
        assert_eq!(plan_chunks(&pts, &none, 0), vec![0..8]);
    }

    #[test]
    fn first_vcl_idr_detection() {
        // SEI(6) then IDR(5)
        let idr = [0, 0, 0, 2, 0x06, 0x00, 0, 0, 0, 2, 0x65, 0x88];
        assert!(first_vcl_is_idr(&idr, 4));
        // SEI then non-IDR slice(1)
        let p = [0, 0, 0, 2, 0x06, 0x00, 0, 0, 0, 2, 0x41, 0x88];
        assert!(!first_vcl_is_idr(&p, 4));
        assert!(!first_vcl_is_idr(&[0, 0, 0, 9, 0x65], 4));
        assert!(!first_vcl_is_idr(&[1, 0x65, 1, 0x41], 1));
        assert!(!first_vcl_is_idr(&[1, 0xe5], 1));
        assert!(!first_vcl_is_idr(&[1, 0x65, 0], 1));
    }

    #[test]
    fn ordered_map_preserves_order_and_bounds_window() {
        let cancel = Cancellation::new();
        let in_flight = AtomicUsize::new(0);
        let peak = AtomicUsize::new(0);
        let mut seen = Vec::new();
        run_ordered(
            50,
            4,
            5,
            &cancel,
            &Metrics::new(false),
            |c| {
                let now = in_flight.fetch_add(1, Ordering::SeqCst) + 1;
                peak.fetch_max(now, Ordering::SeqCst);
                // Reverse-skewed work so later chunks often finish first.
                std::thread::sleep(Duration::from_micros(((50 - c) * 150) as u64));
                in_flight.fetch_sub(1, Ordering::SeqCst);
                Ok(c * 10)
            },
            |c, v| {
                seen.push((c, v));
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(seen, (0..50).map(|c| (c, c * 10)).collect::<Vec<_>>());
        assert!(peak.load(Ordering::SeqCst) <= 4);
    }

    #[test]
    fn ordered_map_failures_and_panics_do_not_deadlock() {
        for mode in 0..4 {
            let (tx, rx) = std::sync::mpsc::channel();
            std::thread::spawn(move || {
                let cancel = Cancellation::new();
                let r = run_ordered(
                    40,
                    4,
                    5,
                    &cancel,
                    &Metrics::new(false),
                    |c| match (mode, c) {
                        (0, 0) | (1, 20) | (2, 39) => Err(anyhow!("injected decode failure")),
                        (3, 7) => panic!("injected panic"),
                        _ => {
                            std::thread::sleep(Duration::from_millis(1));
                            Ok(c)
                        }
                    },
                    |_, _| Ok(()),
                );
                tx.send(r.map_err(|e| e.to_string())).unwrap();
            });
            let r = rx
                .recv_timeout(Duration::from_secs(5))
                .expect("ordered map deadlocked");
            let msg = r.unwrap_err();
            assert!(msg.contains("injected"), "{msg}");
        }
        // Emitter (collector-side) failure also stops workers.
        let cancel = Cancellation::new();
        let r = run_ordered(1000, 4, 5, &cancel, &Metrics::new(false), Ok, |c, _| {
            if c == 3 {
                Err(anyhow!("writer failed"))
            } else {
                Ok(())
            }
        });
        assert!(r.unwrap_err().to_string().contains("writer failed"));
    }

    #[test]
    fn external_cancellation_stops_workers() {
        let cancel = Cancellation::new();
        let r = run_ordered(
            1000,
            4,
            5,
            &cancel,
            &Metrics::new(false),
            |c| {
                if c == 10 {
                    cancel.cancel();
                }
                cancel.check()?;
                Ok(c)
            },
            |_, _| Ok(()),
        );
        assert!(r.unwrap_err().is::<Cancelled>());
    }
}

#[cfg(test)]
mod regression_tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    struct Live<'a>(&'a AtomicUsize);
    impl Drop for Live<'_> {
        fn drop(&mut self) {
            self.0.fetch_sub(1, Ordering::SeqCst);
        }
    }
    #[test]
    fn regression_budget_includes_emitting_chunk() {
        let live = AtomicUsize::new(0);
        let peak = AtomicUsize::new(0);
        run_ordered(
            30,
            4,
            2,
            &Cancellation::new(),
            &Metrics::new(false),
            |_| {
                let now = live.fetch_add(1, Ordering::SeqCst) + 1;
                peak.fetch_max(now, Ordering::SeqCst);
                Ok(Live(&live))
            },
            |_, value| {
                std::thread::sleep(Duration::from_millis(3));
                drop(value);
                Ok(())
            },
        )
        .unwrap();
        assert!(
            peak.load(Ordering::SeqCst) <= 2,
            "live chunk payloads exceeded budget: {}",
            peak.load(Ordering::SeqCst)
        );
    }
    #[test]
    fn regression_emitter_panic_cancels_before_join() {
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let result = std::panic::catch_unwind(|| {
                run_ordered(
                    10000,
                    4,
                    2,
                    &Cancellation::new(),
                    &Metrics::new(false),
                    Ok,
                    |_, _| panic!("injected emitter panic"),
                )
            });
            tx.send(result.is_err() || result.unwrap().is_err())
                .unwrap();
        });
        assert!(
            rx.recv_timeout(Duration::from_secs(2))
                .expect("emitter panic stranded scoped workers")
        );
    }
}

#[cfg(test)]
mod planning_budget_tests {
    use super::*;
    #[test]
    fn planning_cap_rejects_excess_and_overflow_before_allocation() {
        assert!(plan_fits_budget(PLAN_BUDGET_BYTES / PLAN_BYTES_PER_SAMPLE));
        assert!(!plan_fits_budget(
            PLAN_BUDGET_BYTES / PLAN_BYTES_PER_SAMPLE + 1
        ));
        assert!(!plan_fits_budget(usize::MAX));
    }
}
