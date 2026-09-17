use anyhow::{Context, Result, bail};

use crate::media::Timeline;
use crate::metrics::{Metrics, Stage};
use mp4io::{Codec, CodecConfig, Mp4, SampleEntry, TrackKind};
use openh264::{
    OpenH264API,
    decoder::{Decoder, DecoderConfig, Flush},
    formats::YUVSource,
};
use rust_h264::nal::parse_avcc_config;
use std::{
    cmp::Reverse,
    collections::{BTreeMap, BinaryHeap},
};

#[derive(Debug, Clone)]
pub struct VideoInfo {
    pub width: usize,
    pub height: usize,
    pub duration_ms: u64,
    pub sample_count: usize,
    pub resized_count: usize,
    /// Access units never sent to the decoder: non-reference and not epoch-selected.
    pub skipped_count: usize,
}

#[derive(Clone, Copy)]
pub struct VideoConfig {
    pub resolution: usize,
    pub window_ms: u64,
    pub audio_duration_ms: u64,
    pub decoder_threads: usize,
    /// Skip decoding access units that no picture references and no epoch selects.
    pub skip_nonref: bool,
    /// Print decoder-mode diagnostics on stderr.
    pub diagnostics: bool,
}

/// True when no other picture can depend on this access unit: every VCL NAL is a
/// non-IDR slice with `nal_ref_idc == 0`, and the unit carries nothing else that
/// affects decoder state (only SEI, access-unit delimiters or filler may accompany it).
/// Malformed units return false so normal decoding reports the input error.
pub(crate) fn is_disposable(mut data: &[u8], length_size: usize) -> bool {
    if !(1..=4).contains(&length_size) {
        return false;
    }
    let mut slices = 0;
    while !data.is_empty() {
        if data.len() < length_size {
            return false;
        }
        let n = data[..length_size]
            .iter()
            .fold(0usize, |v, b| (v << 8) | *b as usize);
        data = &data[length_size..];
        if n == 0 || n > data.len() || data[0] & 0x80 != 0 {
            return false;
        }
        let (ref_idc, kind) = ((data[0] >> 5) & 3, data[0] & 0x1f);
        match kind {
            1 if ref_idc == 0 => slices += 1,
            6 | 9 | 12 => (),
            _ => return false,
        }
        data = &data[n..];
    }
    slices > 0
}

/// Streaming answer to "does any epoch select the picture presented at `pts`?",
/// using the same bounded presentation lookahead and midpoint rules as decoding.
/// Unknown answers are reported as needed, so the oracle can only disable skipping.
struct SelectionOracle<I> {
    presentation: PresentationTimes<I>,
    current: Option<i64>,
    next: Option<i64>,
    epoch: usize,
    window_ms: u64,
    total: usize,
    needed: BTreeMap<i64, bool>,
}
impl<I: Iterator<Item = Result<i64>>> SelectionOracle<I> {
    fn new(source: I, window_ms: u64, total: usize) -> Result<Self> {
        let mut presentation = PresentationTimes::new(source);
        let current = presentation.next_pts()?;
        let next = presentation.next_pts()?;
        Ok(Self {
            presentation,
            current,
            next,
            epoch: 0,
            window_ms,
            total,
            needed: BTreeMap::new(),
        })
    }
    /// `pts` must be inside the presentation range.
    fn needed(&mut self, pts: i64) -> Result<bool> {
        while let Some(current) = self.current.filter(|&c| c <= pts) {
            let end = epoch_end(current, self.next, self.window_ms, self.total);
            let selected = self.epoch < end;
            if selected {
                self.epoch = end;
            }
            *self.needed.entry(current).or_insert(false) |= selected;
            self.current = self.next;
            self.next = self.presentation.next_pts()?;
        }
        // Decode order trails presentation by at most the reorder depth; keep a margin.
        while self.needed.len() > 4 * MAX_REORDER {
            self.needed.pop_first();
        }
        Ok(self.needed.get(&pts).copied().unwrap_or(true))
    }
}

/// Decode every access unit, converting only nearest-epoch pictures.
/// The callback borrows one tensor even when it serves multiple epochs.
pub fn decode_h264_mp4<F>(
    mp4: &Mp4<'_>,
    config: VideoConfig,
    metrics: &Metrics,
    cancel: &crate::concurrency::Cancellation,
    mut on_epoch: F,
) -> Result<VideoInfo>
where
    F: FnMut(usize, i64, &[f32]) -> Result<()>,
{
    let VideoConfig {
        resolution,
        window_ms,
        audio_duration_ms,
        decoder_threads,
        skip_nonref,
        ..
    } = config;
    anyhow::ensure!(window_ms > 0, "epoch window must be positive");
    let track = mp4
        .tracks()
        .iter()
        .find(|t| t.kind() == TrackKind::Video && t.codec() == Some(Codec::H264))
        .context("no supported H.264 video track found")?;

    let timeline = Timeline::for_track(track, mp4.timescale())?;
    let entry = track
        .sample_entry()
        .context("H.264 track missing sample entry")?;
    let (width, height, avcc_raw) = match entry {
        SampleEntry::Video(v) => {
            let raw = match &v.config {
                CodecConfig::Avc(c) => c.raw.clone(),
                _ => bail!("H.264 sample entry has no avcC configuration"),
            };
            (v.width as usize, v.height as usize, raw)
        }
        _ => bail!("selected video track did not have a video sample entry"),
    };

    let config = parse_avcc_config(&avcc_raw)
        .map_err(anyhow::Error::msg)
        .context("parse avcC configuration")?;
    anyhow::ensure!(
        !config.sps_nals.is_empty() && !config.pps_nals.is_empty(),
        "avcC must contain SPS and PPS"
    );
    let decoder_config = DecoderConfig::new().flush_after_decode(Flush::NoFlush);
    #[cfg(feature = "experimental-decoder-threads")]
    let decoder_config = if decoder_threads > 0 {
        // Experimental subprocess tests only. Patched wrapper sets this BEFORE Initialize.
        unsafe { decoder_config.num_threads(decoder_threads as u32) }
    } else {
        decoder_config
    };
    #[cfg(not(feature = "experimental-decoder-threads"))]
    anyhow::ensure!(
        decoder_threads == 0,
        "decoder threading requires experimental feature"
    );
    let mut decoder = Decoder::with_api_config(OpenH264API::from_source(), decoder_config)?;
    // OpenH264 accepts Annex-B access units, while MP4 stores AVCC lengths.
    let mut parameter_sets = Vec::new();
    let mut pos = 6;
    for _ in 0..(avcc_raw[5] & 31) {
        let n = u16::from_be_bytes([avcc_raw[pos], avcc_raw[pos + 1]]) as usize;
        pos += 2;
        parameter_sets.extend_from_slice(&[0, 0, 0, 1]);
        parameter_sets.extend_from_slice(&avcc_raw[pos..pos + n]);
        pos += n;
    }
    let count = avcc_raw[pos];
    pos += 1;
    for _ in 0..count {
        let n = u16::from_be_bytes([avcc_raw[pos], avcc_raw[pos + 1]]) as usize;
        pos += 2;
        parameter_sets.extend_from_slice(&[0, 0, 0, 1]);
        parameter_sets.extend_from_slice(&avcc_raw[pos..pos + n]);
        pos += n;
    }
    decoder
        .decode(&parameter_sets)
        .context("initialize OpenH264 SPS/PPS")?;
    let mut pts_ms = BinaryHeap::new();
    let color = video_color(mp4)?;
    let in_range = |pts: i64| pts >= 0 && (pts as f64) < timeline.duration_sec * 1000.;
    // Metadata-only lookahead avoids copying a borrowed decoder picture just to
    // learn its successor's PTS. The heap is capped, independent of clip duration.
    let timestamps = || {
        track
            .samples()
            .map(|sample| {
                let sample = sample.context("read H.264 presentation metadata")?;
                Ok((timeline.pts_sec(sample.cts, track.timescale()) * 1000.).round() as i64)
            })
            .filter(|pts: &Result<i64>| match pts {
                Ok(pts) => in_range(*pts),
                Err(_) => true,
            })
    };
    let mut presentation = PresentationTimes::new(timestamps());
    let mut current_pts = presentation.next_pts()?;
    anyhow::ensure!(
        current_pts.is_some(),
        "H.264 decoder produced no display frames"
    );
    let mut next_pts = presentation.next_pts()?;
    let duration_ms = (timeline.duration_sec * 1000.0).round() as u64;
    let total_epochs = duration_ms.max(audio_duration_ms).div_ceil(window_ms) as usize;
    // A second, independent lookahead decides skips before decoding. Any metadata
    // error just disables skipping, so errors surface in the original order below.
    let mut oracle = (skip_nonref && decoder_threads == 0)
        .then(|| SelectionOracle::new(timestamps(), window_ms, total_epochs).ok())
        .flatten();
    // In-range presentation times whose access units were skipped, with multiplicity.
    let mut skipped_pts: BTreeMap<i64, usize> = BTreeMap::new();
    let mut skipped_count = 0usize;
    let take_skipped = |skipped: &mut BTreeMap<i64, usize>, pts: Option<i64>| -> Option<i64> {
        let pts = pts?;
        let count = skipped.get_mut(&pts)?;
        *count -= 1;
        if *count == 0 {
            skipped.remove(&pts);
        }
        Some(pts)
    };
    let mut epoch = 0;
    let mut resized_count = 0;
    let mut out_index = 0usize;
    let mut emit = |frame: openh264::decoder::DecodedYUV<'_>,
                    pts_ms: &mut BinaryHeap<Reverse<i64>>,
                    skipped: &mut BTreeMap<i64, usize>|
     -> Result<()> {
        let Reverse(pts) = pts_ms.pop().context("decoder produced extra picture")?;
        out_index += 1;
        let (w, h) = frame.dimensions();
        anyhow::ensure!(
            w == width && h == height,
            "midstream H.264 resolution changes unsupported"
        );
        if in_range(pts) {
            // Earlier presentation slots that were never decoded advance the lookahead
            // exactly as a decoded, unselected picture would.
            while let Some(skipped_pts) =
                take_skipped(skipped, current_pts.filter(|&current| current < pts))
            {
                anyhow::ensure!(
                    epoch >= epoch_end(skipped_pts, next_pts, window_ms, total_epochs),
                    "skipped H.264 picture {skipped_pts} ms was selected for an epoch"
                );
                current_pts = next_pts;
                next_pts = presentation.next_pts()?;
            }
            anyhow::ensure!(
                current_pts == Some(pts),
                "decoded PTS disagrees with bounded presentation lookahead"
            );
            let end = epoch_end(pts, next_pts, window_ms, total_epochs);
            if epoch < end {
                let started = metrics.start();
                let chw = yuv420_to_resized_chw(
                    frame.y(),
                    frame.u(),
                    frame.v(),
                    (w, h),
                    (resolution, resolution),
                    color,
                );
                metrics.end(Stage::VideoResize, started);
                resized_count += 1;
                while epoch < end {
                    on_epoch(epoch, pts, &chw)?;
                    epoch += 1;
                }
            }
            current_pts = next_pts;
            next_pts = presentation.next_pts()?;
        }
        Ok(())
    };
    for (index, sample) in track.samples().enumerate() {
        cancel.check()?;
        let parse_started = metrics.start();
        let sample = sample.context("read H.264 access unit")?;
        let pts = (timeline.pts_sec(sample.cts, track.timescale()) * 1000.).round() as i64;
        // Some(true) = skip, Some(false) = decode, None = oracle failed (stop skipping).
        let decision = match oracle.as_mut() {
            Some(oracle) if is_disposable(sample.data, config.length_size) => {
                if in_range(pts) {
                    oracle.needed(pts).ok().map(|needed| !needed)
                } else {
                    Some(true)
                }
            }
            _ => Some(false),
        };
        if decision.is_none() {
            oracle = None;
        }
        if decision == Some(true) {
            skipped_count += 1;
            if in_range(pts) {
                *skipped_pts.entry(pts).or_default() += 1;
            }
            metrics.end(Stage::VideoParse, parse_started);
            continue;
        }
        pts_ms.push(Reverse(pts));
        anyhow::ensure!(
            pts_ms.len() <= 34,
            "H.264 reorder queue exceeds 34 pictures"
        );
        let annexb = avcc_to_annexb(sample.data, config.length_size)?;
        for nal in rust_h264::nal::parse_avcc(sample.data, config.length_size) {
            let sets = match nal.nal_unit_type {
                rust_h264::nal::NalUnitType::Sps => Some(&config.sps_nals),
                rust_h264::nal::NalUnitType::Pps => Some(&config.pps_nals),
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
            emit(frame, &mut pts_ms, &mut skipped_pts)?;
        }
    }
    let decode_started = metrics.start();
    let remaining = decoder.flush_remaining().context("flush OpenH264")?;
    metrics.end(Stage::VideoDecode, decode_started);
    for frame in remaining {
        emit(frame, &mut pts_ms, &mut skipped_pts)?;
    }
    // Skipped pictures presented after the last decoded one.
    while let Some(skipped) = take_skipped(&mut skipped_pts, current_pts) {
        anyhow::ensure!(
            epoch >= epoch_end(skipped, next_pts, window_ms, total_epochs),
            "skipped H.264 picture {skipped} ms was selected for an epoch"
        );
        current_pts = next_pts;
        next_pts = presentation.next_pts()?;
    }
    anyhow::ensure!(
        out_index + skipped_count == track.sample_count(),
        "H.264 picture count mismatch: {out_index} vs {} access units",
        track.sample_count() - skipped_count
    );
    anyhow::ensure!(
        current_pts.is_none() && epoch == total_epochs && skipped_pts.is_empty(),
        "incomplete H.264 presentation sequence"
    );
    Ok(VideoInfo {
        width,
        height,
        duration_ms: (timeline.duration_sec * 1000.0).round() as u64,
        sample_count: track.sample_count(),
        resized_count,
        skipped_count,
    })
}

// Keep the same earlier-picture tie break and integer midpoint as v0.1.4.
pub(crate) fn epoch_end(pts: i64, next_pts: Option<i64>, window_ms: u64, total: usize) -> usize {
    next_pts.map_or(total, |next| {
        let midpoint = pts + (next - pts) / 2;
        ((midpoint as u64 / window_ms + 1) as usize).min(total)
    })
}

pub(crate) const MAX_REORDER: usize = 34;
pub(crate) struct PresentationTimes<I> {
    source: I,
    heap: BinaryHeap<Reverse<i64>>,
    previous: Option<i64>,
}
impl<I: Iterator<Item = Result<i64>>> PresentationTimes<I> {
    pub(crate) fn new(source: I) -> Self {
        Self {
            source,
            heap: BinaryHeap::new(),
            previous: None,
        }
    }
    pub(crate) fn next_pts(&mut self) -> Result<Option<i64>> {
        while self.heap.len() < MAX_REORDER {
            match self.source.next() {
                Some(pts) => self.heap.push(Reverse(pts?)),
                None => break,
            }
        }
        let pts = self.heap.pop().map(|Reverse(pts)| pts);
        if let (Some(previous), Some(pts)) = (self.previous, pts) {
            anyhow::ensure!(
                pts >= previous,
                "H.264 presentation reordering exceeds supported lookahead"
            );
        }
        self.previous = pts;
        Ok(pts)
    }
}

/// Resize YUV420 directly into normalized CHW RGB, avoiding an intermediate full-resolution RGB frame.
pub(crate) fn yuv420_to_resized_chw(
    y: &[u8],
    u: &[u8],
    v: &[u8],
    source_size: (usize, usize),
    destination_size: (usize, usize),
    color: Color,
) -> Vec<f32> {
    let (src_w, src_h) = source_size;
    let (dst_w, dst_h) = destination_size;
    let mut out = vec![0.0f32; 3 * dst_w * dst_h];
    if src_w == 0 || src_h == 0 || dst_w == 0 || dst_h == 0 {
        return out;
    }
    let y_stride = (y.len() / src_h).max(src_w);
    let chroma_h = src_h.div_ceil(2);
    let chroma_w = src_w.div_ceil(2);
    let u_stride = (u.len() / chroma_h).max(chroma_w);
    let v_stride = (v.len() / chroma_h).max(chroma_w);
    let plane = dst_w * dst_h;

    for dy in 0..dst_h {
        let sy = if dst_h > 1 {
            dy * (src_h - 1) / (dst_h - 1)
        } else {
            0
        };
        for dx in 0..dst_w {
            let sx = if dst_w > 1 {
                dx * (src_w - 1) / (dst_w - 1)
            } else {
                0
            };
            let yy = *y.get(sy * y_stride + sx).unwrap_or(&16) as f32;
            let cx = (sx / 2).min(chroma_w.saturating_sub(1));
            let cy = (sy / 2).min(chroma_h.saturating_sub(1));
            let uu = *u.get(cy * u_stride + cx).unwrap_or(&128) as f32;
            let vv = *v.get(cy * v_stride + cx).unwrap_or(&128) as f32;

            // Honor the SPS color matrix and range recorded in the Arrow metadata.
            let c = if color.full {
                yy
            } else {
                (yy - 16.).max(0.) * (255. / 219.)
            };
            let d = uu - 128.0;
            let e = vv - 128.0;
            let chroma = if color.full { 1. } else { 255. / 224. };
            let (kr, kb) = if color.bt709 {
                (0.2126, 0.0722)
            } else {
                (0.299, 0.114)
            };
            let kg = 1. - kr - kb;
            let r = (c + (2. - 2. * kr) * e * chroma).clamp(0., 255.);
            let g =
                (c - 2. * kb * (1. - kb) / kg * d * chroma - 2. * kr * (1. - kr) / kg * e * chroma)
                    .clamp(0., 255.);
            let b = (c + (2. - 2. * kb) * d * chroma).clamp(0., 255.);

            let i = dy * dst_w + dx;
            out[i] = r / 127.5 - 1.0;
            out[plane + i] = g / 127.5 - 1.0;
            out[2 * plane + i] = b / 127.5 - 1.0;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selection_matches_legacy_midpoints_gaps_ties_and_tail() {
        for pts in [
            vec![0],
            vec![0, 1000, 2000],
            vec![0, 0, 33, 500, 501],
            vec![80, 117, 401, 1200, 1337],
        ] {
            for window in [50, 330, 500, 1000] {
                let total = 80;
                let mut legacy = Vec::new();
                for pair in pts.windows(2) {
                    let midpoint = pair[0] + (pair[1] - pair[0]) / 2;
                    while legacy.len() < total && (legacy.len() as u64 * window) as i64 <= midpoint
                    {
                        legacy.push(pair[0]);
                    }
                }
                legacy.resize(total, *pts.last().unwrap());
                let mut selected = Vec::new();
                for (i, &p) in pts.iter().enumerate() {
                    let end = epoch_end(p, pts.get(i + 1).copied(), window, total);
                    selected.resize(end, p);
                }
                assert_eq!(selected, legacy);
            }
        }
    }

    #[test]
    fn presentation_lookahead_orders_b_frames_with_bounded_memory() {
        let input = (0..300).flat_map(|g| [g * 4, g * 4 + 3, g * 4 + 1, g * 4 + 2]);
        let mut pts = PresentationTimes::new(input.map(Ok));
        for expected in 0..1200 {
            assert_eq!(pts.next_pts().unwrap(), Some(expected));
            assert!(pts.heap.len() < MAX_REORDER);
        }
        assert_eq!(pts.next_pts().unwrap(), None);
    }

    #[test]
    fn presentation_lookahead_rejects_excessive_reordering_and_parse_errors() {
        let mut pts = PresentationTimes::new((1..=70).chain([0]).map(Ok));
        let mut failed = false;
        for _ in 0..72 {
            if pts.next_pts().is_err() {
                failed = true;
                break;
            }
        }
        assert!(failed);
        let mut pts =
            PresentationTimes::new([Ok(0), Err(anyhow::anyhow!("bad sample"))].into_iter());
        assert!(pts.next_pts().is_err());
    }

    #[test]
    fn thirty_fps_selects_exactly_two_pictures_per_second() {
        let timestamps: Vec<i64> = (0..9900)
            .map(|i| (i as f64 * 1000. / 30.).round() as i64)
            .collect();
        let mut epoch = 0;
        let mut resized = 0;
        for (i, &pts) in timestamps.iter().enumerate() {
            let end = epoch_end(pts, timestamps.get(i + 1).copied(), 500, 660);
            if epoch < end {
                resized += 1;
                epoch = end;
            }
        }
        assert_eq!((epoch, resized), (660, 660));
    }

    fn avcc(nals: &[&[u8]]) -> Vec<u8> {
        nals.iter()
            .flat_map(|n| {
                (n.len() as u32)
                    .to_be_bytes()
                    .into_iter()
                    .chain(n.iter().copied())
            })
            .collect()
    }

    #[test]
    fn disposable_only_for_unreferenced_non_idr_slices() {
        let nonref_slice: &[u8] = &[0x01, 0x88];
        let sei: &[u8] = &[0x06, 0x05];
        let aud: &[u8] = &[0x09, 0x10];
        assert!(is_disposable(&avcc(&[nonref_slice]), 4));
        assert!(is_disposable(
            &avcc(&[aud, sei, nonref_slice, nonref_slice]),
            4
        ));
        for kept in [
            avcc(&[&[0x21, 0x88]]),               // nal_ref_idc 1
            avcc(&[&[0x41, 0x88]]),               // nal_ref_idc 2
            avcc(&[&[0x65, 0x88]]),               // IDR
            avcc(&[nonref_slice, &[0x41, 0x88]]), // one referenced slice
            avcc(&[&[0x67, 0x42], nonref_slice]), // SPS changes decoder state
            avcc(&[nonref_slice, &[0x0a]]),       // end of sequence
            avcc(&[&[0x02, 0x88]]),               // data partitioning
            avcc(&[sei]),                         // no slice at all
            vec![],
            vec![0, 0, 0, 9, 0x01],    // truncated payload
            vec![0, 0, 0, 1, 0x81],    // forbidden bit
            vec![0, 0, 0, 2, 0x01],    // truncated payload
            vec![0, 0, 0, 1, 0x01, 0], // trailing partial length
        ] {
            assert!(!is_disposable(&kept, 4), "{kept:?}");
        }
        assert!(is_disposable(&[2, 0x01, 0x88], 1));
        assert!(!is_disposable(&avcc(&[nonref_slice]), 5));
    }

    #[test]
    fn selection_oracle_matches_upfront_selection_in_decode_order() {
        // Decode order is I P B B per group; PTS gaps and duplicates exercise ties and sparsity.
        let patterns: Vec<Vec<i64>> = vec![
            (0..200)
                .flat_map(|g| [g * 4, g * 4 + 3, g * 4 + 1, g * 4 + 2])
                .map(|f| (f as f64 * 1000. / 30.).round() as i64)
                .collect(),
            (0..60)
                .flat_map(|g| [g * 900, g * 900 + 700, g * 900 + 100, g * 900 + 350])
                .collect(),
            vec![0, 40, 0, 20, 80, 120, 80, 100, 160, 160, 140, 150],
        ];
        for decode_order in patterns {
            let mut presented = decode_order.clone();
            presented.sort_unstable();
            for window in [50, 330, 500, 1000] {
                let total =
                    ((*presented.last().unwrap() as u64) + window).div_ceil(window) as usize;
                let mut selected = std::collections::BTreeSet::new();
                let mut epoch = 0;
                for (i, &p) in presented.iter().enumerate() {
                    let end = epoch_end(p, presented.get(i + 1).copied(), window, total);
                    if epoch < end {
                        selected.insert(p);
                        epoch = end;
                    }
                }
                let mut oracle =
                    SelectionOracle::new(decode_order.iter().copied().map(Ok), window, total)
                        .unwrap();
                for &p in &decode_order {
                    assert_eq!(
                        oracle.needed(p).unwrap(),
                        selected.contains(&p),
                        "pts {p}, window {window}"
                    );
                }
            }
        }
    }

    #[test]
    fn neutral_black_yuv_is_near_black_rgb() {
        let y = vec![16u8; 16];
        let u = vec![128u8; 4];
        let v = vec![128u8; 4];
        let out = yuv420_to_resized_chw(
            &y,
            &u,
            &v,
            (4, 4),
            (2, 2),
            Color {
                bt709: false,
                full: false,
            },
        );
        assert!(out.iter().all(|x| (*x + 1.0).abs() < 0.03));
    }
}

/// Unlike the dependency's lenient parser, reject every truncated/empty NAL.
pub(crate) fn avcc_to_annexb(mut data: &[u8], length_size: usize) -> Result<Vec<u8>> {
    let mut out = Vec::with_capacity(data.len() + 64);
    anyhow::ensure!(!data.is_empty(), "empty AVC sample");
    while !data.is_empty() {
        anyhow::ensure!(data.len() >= length_size, "truncated AVCC NAL length");
        let n = data[..length_size]
            .iter()
            .fold(0usize, |v, b| (v << 8) | *b as usize);
        data = &data[length_size..];
        anyhow::ensure!(
            n > 0 && n <= data.len(),
            "invalid/truncated AVCC NAL payload"
        );
        anyhow::ensure!(
            data[0] & 0x80 == 0 && data[0] & 0x1f != 0,
            "invalid AVC NAL header"
        );
        out.extend_from_slice(&[0, 0, 0, 1]);
        out.extend_from_slice(&data[..n]);
        data = &data[n..];
    }
    Ok(out)
}

#[derive(Clone, Copy, PartialEq)]
pub struct Color {
    pub bt709: bool,
    pub full: bool,
}
impl Color {
    pub fn description(self) -> String {
        format!(
            "{} {}",
            if self.bt709 { "BT.709" } else { "BT.601" },
            if self.full { "full" } else { "limited" }
        )
    }
}
pub fn video_color(mp4: &Mp4<'_>) -> Result<Color> {
    let track = mp4
        .tracks()
        .iter()
        .find(|t| t.kind() == TrackKind::Video && t.codec() == Some(Codec::H264))
        .context("no supported H.264 video track found")?;
    let Some(SampleEntry::Video(v)) = track.sample_entry() else {
        bail!("missing video entry")
    };
    let CodecConfig::Avc(c) = &v.config else {
        bail!("missing AVC configuration")
    };
    let config = parse_avcc_config(&c.raw).map_err(anyhow::Error::msg)?;
    anyhow::ensure!(!config.sps_nals.is_empty(), "missing SPS");
    let mut result = None;
    for nal in &config.sps_nals {
        let s = rust_h264::sps::parse_sps(&nal.rbsp).map_err(anyhow::Error::msg)?;
        anyhow::ensure!(
            s.bit_depth_luma_minus8 == 0
                && s.bit_depth_chroma_minus8 == 0
                && s.chroma_format_idc == 1
                && s.frame_mbs_only_flag,
            "only progressive 8-bit YUV420 H.264 is supported"
        );
        anyhow::ensure!(
            s.width() > 0 && s.height() > 0 && s.width() <= 4096 && s.height() <= 2160,
            "H.264 dimensions exceed supported 4096x2160 limit"
        );
        anyhow::ensure!(
            s.width() == v.width as u32 && s.height() == v.height as u32,
            "SPS/sample-entry dimensions disagree"
        );
        let mut matrix = s.matrix_coefficients.unwrap_or(2) as u16;
        let mut full = s.video_full_range.unwrap_or(false);
        let mut transfer = s.transfer_characteristics.unwrap_or(2) as u16;
        if let Some(info) = &v.colour_info {
            match info {
                mp4io::ColourInfo::Nclx {
                    matrix_coefficients,
                    full_range,
                    transfer_characteristics,
                    ..
                } => {
                    anyhow::ensure!(
                        matrix == 2 || *matrix_coefficients == 2 || matrix == *matrix_coefficients,
                        "conflicting MP4/SPS color matrix"
                    );
                    if *matrix_coefficients != 2 {
                        matrix = *matrix_coefficients;
                    }
                    full = *full_range;
                    transfer = *transfer_characteristics;
                }
                _ => bail!("ICC/unknown color profiles unsupported"),
            }
        }
        anyhow::ensure!(
            matches!(matrix, 1 | 2 | 5 | 6),
            "unsupported color matrix {matrix}"
        );
        anyhow::ensure!(
            matches!(transfer, 1 | 2 | 6 | 13 | 14 | 15),
            "HDR/unsupported transfer function {transfer}"
        );
        let color = Color {
            bt709: matrix == 1,
            full,
        };
        anyhow::ensure!(
            result.is_none_or(|old| old == color),
            "changing SPS colors unsupported"
        );
        result = Some(color);
    }
    result.context("no video color information")
}

#[cfg(test)]
mod avcc_tests {
    use super::*;
    #[test]
    fn converts_one_two_and_four_byte_lengths() {
        for size in [1usize, 2, 4] {
            let mut bytes = vec![0; size];
            bytes[size - 1] = 2;
            bytes.extend([0x65, 0x88]);
            assert_eq!(
                avcc_to_annexb(&bytes, size).unwrap(),
                vec![0, 0, 0, 1, 0x65, 0x88]
            );
        }
    }
    #[test]
    fn rejects_truncated_zero_and_invalid_nals() {
        for bytes in [
            vec![],
            vec![0, 0],
            vec![0, 0, 0, 0],
            vec![0, 0, 0, 8, 0x65],
            vec![0, 0, 0, 1, 0x80],
        ] {
            assert!(avcc_to_annexb(&bytes, 4).is_err());
        }
    }
}
