//! Optional wall-clock stage timers. Overlapping worker durations are not additive.
//! With --video-workers > 1, video_parse/decode/resize are summed across decode workers.
use std::sync::{
    Arc,
    atomic::{AtomicU64, Ordering},
};
use std::time::Instant;
#[derive(Clone, Copy)]
#[repr(usize)]
pub enum Stage {
    Setup,
    VideoParse,
    VideoDecode,
    VideoResize,
    AudioSource,
    AudioResample,
    AudioMel,
    ArrowPack,
    ArrowWrite,
    SendWait,
    ReceiveWait,
    FinalSync,
    VideoWindowWait,
    VideoReorderWait,
    AudioSourceWait,
}
const NAMES: [&str; 15] = [
    "setup",
    "video_parse",
    "video_decode",
    "video_resize",
    "audio_source",
    "audio_resample",
    "audio_mel",
    "arrow_pack",
    "arrow_write",
    "worker_send_wait",
    "collector_receive_wait",
    "final_sync",
    "video_window_wait",
    "video_reorder_wait",
    "audio_source_wait",
];
#[derive(Clone)]
pub struct Metrics {
    enabled: bool,
    values: Arc<[AtomicU64; 15]>,
}
impl Metrics {
    pub fn new(enabled: bool) -> Self {
        Self {
            enabled,
            values: Arc::new(std::array::from_fn(|_| AtomicU64::new(0))),
        }
    }
    pub fn start(&self) -> Option<Instant> {
        self.enabled.then(Instant::now)
    }
    pub fn nanos(&self, stage: Stage) -> u64 {
        self.values[stage as usize].load(Ordering::Relaxed)
    }
    pub fn end(&self, stage: Stage, start: Option<Instant>) {
        self.end_excluding(stage, start, 0);
    }
    pub fn end_excluding(&self, stage: Stage, start: Option<Instant>, exclude: u64) {
        if let Some(start) = start {
            self.values[stage as usize].fetch_add(
                (start.elapsed().as_nanos() as u64).saturating_sub(exclude),
                Ordering::Relaxed,
            );
        }
    }
    /// Profile JSON for `--profile` runs; `None` when profiling is off.
    pub fn report(
        &self,
        elapsed: f64,
        mode: &str,
        slots: usize,
        queue_bytes: usize,
        threads: usize,
    ) -> Option<String> {
        if !self.enabled {
            return None;
        }
        let stages = NAMES
            .iter()
            .enumerate()
            .map(|(i, name)| {
                format!(
                    "\"{name}\":{:.9}",
                    self.values[i].load(Ordering::Relaxed) as f64 / 1e9
                )
            })
            .collect::<Vec<_>>()
            .join(",");
        Some(format!(
            "{{\"wall_seconds\":{elapsed:.9},\"execution\":\"{mode}\",\"queue_slots_per_track\":{slots},\"queued_payload_budget_used_bytes\":{queue_bytes},\"decoder_threads\":{threads},\"stages_seconds\":{{{stages}}}}}"
        ))
    }
}
