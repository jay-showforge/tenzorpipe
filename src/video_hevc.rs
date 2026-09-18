//! H.265 / HEVC decoding, mirroring the H.264 path's selection and validation.
//!
//! The decoder is `rusty_h265` (Apache-2.0, pure Rust, no FFI), fed Annex-B access
//! units converted from the MP4's length-prefixed samples. Epoch selection, the
//! nearest-picture rule, resizing and colour conversion are the H.264 functions,
//! so an H.265 clip and an equivalent H.264 clip follow identical arithmetic once
//! their pictures are decoded.
//!
//! Scope: Main profile, 8-bit 4:2:0, progressive — the same constraints the H.264
//! path enforces. Main 10 and the range extensions are refused by name.
use anyhow::{Context, Result, bail, ensure};
use mp4io::{Codec, CodecConfig, Mp4, SampleEntry, Track, TrackKind};
use rusty_h265::{
    Decoder, Error,
    nal::{NalHeader, NalType, split_annex_b, unescape},
    ps::parse_sps,
};
use std::ops::Not;

use crate::media::Timeline;
use crate::metrics::{Metrics, Stage};
use crate::video::{Color, VideoConfig, VideoInfo, yuv420_to_resized_chw};
use crate::video_parallel::{ParallelConfig, Selection, assign_epochs, run_ordered};

/// The H.265 track of an MP4, if the file has one.
pub fn hevc_track<'a>(mp4: &'a Mp4<'a>) -> Option<&'a Track<'a>> {
    mp4.tracks()
        .iter()
        .find(|t| t.kind() == TrackKind::Video && t.codec() == Some(Codec::H265))
}

/// Parameter sets from an `hvcC` record, as Annex-B, plus its NAL length size.
fn hvcc_parameter_sets(raw: &[u8]) -> Result<(Vec<u8>, usize)> {
    // ISO/IEC 14496-15 §8.3.3.1 HEVCDecoderConfigurationRecord.
    ensure!(raw.len() > 23, "truncated hvcC record");
    ensure!(
        raw[0] == 1,
        "unsupported hvcC configuration version {}",
        raw[0]
    );
    let length_size = (raw[21] & 3) as usize + 1;
    ensure!(
        (1..=4).contains(&length_size) && length_size != 3,
        "unsupported hvcC NAL length size {length_size}"
    );
    let arrays = raw[22] as usize;
    let mut out = Vec::new();
    let mut p = 23;
    for _ in 0..arrays {
        ensure!(p + 3 <= raw.len(), "truncated hvcC array header");
        let count = u16::from_be_bytes([raw[p + 1], raw[p + 2]]) as usize;
        p += 3;
        for _ in 0..count {
            ensure!(p + 2 <= raw.len(), "truncated hvcC NAL length");
            let n = u16::from_be_bytes([raw[p], raw[p + 1]]) as usize;
            p += 2;
            ensure!(n > 0 && p + n <= raw.len(), "truncated hvcC NAL payload");
            out.extend_from_slice(&[0, 0, 0, 1]);
            out.extend_from_slice(&raw[p..p + n]);
            p += n;
        }
    }
    ensure!(!out.is_empty(), "hvcC carries no parameter sets");
    Ok((out, length_size))
}

/// Length-prefixed sample to Annex-B, rejecting truncated or empty NALs like the
/// H.264 converter does.
fn hvcc_to_annexb(mut data: &[u8], length_size: usize) -> Result<Vec<u8>> {
    ensure!(!data.is_empty(), "empty HEVC sample");
    let mut out = Vec::with_capacity(data.len() + 64);
    while !data.is_empty() {
        ensure!(data.len() >= length_size, "truncated HEVC NAL length");
        let n = data[..length_size]
            .iter()
            .fold(0usize, |v, b| (v << 8) | *b as usize);
        data = &data[length_size..];
        ensure!(
            n > 0 && n <= data.len(),
            "invalid/truncated HEVC NAL payload"
        );
        // forbidden_zero_bit clear, nuh_layer_id 0, nuh_temporal_id_plus1 non-zero.
        ensure!(data.len() >= 2, "truncated HEVC NAL header");
        ensure!(
            data[0] & 0x80 == 0 && (data[0] & 1) == 0 && (data[1] >> 3) == 0 && (data[1] & 7) != 0,
            "invalid HEVC NAL header"
        );
        out.extend_from_slice(&[0, 0, 0, 1]);
        out.extend_from_slice(&data[..n]);
        data = &data[n..];
    }
    Ok(out)
}

/// Colour matrix and range for an H.265 track, validated like the H.264 path:
/// the SPS VUI and the container must agree, and unsupported matrices, transfer
/// functions, bit depths and chroma formats are refused by name.
pub fn video_color(mp4: &Mp4<'_>) -> Result<Color> {
    let track = hevc_track(mp4).context("no H.265 video track found")?;
    let Some(SampleEntry::Video(v)) = track.sample_entry() else {
        bail!("H.265 track missing a video sample entry")
    };
    let CodecConfig::Hevc(c) = &v.config else {
        bail!("H.265 sample entry has no hvcC configuration")
    };
    let (parameter_sets, _) = hvcc_parameter_sets(&c.raw)?;
    let mut result = None;
    for nal in split_annex_b(&parameter_sets) {
        let header = NalHeader::parse(nal).context("unreadable HEVC NAL header")?;
        if header.nal_type != NalType::Sps {
            continue;
        }
        let rbsp = unescape(&nal[2..]);
        let sps = parse_sps(&rbsp.data).map_err(|e| anyhow::anyhow!("parse HEVC SPS: {e:?}"))?;
        ensure!(
            !(sps.range_extension || sps.multilayer_extension || sps.ext_3d || sps.scc_extension),
            "H.265 range/screen-content/layered extensions are unsupported"
        );
        ensure!(
            sps.bit_depth_luma == 8 && sps.bit_depth_chroma == 8,
            "H.265 bit depth {}/{} is unsupported; TenzorPipe decodes 8-bit Main profile. \
             Convert with `ffmpeg -i INPUT -c:v libx265 -pix_fmt yuv420p -c:a copy OUTPUT.mp4`.",
            sps.bit_depth_luma,
            sps.bit_depth_chroma
        );
        // Refuse unsupported profiles here rather than mid-decode. Profile 1 is Main;
        // some encoders signal the profile only through the compatibility flags.
        let main_profile = sps.ptl.profile_idc == 1 || sps.ptl.profile_compatibility_flags & 2 != 0;
        ensure!(
            main_profile,
            "H.265 profile {} is unsupported; TenzorPipe decodes Main profile 8-bit 4:2:0 \
             (Main Still Picture, Main Intra, Main 10 and the range extensions are not \
             supported). Re-encode with `ffmpeg -i INPUT -c:v libx265 -profile:v main \
             -pix_fmt yuv420p -c:a copy OUTPUT.mp4`.",
            sps.ptl.profile_idc
        );
        ensure!(
            sps.ptl.interlaced_source_flag.not(),
            "interlaced H.265 is unsupported"
        );
        ensure!(
            sps.chroma_format_idc == 1,
            "only 4:2:0 H.265 is supported (chroma_format_idc {})",
            sps.chroma_format_idc
        );
        let (width, height) = sps.output_size();
        ensure!(
            width > 0 && height > 0 && width <= 4096 && height <= 2160,
            "H.265 dimensions exceed the supported 4096x2160 limit"
        );
        ensure!(
            width as u64 == v.width as u64 && height as u64 == v.height as u64,
            "SPS/sample-entry dimensions disagree"
        );
        // A VUI without a colour description leaves these zero here, while the spec's
        // absent value is 2 ("unspecified"). Identity (0) and reserved transfer (0)
        // cannot occur in the 4:2:0 content this path accepts, so 0 means absent.
        let unspecified = |v: u8| if v == 0 { 2 } else { v as u16 };
        let mut matrix = sps.vui.as_ref().map_or(2, |v| unspecified(v.matrix_coeffs));
        let mut full = sps.vui.as_ref().is_some_and(|v| v.video_full_range_flag);
        let mut transfer = sps
            .vui
            .as_ref()
            .map_or(2, |v| unspecified(v.transfer_characteristics));
        if let Some(info) = &v.colour_info {
            match info {
                mp4io::ColourInfo::Nclx {
                    matrix_coefficients,
                    full_range,
                    transfer_characteristics,
                    ..
                } => {
                    ensure!(
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
        ensure!(
            matches!(matrix, 1 | 2 | 5 | 6),
            "unsupported color matrix {matrix}"
        );
        ensure!(
            matches!(transfer, 1 | 2 | 6 | 13 | 14 | 15),
            "HDR/unsupported transfer function {transfer}"
        );
        let color = Color {
            bt709: matrix == 1,
            full,
        };
        ensure!(
            result.is_none_or(|old| old == color),
            "changing SPS colors unsupported"
        );
        result = Some(color);
    }
    result.context("hvcC carries no SPS")
}

/// Whether an access unit can be dropped without changing any other picture.
///
/// A sub-layer non-reference picture is never referenced by pictures of its own
/// sub-layer, and no picture may reference a higher sub-layer, so a sub-layer
/// non-reference picture at the stream's highest `TemporalId` is referenced by
/// nothing. Only SEI, access-unit delimiters and filler may accompany it.
/// Malformed units answer false, leaving normal decoding to report the error.
fn disposable_at(data: &[u8], length_size: usize, max_temporal_id: u8) -> bool {
    if !(1..=4).contains(&length_size) {
        return false;
    }
    let mut data = data;
    let mut slices = 0;
    while !data.is_empty() {
        if data.len() < length_size {
            return false;
        }
        let n = data[..length_size]
            .iter()
            .fold(0usize, |v, b| (v << 8) | *b as usize);
        data = &data[length_size..];
        if n == 0 || n > data.len() || data.len() < 2 {
            return false;
        }
        let Some(header) = NalHeader::parse(data) else {
            return false;
        };
        if header.layer_id != 0 {
            return false;
        }
        if header.nal_type.is_vcl() {
            if !header.nal_type.is_sub_layer_non_ref() || header.temporal_id != max_temporal_id {
                return false;
            }
            slices += 1;
        } else if !matches!(
            header.nal_type,
            NalType::PrefixSei | NalType::SuffixSei | NalType::Aud | NalType::Fd
        ) {
            return false;
        }
        data = &data[n..];
    }
    slices > 0
}

/// Per-sample facts the decode paths need, gathered from the sample table and NAL
/// headers only: no picture data is decoded here.
struct Plan {
    pts: Vec<i64>,
    /// Access units that can start a chunk: every VCL NAL is an IRAP slice (IDR, CRA
    /// or BLA), so the decoder resets its reference state there.
    irap: Vec<bool>,
    /// Access units whose VCL NALs are all RASL: they follow a CRA in decode order but
    /// present before it, and reference pictures from before that CRA.
    rasl: Vec<bool>,
    /// Access units nothing references and no epoch selects.
    skippable: Vec<bool>,
    selections: Vec<Selection>,
    total_epochs: usize,
    duration_ms: u64,
}

/// Classifies an access unit by its VCL NAL types: `(all IRAP, all RASL)`.
/// A malformed unit is neither, so it never becomes a chunk boundary.
fn classify(data: &[u8], length_size: usize) -> (bool, bool) {
    let mut vcl = 0;
    let mut irap = 0;
    let mut rasl = 0;
    for header in nal_headers(data, length_size) {
        if header.layer_id != 0 {
            return (false, false);
        }
        if header.nal_type.is_vcl() {
            vcl += 1;
            irap += usize::from(header.nal_type.is_irap());
            rasl += usize::from(header.nal_type.is_rasl());
        }
    }
    (vcl > 0 && irap == vcl, vcl > 0 && rasl == vcl)
}

fn build_plan(
    track: &Track<'_>,
    timeline: &Timeline,
    config: &VideoConfig,
    length_size: usize,
    cancel: &crate::concurrency::Cancellation,
    input_mapping: Option<&memmap2::Mmap>,
) -> Result<Plan> {
    let n = track.sample_count();
    ensure!(
        crate::video_parallel::plan_fits_budget(n),
        "H.265 planning metadata exceeds its 32 MiB allowance"
    );
    let duration_ms_f = timeline.duration_sec * 1000.;
    let in_range = |p: i64| p >= 0 && (p as f64) < duration_ms_f;
    let mut pts = Vec::with_capacity(n);
    let mut irap = Vec::with_capacity(n);
    let mut rasl = Vec::with_capacity(n);
    let mut max_temporal_id = 0u8;
    for i in 0..n {
        cancel.check()?;
        if i % 128 == 0 {
            crate::video_parallel::release_index_pages(input_mapping)?;
        }
        let sample = track
            .sample(i)
            .context("read H.265 sample table")?
            .context("read H.265 sample table")?;
        pts.push((timeline.pts_sec(sample.cts, track.timescale()) * 1000.).round() as i64);
        let (is_irap, is_rasl) = classify(sample.data, length_size);
        irap.push(is_irap);
        rasl.push(is_rasl);
        for nal in nal_headers(sample.data, length_size) {
            if nal.nal_type.is_vcl() {
                max_temporal_id = max_temporal_id.max(nal.temporal_id);
            }
        }
    }
    crate::video_parallel::release_index_pages(input_mapping)?;

    let mut presented: Vec<i64> = pts.iter().copied().filter(|p| in_range(*p)).collect();
    ensure!(
        !presented.is_empty(),
        "H.265 track produced no display frames"
    );
    presented.sort_unstable();
    ensure!(
        presented.windows(2).all(|w| w[0] != w[1]),
        "duplicate H.265 presentation timestamps are unsupported"
    );
    let duration_ms = duration_ms_f.round() as u64;
    let total_epochs = duration_ms
        .max(config.audio_duration_ms)
        .div_ceil(config.window_ms) as usize;
    let selections = assign_epochs(&presented, config.window_ms, total_epochs);
    ensure!(
        selections.last().map(|s| s.epochs.1) == Some(total_epochs),
        "H.265 selection does not cover every epoch"
    );

    // A clip that opens on a CRA carries RASL pictures that reference frames before the
    // start of the bitstream: no decoder can produce them (ISO/IEC 23008-2 NoRaslOutputFlag),
    // so they are never decoded or expected here either.
    let mut skippable = vec![false; n];
    if !pts.is_empty() && irap[0] {
        let mut i = 1;
        while i < n && rasl[i] {
            skippable[i] = true;
            i += 1;
        }
    }
    if config.skip_nonref {
        for i in 0..n {
            if skippable[i] {
                continue;
            }
            let sample = track
                .sample(i)
                .context("read H.265 sample table")?
                .context("read H.265 sample table")?;
            skippable[i] = disposable_at(sample.data, length_size, max_temporal_id)
                && selections.binary_search_by_key(&pts[i], |s| s.pts).is_err();
        }
        crate::video_parallel::release_index_pages(input_mapping)?;
    }
    Ok(Plan {
        pts,
        irap,
        rasl,
        skippable,
        selections,
        total_epochs,
        duration_ms,
    })
}

/// NAL headers of a length-prefixed access unit, stopping at the first malformed one.
fn nal_headers(data: &[u8], length_size: usize) -> Vec<NalHeader> {
    let mut out = Vec::new();
    let mut data = data;
    if !(1..=4).contains(&length_size) {
        return out;
    }
    while !data.is_empty() {
        if data.len() < length_size {
            return out;
        }
        let n = data[..length_size]
            .iter()
            .fold(0usize, |v, b| (v << 8) | *b as usize);
        data = &data[length_size..];
        if n == 0 || n > data.len() || data.len() < 2 {
            return out;
        }
        match NalHeader::parse(data) {
            Some(header) => out.push(header),
            None => return out,
        }
        data = &data[n..];
    }
    out
}

/// A chunk of an H.265 clip: the access units one decoder feeds on, and the
/// presentation interval whose pictures that decoder is responsible for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HevcChunk {
    /// The IRAP access unit this chunk starts from.
    pub head: usize,
    /// The rest of the chunk in decode order: everything after this chunk's own RASL
    /// run (those belong to the previous chunk) up to and including the next chunk's
    /// IRAP and the RASL pictures that follow it.
    pub tail: std::ops::Range<usize>,
    /// Presentation interval this chunk owns, in milliseconds: `[start, end)`.
    pub present: (i64, i64),
}

/// Split an H.265 clip at IRAP access units.
///
/// An IDR resets the decoder outright. A CRA does too, but the RASL pictures that
/// follow it in decode order present *before* it and reference pictures from the
/// previous GOP, so they belong to the previous chunk: each chunk decodes through
/// the next chunk's IRAP and its RASL run, and hands out only the pictures inside
/// its own presentation interval. Open-GOP encodes (x265's default) are the reason
/// this is worth doing: they contain no IDR beyond the first frame.
///
/// Returns a single chunk when the clip cannot be split, and `None` when the
/// presentation intervals do not partition the pictures, which sends the caller to
/// the single-decoder path rather than risking a different result.
pub fn plan_hevc_chunks(
    pts: &[i64],
    irap: &[bool],
    rasl: &[bool],
    target_ms: u64,
    in_range: impl Fn(i64) -> bool,
) -> Option<Vec<HevcChunk>> {
    let n = pts.len();
    if n == 0 {
        return None;
    }
    // Candidate starts: IRAP access units far enough apart, never inside a RASL run.
    let mut starts = vec![0usize];
    for k in 1..n {
        if !irap[k] || rasl[k] {
            continue;
        }
        let previous = pts[*starts.last().unwrap()];
        if pts[k].saturating_sub(previous) >= target_ms as i64 {
            starts.push(k);
        }
    }
    let mut chunks = Vec::with_capacity(starts.len());
    for (c, &start) in starts.iter().enumerate() {
        let (decode_end, present_end) = match starts.get(c + 1) {
            Some(&next) => {
                // Include the next IRAP and its RASL run: those RASL pictures present
                // before it and depend on this chunk's references.
                let mut end = next + 1;
                while end < n && rasl[end] {
                    end += 1;
                }
                (end, pts[next])
            }
            None => (n, i64::MAX),
        };
        // This chunk's own RASL pictures present before it and belong to the previous
        // chunk, which decodes them with the references they need.
        let mut tail_start = start + 1;
        while tail_start < n && rasl[tail_start] {
            tail_start += 1;
        }
        chunks.push(HevcChunk {
            head: start,
            tail: tail_start..decode_end.max(tail_start),
            present: (pts[start], present_end),
        });
    }
    // Every in-range picture must be decodable by the chunk that owns its
    // presentation time, and intervals must ascend.
    if chunks.windows(2).any(|w| w[0].present.1 <= w[0].present.0) {
        return None;
    }
    for (i, &stamp) in pts.iter().enumerate() {
        if !in_range(stamp) {
            continue;
        }
        let owner = chunks
            .iter()
            .position(|chunk| stamp >= chunk.present.0 && stamp < chunk.present.1)?;
        let chunk = &chunks[owner];
        if chunk.head != i && !chunk.tail.contains(&i) {
            return None;
        }
    }
    Some(chunks)
}

/// What one decoded chunk produced: its converted pictures and the counters the
/// summary reports. The single-decoder path never fills `pictures` — it hands each
/// tensor straight to the collector instead, so a long clip holds one at a time.
#[derive(Default)]
struct ChunkOutput {
    pictures: Vec<(Selection, Vec<f32>)>,
    resized: usize,
    skipped: usize,
}

/// Geometry and colour shared by every decode worker.
#[derive(Clone)]
struct Shared {
    width: usize,
    height: usize,
    resolution: usize,
    color: Color,
    length_size: usize,
    parameter_sets: Vec<u8>,
}

/// Decode `range` on a private decoder and convert the pictures it owes.
#[allow(clippy::too_many_arguments)]
fn decode_range(
    track: &Track<'_>,
    shared: &Shared,
    plan: &Plan,
    indices: impl Iterator<Item = usize>,
    expected: &[i64],
    wanted: &[Selection],
    metrics: &Metrics,
    cancel: &crate::concurrency::Cancellation,
    mut sink: impl FnMut(Selection, Vec<f32>) -> Result<()>,
) -> Result<(usize, usize)> {
    let mut decoder = Decoder::new();
    decoder
        .push_annexb(&shared.parameter_sets, None)
        .map_err(|e| anyhow::anyhow!("initialize HEVC parameter sets: {e:?}"))?;
    let mut resized = 0usize;
    let mut skipped = 0usize;
    let mut scratch: Vec<u8> = Vec::new();
    let mut next_expected = 0usize;
    let mut next_wanted = 0usize;
    let mut produced = 0usize;

    let emit = |frame: rusty_h265::frame::Frame,
                sink: &mut dyn FnMut(Selection, Vec<f32>) -> Result<()>,
                resized: &mut usize,
                scratch: &mut Vec<u8>,
                next_expected: &mut usize,
                next_wanted: &mut usize,
                produced: &mut usize|
     -> Result<()> {
        *produced += 1;
        ensure!(
            frame.width == shared.width && frame.height == shared.height,
            "midstream H.265 resolution changes unsupported"
        );
        ensure!(
            frame.picture.bit_depth_luma == 8 && frame.picture.chroma_format_idc == 1,
            "only progressive 8-bit YUV420 H.265 is supported"
        );
        let pts = *expected
            .get(*next_expected)
            .context("HEVC decoder produced more pictures than the container declares")?;
        *next_expected += 1;
        if wanted.get(*next_wanted).is_some_and(|s| s.pts == pts) {
            let started = metrics.start();
            // Cropped and contiguous: the shared converter expects planes without padding.
            scratch.clear();
            frame.write_yuv(scratch);
            let luma = shared.width * shared.height;
            let chroma = shared.width.div_ceil(2) * shared.height.div_ceil(2);
            ensure!(
                scratch.len() == luma + 2 * chroma,
                "unexpected HEVC picture layout"
            );
            let (y, rest) = scratch.split_at(luma);
            let (u, v) = rest.split_at(chroma);
            let chw = yuv420_to_resized_chw(
                y,
                u,
                v,
                (shared.width, shared.height),
                (shared.resolution, shared.resolution),
                shared.color,
            );
            metrics.end(Stage::VideoResize, started);
            *resized += 1;
            sink(wanted[*next_wanted], chw)?;
            *next_wanted += 1;
        }
        Ok(())
    };

    for index in indices {
        cancel.check()?;
        let parse_started = metrics.start();
        let sample = track
            .sample(index)
            .context("read H.265 access unit")?
            .context("read H.265 access unit")?;
        if plan.skippable[index] {
            skipped += 1;
            metrics.end(Stage::VideoParse, parse_started);
            continue;
        }
        let annexb = hvcc_to_annexb(sample.data, shared.length_size)?;
        metrics.end(Stage::VideoParse, parse_started);
        let decode_started = metrics.start();
        decoder
            .push_annexb(&annexb, None)
            .map_err(|e| anyhow::anyhow!("decode HEVC access unit {index}: {e:?}"))?;
        metrics.end(Stage::VideoDecode, decode_started);
        loop {
            let started = metrics.start();
            let frame = decoder.next_frame();
            metrics.end(Stage::VideoDecode, started);
            match frame {
                Ok(frame) => emit(
                    frame,
                    &mut sink,
                    &mut resized,
                    &mut scratch,
                    &mut next_expected,
                    &mut next_wanted,
                    &mut produced,
                )?,
                Err(Error::Again) | Err(Error::Eof) => break,
                Err(e) => bail!("decode HEVC picture at access unit {index}: {e:?}"),
            }
        }
    }
    let decode_started = metrics.start();
    decoder.flush();
    metrics.end(Stage::VideoDecode, decode_started);
    loop {
        match decoder.next_frame() {
            Ok(frame) => emit(
                frame,
                &mut sink,
                &mut resized,
                &mut scratch,
                &mut next_expected,
                &mut next_wanted,
                &mut produced,
            )?,
            Err(Error::Again) | Err(Error::Eof) => break,
            Err(e) => bail!("flush HEVC decoder: {e:?}"),
        }
    }
    ensure!(
        next_expected == expected.len() && next_wanted == wanted.len(),
        "incomplete H.265 presentation sequence: {next_expected} of {} pictures, \
         {next_wanted} of {} selections",
        expected.len(),
        wanted.len()
    );
    Ok((resized, skipped))
}

/// In-range presentation times a chunk decodes, in output order, excluding the
/// access units the plan skips.
fn chunk_expected(
    plan: &Plan,
    indices: impl Iterator<Item = usize>,
    duration_ms_f: f64,
) -> Vec<i64> {
    let mut out: Vec<i64> = indices
        .filter(|&i| !plan.skippable[i])
        .map(|i| plan.pts[i])
        .filter(|p| *p >= 0 && (*p as f64) < duration_ms_f)
        .collect();
    out.sort_unstable();
    out
}

/// Decode an H.265 track, using `par` workers over IDR-aligned chunks when the
/// clip allows it. The callback receives `(epoch, pts_ms, chw_tensor)` in
/// ascending epoch order, exactly as the H.264 paths deliver it.
pub fn decode_hevc_mp4<F>(
    mp4: &Mp4<'_>,
    config: VideoConfig,
    par: ParallelConfig,
    metrics: &Metrics,
    cancel: &crate::concurrency::Cancellation,
    input_mapping: Option<&memmap2::Mmap>,
    mut on_epoch: F,
) -> Result<VideoInfo>
where
    F: FnMut(usize, i64, &[f32]) -> Result<()>,
{
    ensure!(config.window_ms > 0, "epoch window must be positive");
    ensure!(
        config.decoder_threads == 0,
        "--decoder-threads applies to the H.264 decoder only"
    );
    let track = hevc_track(mp4).context("no H.265 video track found")?;
    let timeline = Timeline::for_track(track, mp4.timescale())?;
    let entry = track
        .sample_entry()
        .context("H.265 track missing sample entry")?;
    let (width, height, raw) = match entry {
        SampleEntry::Video(v) => match &v.config {
            CodecConfig::Hevc(c) => (v.width as usize, v.height as usize, c.raw.clone()),
            _ => bail!("H.265 sample entry has no hvcC configuration"),
        },
        _ => bail!("selected video track did not have a video sample entry"),
    };
    let (parameter_sets, length_size) = hvcc_parameter_sets(&raw)?;
    let color = video_color(mp4)?;

    let plan_started = metrics.start();
    let plan = build_plan(
        track,
        &timeline,
        &config,
        length_size,
        cancel,
        input_mapping,
    )?;
    let duration_ms_f = timeline.duration_sec * 1000.;
    let shared = Shared {
        width,
        height,
        resolution: config.resolution,
        color,
        length_size,
        parameter_sets,
    };

    // Chunk the clip at IRAP access units, giving each chunk the presentation interval
    // it owns and the access units it must decode to produce it.
    let in_range = |p: i64| p >= 0 && (p as f64) < duration_ms_f;
    let chunks = plan_hevc_chunks(
        &plan.pts,
        &plan.irap,
        &plan.rasl,
        par.chunk_target_ms,
        in_range,
    )
    .unwrap_or_default();
    let mut chunk_expected_pts: Vec<Vec<i64>> = Vec::with_capacity(chunks.len());
    let mut chunk_selected: Vec<std::ops::Range<usize>> = Vec::with_capacity(chunks.len());
    let mut cursor = 0usize;
    for chunk in &chunks {
        chunk_expected_pts.push(chunk_expected(
            &plan,
            std::iter::once(chunk.head).chain(chunk.tail.clone()),
            duration_ms_f,
        ));
        let begin = cursor;
        while cursor < plan.selections.len() && plan.selections[cursor].pts < chunk.present.1 {
            cursor += 1;
        }
        chunk_selected.push(begin..cursor);
    }
    // A selected picture the plan skipped, or one outside its chunk, would never reach
    // the collector: either sends the clip to the single-decoder path instead.
    let assignment_ok = !chunks.is_empty()
        && cursor == plan.selections.len()
        && chunk_selected.iter().enumerate().all(|(c, range)| {
            plan.selections[range.clone()]
                .iter()
                .all(|s| chunk_expected_pts[c].binary_search(&s.pts).is_ok())
        });
    let tensor_bytes = 3 * config.resolution * config.resolution * std::mem::size_of::<f32>();
    let max_chunk_bytes = chunk_selected.iter().map(|r| r.len()).max().unwrap_or(0) * tensor_bytes;
    let window = (par.workers + 1)
        .min(
            par.buffer_bytes
                .checked_div(max_chunk_bytes)
                .unwrap_or(par.workers + 1),
        )
        .max(1);
    let workers = par.workers.min(window).min(chunks.len().max(1));
    metrics.end(Stage::VideoParse, plan_started);

    let parallel = par.workers >= 2 && chunks.len() >= 2 && workers >= 2 && assignment_ok;
    if config.diagnostics {
        if parallel {
            eprintln!(
                "video_codec=h265 decoder=rusty_h265 video_mode=chunked workers={workers} \
                 window={window} chunks={} max_chunk_tensor_bytes={max_chunk_bytes} \
                 bound_bytes={}",
                chunks.len(),
                window * max_chunk_bytes
            );
        } else {
            let reason = if par.workers < 2 {
                "fewer than 2 workers requested"
            } else if chunks.len() < 2 {
                "fewer than 2 IDR chunks"
            } else if !assignment_ok {
                "chunk plan does not partition the pictures"
            } else {
                "byte budget allows fewer than 2 in-flight chunks"
            };
            eprintln!("video_codec=h265 decoder=rusty_h265 video_mode=single reason=\"{reason}\"");
        }
    }

    let mut resized_count = 0usize;
    let mut skipped_count = 0usize;
    let mut next_epoch = 0usize;
    // Pictures reach the collector in epoch order, one tensor at a time.
    let hand_over = |selection: Selection,
                     chw: &[f32],
                     next_epoch: &mut usize,
                     on_epoch: &mut F|
     -> Result<()> {
        let (start, end) = selection.epochs;
        ensure!(
            start == *next_epoch,
            "epoch order mismatch: expected {}, got {start}",
            *next_epoch
        );
        for epoch in start..end {
            on_epoch(epoch, selection.pts, chw)?;
        }
        *next_epoch = end;
        Ok(())
    };

    if parallel {
        run_ordered(
            chunks.len(),
            workers,
            window,
            cancel,
            metrics,
            |c| {
                // A worker buffers only its own chunk; the byte budget above bounds
                // how many such chunks may wait for their turn.
                let mut out = ChunkOutput::default();
                let (resized, skipped) = decode_range(
                    track,
                    &shared,
                    &plan,
                    std::iter::once(chunks[c].head).chain(chunks[c].tail.clone()),
                    &chunk_expected_pts[c],
                    &plan.selections[chunk_selected[c].clone()],
                    metrics,
                    cancel,
                    |selection, chw| {
                        out.pictures.push((selection, chw));
                        Ok(())
                    },
                )?;
                out.resized = resized;
                out.skipped = skipped;
                Ok(out)
            },
            |_, chunk: ChunkOutput| {
                resized_count += chunk.resized;
                skipped_count += chunk.skipped;
                for (selection, chw) in chunk.pictures {
                    hand_over(selection, &chw, &mut next_epoch, &mut on_epoch)?;
                }
                Ok(())
            },
        )?;
    } else {
        // One decoder, and each tensor is handed over as soon as it is converted.
        let expected = chunk_expected(&plan, 0..plan.pts.len(), duration_ms_f);
        let mut epoch_cursor = 0usize;
        let (resized, skipped) = decode_range(
            track,
            &shared,
            &plan,
            0..plan.pts.len(),
            &expected,
            &plan.selections,
            metrics,
            cancel,
            |selection, chw| hand_over(selection, &chw, &mut epoch_cursor, &mut on_epoch),
        )?;
        resized_count = resized;
        skipped_count = skipped;
        next_epoch = epoch_cursor;
    }
    ensure!(
        next_epoch == plan.total_epochs,
        "incomplete H.265 presentation sequence"
    );
    Ok(VideoInfo {
        width,
        height,
        duration_ms: plan.duration_ms,
        sample_count: track.sample_count(),
        resized_count,
        skipped_count,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hvcc_parameter_sets_reads_every_array() {
        // version 1, 21 fixed bytes, length size 4, two arrays of one NAL each.
        let mut raw = vec![0u8; 22];
        raw[0] = 1;
        raw[21] = 0xF3; // reserved bits set, lengthSizeMinusOne = 3
        raw.push(2); // numOfArrays
        for payload in [[0x40u8, 0x01], [0x42, 0x01]] {
            raw.push(0x20);
            raw.extend_from_slice(&1u16.to_be_bytes());
            raw.extend_from_slice(&(payload.len() as u16).to_be_bytes());
            raw.extend_from_slice(&payload);
        }
        let (sets, length_size) = hvcc_parameter_sets(&raw).unwrap();
        assert_eq!(length_size, 4);
        assert_eq!(sets, vec![0, 0, 0, 1, 0x40, 0x01, 0, 0, 0, 1, 0x42, 0x01]);
    }

    #[test]
    fn hvcc_parameter_sets_rejects_malformed_records() {
        assert!(hvcc_parameter_sets(&[]).is_err());
        let mut raw = vec![0u8; 24];
        raw[0] = 2; // wrong configuration version
        assert!(hvcc_parameter_sets(&raw).is_err());
        let mut raw = vec![0u8; 24];
        raw[0] = 1;
        raw[21] = 0xF3;
        raw[22] = 1; // one array, but no array data follows
        assert!(hvcc_parameter_sets(&raw).is_err());
    }

    #[test]
    fn annexb_conversion_rejects_invalid_nal_headers() {
        // Valid: type 1 (TRAIL_R), layer 0, temporal id 1.
        let good = [0, 0, 0, 3, 0x02, 0x01, 0xAF];
        assert_eq!(
            hvcc_to_annexb(&good, 4).unwrap(),
            vec![0, 0, 0, 1, 0x02, 0x01, 0xAF]
        );
        for bad in [
            vec![],                             // empty sample
            vec![0, 0, 0, 3, 0x82, 0x01, 0xAF], // forbidden_zero_bit set
            vec![0, 0, 0, 3, 0x03, 0x01, 0xAF], // nuh_layer_id != 0
            vec![0, 0, 0, 3, 0x02, 0x00, 0xAF], // temporal_id_plus1 == 0
            vec![0, 0, 0, 9, 0x02, 0x01],       // length past the end
            vec![0, 0, 0, 0],                   // zero-length NAL
        ] {
            assert!(hvcc_to_annexb(&bad, 4).is_err(), "{bad:?}");
        }
    }
}
