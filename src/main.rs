mod audio;
mod concurrency;
mod media;
mod metrics;
mod storage;
mod video;
mod video_parallel;
use anyhow::{Context, Result, bail, ensure};
use clap::{Parser, ValueEnum};
use concurrency::{AudioEpoch, Cancellation, Message, VideoEpoch};
use memmap2::Mmap;
use metrics::{Metrics, Stage};
use mp4io::{Mp4, TrackKind};
use std::{fs::File, path::PathBuf, time::Instant};
use storage::{BatchAccumulator, TenzorMetadata, TenzorWriter};
#[derive(Clone, Copy, Debug, ValueEnum)]
enum Execution {
    Sequential,
    Concurrent,
}
#[derive(Parser, Debug)]
#[command(
    name = "tenzor",
    version,
    about = "MP4 H.264/AAC-LC and WAV to synchronized Arrow tensors"
)]
struct Cli {
    #[arg(short, long)]
    input: PathBuf,
    #[arg(short, long)]
    output: PathBuf,
    /// Epoch duration; must be an exact multiple of the 10 ms audio hop.
    #[arg(short = 'w', long, default_value_t = 0.5)]
    window_sec: f64,
    #[arg(long, default_value_t = 224)]
    resolution: usize,
    #[arg(long, default_value_t = 32)]
    batch_epochs: usize,
    #[arg(long, value_enum, default_value_t = Execution::Concurrent)]
    execution: Execution,
    /// Total queued tensor payload budget across both tracks (MiB); 0 uses rendezvous.
    #[arg(long, default_value_t = 4)]
    queue_mib: usize,
    #[arg(long, default_value_t = 2)]
    queue_depth: usize,
    /// Print active-stage and channel-wait wall-clock timers as JSON on stderr.
    #[arg(long)]
    profile: bool,
    /// Nonzero values require the experimental-decoder-threads build feature.
    #[arg(long, default_value_t = 0)]
    decoder_threads: usize,
    /// Independent H.264 decoders over IDR-aligned chunks. 1 = single-decoder path;
    /// 0 = auto (available parallelism, at most 16, never more than the clip's IDR chunks).
    /// Default: auto, or 1 when --decoder-threads is set.
    #[arg(long)]
    video_workers: Option<usize>,
    /// Decode every access unit, including non-reference pictures that no epoch selects.
    /// Output is identical either way; skipping only saves decode time.
    #[arg(long)]
    no_skip_nonref: bool,
    /// In concurrent mode, decode audio inline instead of on its own thread
    /// (0.1.9 behaviour). Output values are identical either way.
    #[arg(long)]
    no_audio_decode_thread: bool,
    /// Minimum media duration per decode chunk (chunks always start at an IDR).
    #[arg(long, default_value_t = 2000)]
    chunk_target_ms: u64,
    /// Upper bound for decoded video tensors waiting in the chunk reorder window (MiB).
    #[arg(long, default_value_t = 64)]
    video_buffer_mib: usize,
}
fn main() -> Result<()> {
    let args = Cli::parse();
    ensure!(
        [0, 1, 2, 4].contains(&args.decoder_threads),
        "--decoder-threads must be 0, 1, 2, or 4"
    );
    ensure!(
        args.decoder_threads == 0 || cfg!(feature = "experimental-decoder-threads"),
        "decoder threading is experimental; rebuild with --features experimental-decoder-threads"
    );
    ensure!(args.queue_mib <= 64, "--queue-mib must be 0..64");
    ensure!(
        args.video_workers.is_none_or(|n| n <= 64),
        "--video-workers must be 0..64"
    );
    ensure!(
        (250..=60_000).contains(&args.chunk_target_ms),
        "--chunk-target-ms must be 250..60000"
    );
    ensure!(
        (1..=1024).contains(&args.video_buffer_mib),
        "--video-buffer-mib must be 1..1024"
    );
    ensure!(
        args.video_workers.is_none_or(|n| n == 1) || args.decoder_threads == 0,
        "--video-workers and --decoder-threads cannot be combined"
    );
    ensure!(
        (1..=4).contains(&args.queue_depth),
        "--queue-depth must be 1..4"
    );
    ensure!(
        (0.05..=10.).contains(&args.window_sec),
        "--window-sec must be between 0.05 and 10 seconds"
    );
    ensure!(
        (args.window_sec * 100. - (args.window_sec * 100.).round()).abs() < 1e-6,
        "--window-sec must be a multiple of 0.01 seconds (10 ms hop)"
    );
    ensure!(
        (32..=1024).contains(&args.resolution),
        "--resolution must be between 32 and 1024"
    );
    ensure!(
        (1..=1024).contains(&args.batch_epochs),
        "--batch-epochs must be between 1 and 1024"
    );
    let row_bytes =
        (3 * args.resolution * args.resolution + (args.window_sec * 100.) as usize * 64) * 4;
    ensure!(
        row_bytes * args.batch_epochs <= 256 * 1024 * 1024,
        "requested batch exceeds 256 MiB; reduce --batch-epochs or --resolution"
    );
    ensure!(
        args.input.is_file(),
        "input does not exist: {}",
        args.input.display()
    );
    ensure!(args.input.metadata()?.len() > 0, "input file is empty");
    // Reserve output atomically, never overwrite input or an existing dataset.
    ensure!(
        !args.output.exists(),
        "output already exists; choose a new path"
    );
    let ext = args
        .input
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    ensure!(
        matches!(ext.as_str(), "mp4" | "wav"),
        "unsupported extension '{ext}'; supports only MP4 H.264/AAC-LC and WAV"
    );
    let start = Instant::now();
    // Reserve the final path exactly once. A failed ordinary run removes it.
    // IPC readers cannot open the incomplete file because its footer is absent.
    let output = File::options()
        .write(true)
        .create_new(true)
        .open(&args.output)
        .context("create output without overwriting existing files")?;
    let mut guard = OutputGuard {
        path: args.output.clone(),
        completed: false,
    };
    let metrics = Metrics::new(args.profile);
    let (epochs, slots, bytes) = run(&args, &ext, output.try_clone()?, &metrics)?;
    let sync_started = metrics.start();
    output.sync_all()?;
    metrics.end(Stage::FinalSync, sync_started);
    drop(output);
    // Validate the actual reopened file, rather than trusting writer success.
    let reader = arrow::ipc::reader::FileReader::try_new(File::open(&args.output)?, None)
        .context("reopen completed Arrow output")?;
    ensure!(
        reader.num_batches() > 0,
        "completed Arrow output contains no batches"
    );
    drop(reader);
    guard.completed = true;
    metrics.report(
        start.elapsed().as_secs_f64(),
        match args.execution {
            Execution::Sequential => "sequential",
            Execution::Concurrent => "concurrent",
        },
        slots,
        bytes,
        args.decoder_threads,
    );
    println!(
        "TenzorPipe {}: {} epochs in {:.3}s",
        env!("CARGO_PKG_VERSION"),
        epochs,
        start.elapsed().as_secs_f64()
    );
    Ok(())
}
fn run(
    args: &Cli,
    ext: &str,
    output_file: File,
    metrics: &Metrics,
) -> Result<(usize, usize, usize)> {
    let setup_started = metrics.start();
    let file = File::open(&args.input)?;
    // Read-only mapping; input must remain immutable throughout the run.
    let mmap = if ext == "mp4" {
        Some(unsafe { Mmap::map(&file) }.context("map MP4")?)
    } else {
        None
    };
    let mp4 = match &mmap {
        Some(m) => Some(
            Mp4::parse_with_limits(
                m,
                mp4io::Strictness::Strict,
                mp4io::Limits::default().with_max_index_bytes(16 << 20),
            )
            .context("parse MP4")?,
        ),
        None => None,
    };
    let mut audio = if let Some(m) = &mp4 {
        let tracks: Vec<_> = m
            .tracks()
            .iter()
            .filter(|t| t.kind() == TrackKind::Audio)
            .collect();
        ensure!(
            tracks.len() <= 1,
            "multiple audio tracks unsupported; select/remux one track first"
        );
        tracks
            .first()
            .map(|t| audio::AudioStream::aac(t, m.timescale()))
            .transpose()?
    } else {
        Some(audio::AudioStream::wav(&args.input)?)
    };
    let has_video = mp4
        .as_ref()
        .is_some_and(|m| m.tracks().iter().any(|t| t.kind() == TrackKind::Video));
    if let Some(m) = &mp4 {
        ensure!(
            m.tracks()
                .iter()
                .filter(|t| t.kind() == TrackKind::Video)
                .count()
                <= 1,
            "multiple video tracks unsupported"
        );
    }
    ensure!(
        has_video || audio.is_some(),
        "no supported audio or video track"
    );
    let fpe = (args.window_sec * 100.).round() as usize;
    let window_ms = (args.window_sec * 1000.).round() as u64;
    let audio_duration_ms = audio
        .as_ref()
        .map_or(0, |a| (a.total_samples as u64 * 1000).div_ceil(16000));
    let video_matrix = if has_video {
        video::video_color(mp4.as_ref().unwrap())?.description()
    } else {
        "none".into()
    };
    let meta = TenzorMetadata {
        video_matrix,
        window_ms,
        video_shape: has_video.then_some((3, args.resolution, args.resolution)),
        audio_shape: (fpe, 64),
        has_audio: audio.is_some(),
    };
    if let Some(a) = audio.as_mut() {
        a.metrics = metrics.clone();
    }
    let video_duration_ms = if has_video {
        let m = mp4.as_ref().unwrap();
        let track = m
            .tracks()
            .iter()
            .find(|t| t.kind() == TrackKind::Video)
            .unwrap();
        (media::Timeline::for_track(track, m.timescale())?.duration_sec * 1000.).round() as u64
    } else {
        0
    };
    let total = video_duration_ms.max(audio_duration_ms).div_ceil(window_ms) as usize;
    let (slots, queued_bytes) = concurrency::queue_capacity(
        args.queue_depth,
        args.queue_mib * 1024 * 1024,
        if audio.is_some() { fpe * 64 * 4 } else { 0 },
        if has_video {
            3 * args.resolution * args.resolution * 4
        } else {
            0
        },
    );
    let mut writer = TenzorWriter::create(output_file, &meta, metrics.clone())?;
    let mut batch = BatchAccumulator::new(
        args.batch_epochs,
        has_video.then_some(3 * args.resolution * args.resolution),
        fpe * 64,
    );
    metrics.end(Stage::Setup, setup_started);
    let mut write_epoch =
        |epoch: usize, video: Option<(i64, &[f32])>, avec: &[f32], valid: usize| -> Result<()> {
            let pack_started = metrics.start();
            batch.push(
                (epoch as u64 * window_ms) as i64,
                video.map(|v| v.1),
                avec,
                valid as i64,
                video.map_or(-1, |v| v.0),
            )?;
            metrics.end(Stage::ArrowPack, pack_started);
            if batch.is_full() {
                let (ts, v, a, valid, pts) = batch.drain();
                writer.write_batch(ts, v, a, valid, pts)?;
                // Release already-touched file pages. Index metadata is separately bounded at 16 MiB.
                #[cfg(unix)]
                if let Some(m) = &mmap {
                    unsafe {
                        m.unchecked_advise(memmap2::UncheckedAdvice::DontNeed)?;
                    }
                }
            }
            Ok(())
        };
    let video_config = video::VideoConfig {
        resolution: args.resolution,
        window_ms,
        audio_duration_ms,
        decoder_threads: args.decoder_threads,
        skip_nonref: !args.no_skip_nonref,
    };
    let epoch = match args.execution {
        Execution::Sequential => {
            let mut epoch = 0;
            let mut emit = |index: usize, video: Option<(i64, &[f32])>| -> Result<()> {
                let (values, valid) = match audio.as_mut() {
                    Some(a) => a.epoch(fpe)?,
                    None => (vec![-10.; fpe * 64], 0),
                };
                write_epoch(index, video, &values, valid)
            };
            if has_video {
                let info = decode_video(
                    args,
                    mp4.as_ref().unwrap(),
                    video_config,
                    metrics,
                    &Cancellation::new(),
                    mmap.as_ref(),
                    |index, pts, tensor| {
                        concurrency::check_index(index, epoch)?;
                        emit(index, Some((pts, tensor)))?;
                        epoch += 1;
                        Ok(())
                    },
                )?;
                log_video(&info);
            } else {
                while epoch < total {
                    emit(epoch, None)?;
                    epoch += 1;
                }
            }
            epoch
        }
        Execution::Concurrent => {
            let audio_metrics = metrics.clone();
            let video_metrics = metrics.clone();
            let audio_decode_thread = !args.no_audio_decode_thread;
            let audio_worker = audio.map(|mut stream| {
                move |tx: crossbeam_channel::Sender<Message<AudioEpoch>>,
                      cancel: &Cancellation|
                      -> Result<()> {
                    let produce = |stream: &mut audio::AudioStream<'_>| -> Result<()> {
                        for index in 0..total {
                            cancel.check()?;
                            let (values, valid) = stream.epoch_cancel(fpe, Some(cancel))?;
                            cancel.send(
                                &tx,
                                Message::Data(AudioEpoch {
                                    index,
                                    values,
                                    valid,
                                }),
                                &audio_metrics,
                            )?;
                        }
                        cancel.send(&tx, Message::End(total), &audio_metrics)
                    };
                    if audio_decode_thread {
                        audio::with_decode_thread(&mut stream, cancel, produce)
                    } else {
                        produce(&mut stream)
                    }
                }
            });
            let video_worker = has_video.then_some({
                |tx: crossbeam_channel::Sender<Message<VideoEpoch>>,
                 cancel: &Cancellation|
                 -> Result<()> {
                    let info = decode_video(
                        args,
                        mp4.as_ref().unwrap(),
                        video_config,
                        &video_metrics,
                        cancel,
                        mmap.as_ref(),
                        |index, pts, tensor| {
                            cancel.send(
                                &tx,
                                Message::Data(VideoEpoch {
                                    index,
                                    pts,
                                    values: tensor.to_vec(),
                                }),
                                &video_metrics,
                            )
                        },
                    )?;
                    log_video(&info);
                    cancel.send(&tx, Message::End(total), &video_metrics)
                }
            });
            concurrency::run(
                slots,
                audio_worker,
                video_worker,
                |audio_rx, video_rx, cancel| {
                    let silence = vec![-10.; fpe * 64];
                    for index in 0..total {
                        let video = video_rx
                            .map(|rx| match cancel.recv(rx, metrics)? {
                                Message::Data(v) => {
                                    concurrency::check_index(v.index, index)?;
                                    Ok(v)
                                }
                                Message::End(_) => {
                                    bail!("video ended before expected epoch {index}")
                                }
                            })
                            .transpose()?;
                        let audio = audio_rx
                            .map(|rx| match cancel.recv(rx, metrics)? {
                                Message::Data(a) => {
                                    concurrency::check_index(a.index, index)?;
                                    Ok(a)
                                }
                                Message::End(_) => {
                                    bail!("audio ended before expected epoch {index}")
                                }
                            })
                            .transpose()?;
                        write_epoch(
                            index,
                            video.as_ref().map(|v| (v.pts, v.values.as_slice())),
                            audio
                                .as_ref()
                                .map_or(silence.as_slice(), |a| a.values.as_slice()),
                            audio.as_ref().map_or(0, |a| a.valid),
                        )?;
                    }
                    if let Some(rx) = audio_rx {
                        ensure!(
                            matches!(cancel.recv(rx,metrics)?,Message::End(n) if n==total),
                            "audio EOF/count mismatch"
                        );
                    }
                    if let Some(rx) = video_rx {
                        ensure!(
                            matches!(cancel.recv(rx,metrics)?,Message::End(n) if n==total),
                            "video EOF/count mismatch"
                        );
                    }
                    Ok(total)
                },
            )?
        }
    };
    ensure!(epoch == total, "incorrect synchronized epoch count");
    if !batch.is_empty() {
        let (ts, v, a, valid, pts) = batch.drain();
        writer.write_batch(ts, v, a, valid, pts)?;
    }
    writer.finish()?;
    if epoch == 0 {
        bail!("no epochs produced");
    }
    Ok((
        epoch,
        if matches!(args.execution, Execution::Concurrent) {
            slots
        } else {
            0
        },
        if matches!(args.execution, Execution::Concurrent) {
            queued_bytes
        } else {
            0
        },
    ))
}
fn video_workers(args: &Cli) -> usize {
    // Chunk planning further caps auto at the clip's number of IDR chunks.
    let auto = || {
        std::thread::available_parallelism()
            .map_or(1, |n| n.get())
            .clamp(1, 16)
    };
    match args.video_workers {
        Some(0) => auto(),
        Some(requested) => requested,
        // Experimental internal decoder threads only run on the single-decoder path.
        None if args.decoder_threads > 0 => 1,
        None => auto(),
    }
}
fn decode_video<F>(
    args: &Cli,
    mp4: &Mp4<'_>,
    config: video::VideoConfig,
    metrics: &Metrics,
    cancel: &Cancellation,
    input_mapping: Option<&Mmap>,
    on_epoch: F,
) -> Result<video::VideoInfo>
where
    F: FnMut(usize, i64, &[f32]) -> Result<()>,
{
    let workers = video_workers(args);
    if workers <= 1 {
        // Unchanged v0.1.6 single-decoder path.
        return video::decode_h264_mp4(mp4, config, metrics, cancel, on_epoch);
    }
    video_parallel::decode_h264_mp4_parallel(
        mp4,
        config,
        video_parallel::ParallelConfig {
            workers,
            chunk_target_ms: args.chunk_target_ms,
            buffer_bytes: args.video_buffer_mib * 1024 * 1024,
        },
        metrics,
        cancel,
        input_mapping,
        on_epoch,
    )
}
fn log_video(info: &video::VideoInfo) {
    eprintln!(
        "video {}x{}, duration {}ms, decoded {} access units, resized {} selected pictures, skipped_nonref={}",
        info.width,
        info.height,
        info.duration_ms,
        info.sample_count - info.skipped_count,
        info.resized_count,
        info.skipped_count
    );
}

struct OutputGuard {
    path: PathBuf,
    completed: bool,
}
impl Drop for OutputGuard {
    fn drop(&mut self) {
        if !self.completed {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}
