use crate::media::Timeline;
use crate::metrics::{Metrics, Stage};
use anyhow::{Context, Result, bail, ensure};
use hound::SampleFormat;
use mp4io::{Codec, CodecConfig, SampleEntry, Track};
#[cfg(test)]
use rayon::prelude::*;
use rustfft::{Fft, FftPlanner, num_complex::Complex};
use rusty_aac::AacDecoder;
use std::{collections::VecDeque, fs::File, io::BufReader, path::Path, sync::Arc};

pub const TARGET_SAMPLE_RATE: u32 = 16_000;
pub const N_FFT: usize = 400;
pub const HOP_LENGTH: usize = 160;
pub const N_MELS: usize = 64;
/// Consumed source samples are released in blocks of this size (bounded buffer).
const INPUT_DRAIN_SAMPLES: usize = 1 << 15;

pub trait MonoSource: Send {
    fn next_chunk(&mut self) -> Result<Option<Vec<f32>>>;
}
struct WavSource {
    reader: hound::WavReader<BufReader<File>>,
}
impl MonoSource for WavSource {
    fn next_chunk(&mut self) -> Result<Option<Vec<f32>>> {
        let s = self.reader.spec();
        let n = 4096 * s.channels as usize;
        let samples: Vec<f32> = match s.sample_format {
            SampleFormat::Float => self
                .reader
                .samples::<f32>()
                .take(n)
                .collect::<std::result::Result<Vec<_>, _>>()?,
            SampleFormat::Int => {
                let scale = 2f32.powi(s.bits_per_sample as i32 - 1);
                self.reader
                    .samples::<i32>()
                    .take(n)
                    .map(|x| x.map(|v| v as f32 / scale))
                    .collect::<std::result::Result<Vec<_>, _>>()?
            }
        };
        if samples.is_empty() {
            return Ok(None);
        }
        ensure!(
            samples.iter().all(|x| x.is_finite()),
            "WAV contains non-finite samples"
        );
        ensure!(
            samples.len().is_multiple_of(s.channels as usize),
            "incomplete WAV channel frame"
        );
        Ok(Some(downmix(&samples, s.channels as usize)))
    }
}
struct AacSource<'a, 'm> {
    track: &'a Track<'m>,
    decoder: AacDecoder,
    index: usize,
    rate: u32,
    channels: usize,
    skip: usize,
}
impl MonoSource for AacSource<'_, '_> {
    fn next_chunk(&mut self) -> Result<Option<Vec<f32>>> {
        loop {
            let Some(sample) = self.track.sample(self.index)? else {
                return Ok(None);
            };
            self.index += 1;
            ensure!(!sample.data.is_empty(), "empty AAC access unit");
            let decoded = self
                .decoder
                .decode(sample.data, Some(sample.cts))
                .with_context(|| format!("decode AAC sample {}", self.index))?;
            ensure!(
                decoded.sample_rate == self.rate && decoded.channels as usize == self.channels,
                "AAC format changed midstream"
            );
            ensure!(
                decoded.samples.iter().all(|x| x.is_finite()),
                "AAC produced non-finite PCM"
            );
            let mono = downmix(&decoded.samples, self.channels);
            let skip = self.skip.min(mono.len());
            self.skip -= skip;
            if skip < mono.len() {
                return Ok(Some(mono[skip..].to_vec()));
            }
        }
    }
}
fn downmix(samples: &[f32], channels: usize) -> Vec<f32> {
    samples
        .chunks_exact(channels)
        .map(|c| c.iter().sum::<f32>() / channels as f32)
        .collect()
}

/// Chunks of decoded mono PCM that may wait between the decode thread and the
/// resampler. Each AAC access unit is at most 1024 samples per channel.
const SOURCE_QUEUE_CHUNKS: usize = 32;

struct EmptySource;
impl MonoSource for EmptySource {
    fn next_chunk(&mut self) -> Result<Option<Vec<f32>>> {
        bail!("audio source already moved to a decode thread")
    }
}

struct ChannelSource {
    rx: crossbeam_channel::Receiver<Result<Option<Vec<f32>>>>,
    finished: bool,
}
impl MonoSource for ChannelSource {
    fn next_chunk(&mut self) -> Result<Option<Vec<f32>>> {
        if self.finished {
            return Ok(None);
        }
        match self.rx.recv() {
            Ok(Ok(None)) => {
                self.finished = true;
                Ok(None)
            }
            Ok(item) => item,
            Err(_) => bail!("audio decode thread stopped without a result"),
        }
    }
}

/// Sets a flag on drop (including unwinding) so a blocked producer can exit.
struct ConsumerGone<'f>(&'f std::sync::atomic::AtomicBool);
impl Drop for ConsumerGone<'_> {
    fn drop(&mut self) {
        self.0.store(true, std::sync::atomic::Ordering::Release);
    }
}

/// Run `body` with this stream's source decoded on a separate thread.
///
/// The source is pulled in exactly the same order and its chunks are delivered
/// unchanged through a bounded queue, so every sample, and therefore every
/// resampled and Mel value, is identical to inline decoding. Only wall-clock
/// timing changes: AAC decode and resample/Mel can run on different cores.
pub fn with_decode_thread<'a, R>(
    stream: &mut AudioStream<'a>,
    cancel: &crate::concurrency::Cancellation,
    body: impl FnOnce(&mut AudioStream<'a>) -> Result<R>,
) -> Result<R> {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::Duration;
    let mut source: Box<dyn MonoSource + 'a> =
        std::mem::replace(&mut stream.source, Box::new(EmptySource));
    let (tx, rx) = crossbeam_channel::bounded(SOURCE_QUEUE_CHUNKS);
    stream.source = Box::new(ChannelSource {
        rx,
        finished: false,
    });
    let previous_stage = stream.source_stage;
    stream.source_stage = Stage::AudioSourceWait;
    let metrics = stream.metrics.clone();
    let gone = AtomicBool::new(false);
    let result = std::thread::scope(|scope| -> Result<R> {
        std::thread::Builder::new()
            .name("tenzor-audio-decode".into())
            .spawn_scoped(scope, || {
                loop {
                    let item = if cancel.check().is_err() {
                        Err(crate::concurrency::Cancelled.into())
                    } else {
                        let started = metrics.start();
                        let item = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            source.next_chunk()
                        }))
                        .unwrap_or_else(|_| Err(anyhow::anyhow!("audio decode thread panicked")));
                        metrics.end(Stage::AudioSource, started);
                        item
                    };
                    let last = !matches!(item, Ok(Some(_)));
                    let mut pending = item;
                    loop {
                        match tx.send_timeout(pending, Duration::from_millis(20)) {
                            Ok(()) => break,
                            Err(crossbeam_channel::SendTimeoutError::Timeout(back)) => {
                                if gone.load(Ordering::Acquire) {
                                    return;
                                }
                                pending = back;
                            }
                            Err(crossbeam_channel::SendTimeoutError::Disconnected(_)) => return,
                        }
                    }
                    if last {
                        return;
                    }
                }
            })
            .context("spawn audio decode thread")?;
        let _gone = ConsumerGone(&gone);
        let out = body(stream);
        // Drop the receiver so a producer blocked on a full queue exits promptly.
        stream.source = Box::new(EmptySource);
        out
    });
    stream.source_stage = previous_stage;
    result
}

/// Pull-based PCM -> sinc -> overlapping STFT -> log-Mel; all queues are bounded.
pub struct AudioStream<'a> {
    source: Box<dyn MonoSource + 'a>,
    sinc: SincResampler,
    /// Contiguous decoded mono PCM; `input[0]` is source sample `base`.
    input: Vec<f32>,
    base: usize,
    read: usize,
    ended: bool,
    source_len: usize,
    output_pos: usize,
    delay_samples: usize,
    pub total_samples: usize,
    resampled: VecDeque<f32>,
    fft: Arc<dyn Fft<f32>>,
    filters: Vec<Vec<f32>>,
    hann: Vec<f32>,
    pub next_frame: usize,
    pub metrics: Metrics,
    /// Timer charged while waiting for source PCM: `AudioSource` when decoding
    /// inline, `AudioSourceWait` when a decode thread feeds this stream.
    source_stage: Stage,
}
impl<'a> AudioStream<'a> {
    pub fn wav(path: &Path) -> Result<Self> {
        let reader = hound::WavReader::open(path).context("open WAV")?;
        let s = reader.spec();
        ensure!((1..=32).contains(&s.channels), "WAV channels must be 1..32");
        ensure!(
            reader.len().is_multiple_of(s.channels as u32),
            "incomplete WAV channel frame"
        );
        ensure!(
            (1..=32).contains(&s.bits_per_sample),
            "unsupported WAV sample depth"
        );
        let len = reader.duration() as usize;
        ensure!(len > 0, "WAV contains no samples");
        Self::new(Box::new(WavSource { reader }), s.sample_rate, len, 0)
    }
    pub fn aac(track: &'a Track<'a>, movie_scale: u32) -> Result<Self> {
        ensure!(
            track.codec() == Some(Codec::Aac),
            "MP4 audio codec unsupported: only AAC-LC is supported"
        );
        let raw = match track.sample_entry() {
            Some(SampleEntry::Audio(a)) => match &a.config {
                CodecConfig::Aac(c) => &c.decoder_specific,
                _ => bail!("missing AAC config"),
            },
            _ => bail!("missing AAC sample entry"),
        };
        let cfg = rusty_aac::parse_audio_specific_config(raw)?;
        let decoder = AacDecoder::with_config_bytes(raw)?;
        ensure!(
            cfg.object_type == 2
                && !decoder
                    .sbr_config()
                    .is_some_and(|s| s.sbr_present || s.ps_present),
            "only AAC-LC supported; HE-AAC/SBR is unsupported"
        );
        ensure!(
            (1..=6).contains(&cfg.channels),
            "AAC channel configuration unsupported"
        );
        // Access units must form a continuous timeline. Do not silently erase gaps.
        let mut expected = 0;
        for s in track.sample_table() {
            ensure!(
                s.dts == expected && s.cts_offset == 0,
                "discontinuous AAC timeline unsupported"
            );
            expected += s.duration as u64;
        }
        let t = Timeline::for_track(track, movie_scale)?;
        let skip = (t.skip_sec * cfg.sample_rate as f64).round() as usize;
        let len = ((t.duration_sec - t.delay_sec) * cfg.sample_rate as f64).round() as usize;
        let delay = (t.delay_sec * 16000.).round() as usize;
        Self::new(
            Box::new(AacSource {
                track,
                decoder,
                index: 0,
                rate: cfg.sample_rate,
                channels: cfg.channels as usize,
                skip,
            }),
            cfg.sample_rate,
            len,
            delay,
        )
    }
    fn new(source: Box<dyn MonoSource + 'a>, rate: u32, len: usize, delay: usize) -> Result<Self> {
        ensure!(
            (8000..=192000).contains(&rate),
            "source audio sample rate must be 8000..192000 Hz"
        );
        let total_samples = (len as u64 * 16000 / rate as u64) as usize + delay;
        ensure!(total_samples > 0, "empty audio timeline");
        Ok(Self {
            source,
            sinc: SincResampler::new(rate, 16000, 64, 1024),
            input: Vec::new(),
            base: 0,
            read: 0,
            ended: false,
            source_len: len,
            output_pos: 0,
            delay_samples: delay,
            total_samples,
            resampled: VecDeque::new(),
            fft: FftPlanner::new().plan_fft_forward(N_FFT),
            filters: build_mel_filterbank(16000, N_FFT, N_MELS),
            hann: (0..N_FFT)
                .map(|i| 0.5 * (1. - (std::f32::consts::TAU * i as f32 / (N_FFT - 1) as f32).cos()))
                .collect(),
            next_frame: 0,
            metrics: Metrics::new(false),
            source_stage: Stage::AudioSource,
        })
    }
    fn next_sample(&mut self) -> Result<f32> {
        let pos = self.output_pos;
        self.output_pos += 1;
        if pos < self.delay_samples || pos >= self.total_samples {
            return Ok(0.);
        }
        let src = (pos - self.delay_samples) as f64 * self.sinc.from_rate as f64
            / self.sinc.to_rate as f64;
        let center = src.floor() as isize;
        let end = (center + self.sinc.radius as isize + 1).max(0) as usize;
        while self.read < end.min(self.source_len) && !self.ended {
            let started = self.metrics.start();
            let chunk = self.source.next_chunk();
            self.metrics.end(self.source_stage, started);
            match chunk? {
                Some(chunk) => {
                    self.read += chunk.len();
                    self.input.extend(chunk);
                }
                None => {
                    self.ended = true;
                    ensure!(
                        self.read >= self.source_len,
                        "audio ended before declared duration ({} < {} samples)",
                        self.read,
                        self.source_len
                    );
                }
            }
        }
        let phase =
            ((src.fract() * self.sinc.phases as f64).round() as usize).min(self.sinc.phases - 1);
        let taps = 2 * self.sinc.radius + 1;
        let first = center - self.sinc.radius as isize;
        let value = if self.sinc.from_rate == TARGET_SAMPLE_RATE {
            self.input
                .get(center as usize - self.base)
                .copied()
                .unwrap_or(0.)
        } else if first >= 0
            && (first as usize + taps) <= self.source_len
            && (first as usize) >= self.base
            && (first as usize - self.base + taps) <= self.input.len()
        {
            // Interior fast path: every tap is present in the contiguous buffer, so
            // the generic loop below would read exactly these samples. Same
            // products, same left-to-right accumulation, identical result.
            let start = first as usize - self.base;
            self.input[start..start + taps]
                .iter()
                .zip(&self.sinc.table[phase * taps..(phase + 1) * taps])
                .map(|(x, w)| x * w)
                .sum::<f32>()
        } else {
            self.sinc.table[phase * taps..(phase + 1) * taps]
                .iter()
                .enumerate()
                .map(|(i, w)| {
                    let idx = center + i as isize - self.sinc.radius as isize;
                    if idx < 0 || idx as usize >= self.source_len {
                        0.
                    } else {
                        self.input
                            .get(idx as usize - self.base)
                            .copied()
                            .unwrap_or(0.)
                            * w
                    }
                })
                .sum::<f32>()
        };
        // Discard consumed samples in blocks rather than one pop per output sample.
        let keep = first.max(0) as usize;
        if keep > self.base && keep - self.base >= INPUT_DRAIN_SAMPLES {
            let drop = (keep - self.base).min(self.input.len());
            self.input.drain(..drop);
            self.base += drop;
        }
        Ok(value)
    }
    pub fn epoch(&mut self, frames: usize) -> Result<(Vec<f32>, usize)> {
        self.epoch_cancel(frames, None)
    }
    pub fn epoch_cancel(
        &mut self,
        frames: usize,
        cancel: Option<&crate::concurrency::Cancellation>,
    ) -> Result<(Vec<f32>, usize)> {
        let mut out = Vec::with_capacity(frames * N_MELS);
        let mut valid = 0;
        for _ in 0..frames {
            if let Some(cancel) = cancel {
                cancel.check()?;
            }
            if self.next_frame * HOP_LENGTH < self.total_samples {
                valid += 1;
            }
            let source_before = self.metrics.nanos(self.source_stage);
            let resample_started = self.metrics.start();
            while self.resampled.len() < N_FFT {
                let x = self.next_sample()?;
                self.resampled.push_back(x);
            }
            self.metrics.end_excluding(
                Stage::AudioResample,
                resample_started,
                self.metrics
                    .nanos(self.source_stage)
                    .saturating_sub(source_before),
            );
            let mel_started = self.metrics.start();
            let mut buf: Vec<Complex<f32>> = self
                .resampled
                .iter()
                .take(N_FFT)
                .zip(&self.hann)
                .map(|(x, w)| Complex::new(x * w, 0.))
                .collect();
            self.fft.process(&mut buf);
            let power: Vec<f32> = buf[..N_FFT / 2 + 1].iter().map(|x| x.norm_sqr()).collect();
            for filter in &self.filters {
                let energy: f32 = filter.iter().zip(&power).map(|(w, p)| w * p).sum();
                out.push((energy + 1e-10).log10());
            }
            self.resampled.drain(..HOP_LENGTH);
            self.next_frame += 1;
            self.metrics.end(Stage::AudioMel, mel_started);
        }
        Ok((out, valid))
    }
}
/// Windowed-sinc resampler with a precomputed phase table. This avoids evaluating sin/cos
/// for every tap of every output sample (the v0.1.2 specification's main hot-path problem).
struct SincResampler {
    from_rate: u32,
    to_rate: u32,
    radius: usize,
    phases: usize,
    table: Vec<f32>, // [phase, 2*radius+1]
}

impl SincResampler {
    fn new(from_rate: u32, to_rate: u32, radius: usize, phases: usize) -> Self {
        let ratio = to_rate as f64 / from_rate as f64;
        let cutoff = if ratio < 1.0 { ratio * 0.95 } else { 0.95 };
        let taps = 2 * radius + 1;
        let mut table = vec![0.0f32; phases * taps];

        for p in 0..phases {
            let frac = p as f64 / phases as f64;
            let mut sum = 0.0f64;
            for t in 0..taps {
                let offset = t as isize - radius as isize;
                let x = offset as f64 - frac;
                let sx = x * cutoff;
                let sinc = if sx.abs() < 1e-12 {
                    1.0
                } else {
                    let px = std::f64::consts::PI * sx;
                    px.sin() / px
                };
                let normalized = (x + radius as f64) / (2.0 * radius as f64);
                let window = if (0.0..=1.0).contains(&normalized) {
                    0.42 - 0.50 * (2.0 * std::f64::consts::PI * normalized).cos()
                        + 0.08 * (4.0 * std::f64::consts::PI * normalized).cos()
                } else {
                    0.0
                };
                let w = sinc * window * cutoff;
                table[p * taps + t] = w as f32;
                sum += w;
            }
            if sum.abs() > 1e-12 {
                for t in 0..taps {
                    table[p * taps + t] /= sum as f32;
                }
            }
        }

        Self {
            from_rate,
            to_rate,
            radius,
            phases,
            table,
        }
    }

    #[cfg(test)]
    fn process(&self, input: &[f32]) -> Vec<f32> {
        if input.is_empty() {
            return Vec::new();
        }
        let ratio = self.to_rate as f64 / self.from_rate as f64;
        let out_len = (input.len() as f64 * ratio).floor() as usize;
        let taps = 2 * self.radius + 1;

        (0..out_len)
            .into_par_iter()
            .map(|i| {
                let src = i as f64 / ratio;
                let base = src.floor() as isize;
                let frac = src - src.floor();
                let phase = ((frac * self.phases as f64).round() as usize).min(self.phases - 1);
                let weights = &self.table[phase * taps..(phase + 1) * taps];
                let mut acc = 0.0f32;
                for (t, &w) in weights.iter().enumerate() {
                    let idx = base + t as isize - self.radius as isize;
                    if idx >= 0 && (idx as usize) < input.len() {
                        acc += input[idx as usize] * w;
                    }
                }
                acc.clamp(-1.0, 1.0)
            })
            .collect()
    }
}

fn hz_to_mel(hz: f32) -> f32 {
    2595.0 * (1.0 + hz / 700.0).log10()
}
fn mel_to_hz(mel: f32) -> f32 {
    700.0 * (10.0f32.powf(mel / 2595.0) - 1.0)
}

fn build_mel_filterbank(sample_rate: u32, n_fft: usize, n_mels: usize) -> Vec<Vec<f32>> {
    let half = n_fft / 2 + 1;
    let mel_min = hz_to_mel(0.0);
    let mel_max = hz_to_mel(sample_rate as f32 / 2.0);
    let points: Vec<f32> = (0..n_mels + 2)
        .map(|i| {
            let mel = mel_min + (mel_max - mel_min) * i as f32 / (n_mels + 1) as f32;
            (n_fft as f32) * mel_to_hz(mel) / sample_rate as f32
        })
        .collect();

    let mut filters = vec![vec![0.0f32; half]; n_mels];
    for m in 0..n_mels {
        let (l, c, r) = (points[m], points[m + 1], points[m + 2]);
        for (k, weight) in filters[m].iter_mut().enumerate() {
            let x = k as f32;
            *weight = if x > l && x < c {
                (x - l) / (c - l)
            } else if x >= c && x < r {
                (r - x) / (r - c)
            } else {
                0.0
            };
        }
    }
    filters
}

#[cfg(test)]
mod tests {
    use super::*;
    fn tone(rate: u32, hz: f32) -> Vec<f32> {
        (0..rate)
            .map(|i| (std::f32::consts::TAU * hz * i as f32 / rate as f32).sin() * 0.5)
            .collect()
    }
    fn rms(x: &[f32]) -> f32 {
        (x.iter().map(|v| v * v).sum::<f32>() / x.len() as f32).sqrt()
    }
    #[test]
    fn sinc_antialias_and_gain() {
        for rate in [44100, 48000] {
            let r = SincResampler::new(rate, 16000, 64, 1024);
            let low = r.process(&tone(rate, 1000.));
            let high = r.process(&tone(rate, 12000.));
            assert_eq!(low.len(), 16000);
            let gain = rms(&low[100..15900]);
            let rejected = rms(&high[100..15900]);
            assert!((gain - 0.353553).abs() < 0.003, "passband gain {gain}");
            assert!(
                rejected / gain < 0.001,
                "alias rejection {} dB",
                20. * (rejected / gain).log10()
            );
        }
    }
    struct ChunkSource {
        x: Vec<f32>,
        pos: usize,
        chunk: usize,
    }
    impl MonoSource for ChunkSource {
        fn next_chunk(&mut self) -> Result<Option<Vec<f32>>> {
            if self.pos == self.x.len() {
                return Ok(None);
            }
            let end = (self.pos + self.chunk).min(self.x.len());
            let x = self.x[self.pos..end].to_vec();
            self.pos = end;
            Ok(Some(x))
        }
    }
    #[test]
    fn streaming_sinc_matches_whole_signal_across_boundaries() {
        for rate in [44100, 48000] {
            let x = tone(rate, 997.);
            let expected = SincResampler::new(rate, 16000, 64, 1024).process(&x);
            let mut s = AudioStream::new(
                Box::new(ChunkSource {
                    x,
                    pos: 0,
                    chunk: 317,
                }),
                rate,
                rate as usize,
                0,
            )
            .unwrap();
            let actual: Vec<f32> = (0..16000).map(|_| s.next_sample().unwrap()).collect();
            assert!(
                actual
                    .iter()
                    .zip(expected)
                    .all(|(a, b)| (a - b).abs() < 1e-4)
            );
            // Bounded: one drain block plus the filter span and one source chunk.
            assert!(s.input.len() < INPUT_DRAIN_SAMPLES + 2 * 64 + 1 + 317 + 1);
        }
    }
    struct FailingSource {
        inner: ChunkSource,
        fail_at: usize,
        calls: usize,
        panic: bool,
    }
    impl MonoSource for FailingSource {
        fn next_chunk(&mut self) -> Result<Option<Vec<f32>>> {
            self.calls += 1;
            if self.calls == self.fail_at {
                if self.panic {
                    panic!("injected source panic");
                }
                bail!("injected source failure at call {}", self.calls);
            }
            self.inner.next_chunk()
        }
    }
    fn stream_48k(x: Vec<f32>, chunk: usize) -> AudioStream<'static> {
        let len = x.len();
        AudioStream::new(Box::new(ChunkSource { x, pos: 0, chunk }), 48000, len, 0).unwrap()
    }
    fn run_with_timeout<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> T {
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let _ = tx.send(f());
        });
        rx.recv_timeout(std::time::Duration::from_secs(10))
            .expect("audio decode thread deadlocked")
    }
    #[test]
    fn decode_thread_output_is_bit_identical_to_inline() {
        let x: Vec<f32> = (0..3 * 48000)
            .map(|i| {
                (i as f32 * 0.0137).sin() * 0.4 + ((i * 7919 % 1000) as f32 / 1000. - 0.5) * 0.2
            })
            .collect();
        let mut inline = stream_48k(x.clone(), 1024);
        let mut threaded = stream_48k(x, 1024);
        let cancel = crate::concurrency::Cancellation::new();
        let a: Vec<(Vec<f32>, usize)> = (0..8).map(|_| inline.epoch(50).unwrap()).collect();
        let b = with_decode_thread(&mut threaded, &cancel, |s| {
            (0..8).map(|_| s.epoch(50)).collect::<Result<Vec<_>>>()
        })
        .unwrap();
        assert_eq!(a.len(), b.len());
        for ((va, na), (vb, nb)) in a.iter().zip(&b) {
            assert_eq!(na, nb);
            assert!(va.iter().zip(vb).all(|(p, q)| p.to_bits() == q.to_bits()));
        }
    }
    #[test]
    fn decode_thread_reports_source_error_and_panic_without_hanging() {
        for panic in [false, true] {
            let msg = run_with_timeout(move || {
                let x = vec![0.25f32; 4 * 48000];
                let len = x.len();
                let mut s = AudioStream::new(
                    Box::new(FailingSource {
                        inner: ChunkSource {
                            x,
                            pos: 0,
                            chunk: 1024,
                        },
                        fail_at: 60,
                        calls: 0,
                        panic,
                    }),
                    48000,
                    len,
                    0,
                )
                .unwrap();
                let cancel = crate::concurrency::Cancellation::new();
                with_decode_thread(&mut s, &cancel, |s| {
                    for _ in 0..8 {
                        s.epoch(50)?;
                    }
                    Ok(())
                })
                .unwrap_err()
                .to_string()
            });
            assert!(
                msg.contains(if panic {
                    "panicked"
                } else {
                    "injected source failure at call 60"
                }),
                "{msg}"
            );
        }
    }
    #[test]
    fn decode_thread_exits_when_consumer_stops_early_errors_or_panics() {
        for mode in 0..3 {
            let ok = run_with_timeout(move || {
                // Large source, tiny chunks: the producer fills its queue and blocks.
                let mut s = stream_48k(vec![0.1f32; 30 * 48000], 64);
                let cancel = crate::concurrency::Cancellation::new();
                let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    with_decode_thread(&mut s, &cancel, |s| {
                        s.epoch(10)?;
                        match mode {
                            0 => Ok(()),
                            1 => bail!("writer failed"),
                            _ => panic!("consumer panic"),
                        }
                    })
                }));
                match (mode, r) {
                    (0, Ok(Ok(()))) => true,
                    (1, Ok(Err(e))) => e.to_string().contains("writer failed"),
                    (2, Err(_)) => true,
                    _ => false,
                }
            });
            assert!(ok, "mode {mode}");
        }
    }
    #[test]
    fn decode_thread_observes_pipeline_cancellation() {
        let ok = run_with_timeout(|| {
            let mut s = stream_48k(vec![0.1f32; 30 * 48000], 64);
            let cancel = crate::concurrency::Cancellation::new();
            let r: Result<()> = with_decode_thread(&mut s, &cancel, |s| {
                cancel.cancel();
                loop {
                    s.epoch_cancel(10, Some(&cancel))?;
                }
            });
            r.unwrap_err().is::<crate::concurrency::Cancelled>()
        });
        assert!(ok);
    }
    #[test]
    fn downmix_channels() {
        assert_eq!(downmix(&[1., -1., 0.5, 0.5], 2), vec![0., 0.5]);
        assert_eq!(downmix(&[1., 0., -1., 1., 0., -1.], 6), vec![0.]);
    }
    #[test]
    fn short_final_window_is_analyzed_and_silence_is_floor() {
        let mut s = AudioStream::new(
            Box::new(ChunkSource {
                x: vec![0.5; 80],
                pos: 0,
                chunk: 80,
            }),
            16000,
            80,
            0,
        )
        .unwrap();
        let (m, valid) = s.epoch(50).unwrap();
        assert_eq!(valid, 1);
        assert_eq!(m.len(), 3200);
        assert!(m[..64].iter().any(|x| *x > -5.));
        assert!(m[64..].iter().all(|x| (*x + 10.).abs() < 1e-5));
    }
    #[test]
    fn mel_bank_covers_frequency_range() {
        let f = build_mel_filterbank(16000, 400, 64);
        assert_eq!(f.len(), 64);
        assert!(
            f.iter()
                .all(|r| r.len() == 201 && r.iter().any(|x| *x > 0.))
        );
    }
}

#[cfg(test)]
mod aac_transform_tests {
    #[test]
    fn fast_imdct_matches_direct_sum_for_long_short_dense_and_sparse_spectra() {
        for m in [128usize, 1024] {
            for sparse in [false, true] {
                let x: Vec<f32> = (0..m)
                    .map(|i| {
                        if sparse && i != 17 {
                            0.
                        } else {
                            ((i * 37 % 97) as f32 - 48.) * 0.25
                        }
                    })
                    .collect();
                let expected = rusty_aac::dsp::imdct_reference(&x);
                let actual = rusty_aac::dsp::imdct(&x);
                assert_eq!(actual.len(), 2 * m);
                let max_error = actual
                    .iter()
                    .zip(expected)
                    .map(|(a, b)| (a - b).abs())
                    .fold(0f32, f32::max);
                assert!(
                    max_error < 2e-6,
                    "M={m}, sparse={sparse}, max_error={max_error}"
                );
            }
        }
    }
}
