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
use std::{cmp::Reverse, collections::BinaryHeap};

#[derive(Debug, Clone)]
pub struct VideoInfo {
    pub width: usize,
    pub height: usize,
    pub duration_ms: u64,
    pub sample_count: usize,
    pub resized_count: usize,
}

#[derive(Clone, Copy)]
pub struct VideoConfig {
    pub resolution: usize,
    pub window_ms: u64,
    pub audio_duration_ms: u64,
    pub decoder_threads: usize,
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
    // Metadata-only lookahead avoids copying a borrowed decoder picture just to
    // learn its successor's PTS. The heap is capped, independent of clip duration.
    let timestamps = track
        .samples()
        .map(|sample| {
            let sample = sample.context("read H.264 presentation metadata")?;
            Ok((timeline.pts_sec(sample.cts, track.timescale()) * 1000.).round() as i64)
        })
        .filter(|pts: &Result<i64>| match pts {
            Ok(pts) => *pts >= 0 && (*pts as f64) < timeline.duration_sec * 1000.,
            Err(_) => true,
        });
    let mut presentation = PresentationTimes::new(timestamps);
    let mut current_pts = presentation.next_pts()?;
    anyhow::ensure!(
        current_pts.is_some(),
        "H.264 decoder produced no display frames"
    );
    let mut next_pts = presentation.next_pts()?;
    let duration_ms = (timeline.duration_sec * 1000.0).round() as u64;
    let total_epochs = duration_ms.max(audio_duration_ms).div_ceil(window_ms) as usize;
    let mut epoch = 0;
    let mut resized_count = 0;
    let mut out_index = 0usize;
    let mut emit = |frame: openh264::decoder::DecodedYUV<'_>,
                    pts_ms: &mut BinaryHeap<Reverse<i64>>|
     -> Result<()> {
        let Reverse(pts) = pts_ms.pop().context("decoder produced extra picture")?;
        out_index += 1;
        let (w, h) = frame.dimensions();
        anyhow::ensure!(
            w == width && h == height,
            "midstream H.264 resolution changes unsupported"
        );
        if pts >= 0 && (pts as f64) < timeline.duration_sec * 1000. {
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
        pts_ms.push(Reverse(
            (timeline.pts_sec(sample.cts, track.timescale()) * 1000.).round() as i64,
        ));
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
            emit(frame, &mut pts_ms)?;
        }
    }
    let decode_started = metrics.start();
    let remaining = decoder.flush_remaining().context("flush OpenH264")?;
    metrics.end(Stage::VideoDecode, decode_started);
    for frame in remaining {
        emit(frame, &mut pts_ms)?;
    }
    anyhow::ensure!(
        out_index == track.sample_count(),
        "H.264 picture count mismatch: {out_index} vs {} access units",
        track.sample_count()
    );
    anyhow::ensure!(
        current_pts.is_none() && epoch == total_epochs,
        "incomplete H.264 presentation sequence"
    );
    Ok(VideoInfo {
        width,
        height,
        duration_ms: (timeline.duration_sec * 1000.0).round() as u64,
        sample_count: track.sample_count(),
        resized_count,
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
