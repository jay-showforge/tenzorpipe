//! Bounded channels with broadcast cancellation and unconditional worker joins.
use crate::metrics::{Metrics, Stage};
use anyhow::{Result, anyhow, ensure};
use crossbeam_channel::{Receiver, Sender, bounded, select};
use std::sync::{
    Mutex,
    atomic::{AtomicBool, Ordering},
};

#[derive(Debug)]
pub struct Cancelled;
impl std::fmt::Display for Cancelled {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "pipeline cancelled")
    }
}
impl std::error::Error for Cancelled {}
pub struct Cancellation {
    flag: AtomicBool,
    sender: Mutex<Option<Sender<()>>>,
    receiver: Receiver<()>,
}
impl Cancellation {
    pub fn new() -> Self {
        let (tx, rx) = bounded(0);
        Self {
            flag: AtomicBool::new(false),
            sender: Mutex::new(Some(tx)),
            receiver: rx,
        }
    }
    pub fn cancel(&self) {
        self.flag.store(true, Ordering::Release);
        self.sender.lock().unwrap_or_else(|e| e.into_inner()).take();
    }
    pub fn check(&self) -> Result<()> {
        if self.flag.load(Ordering::Acquire) {
            Err(Cancelled.into())
        } else {
            Ok(())
        }
    }
    pub fn send<T>(&self, tx: &Sender<T>, value: T, metrics: &Metrics) -> Result<()> {
        self.check()?;
        let start = metrics.start();
        let result = select! {recv(self.receiver)->_=>Err(Cancelled.into()),send(tx,value)->r=>r.map_err(|_|Cancelled.into())};
        metrics.end(Stage::SendWait, start);
        result
    }
    pub fn recv<T>(&self, rx: &Receiver<T>, metrics: &Metrics) -> Result<T> {
        self.check()?;
        let start = metrics.start();
        let result = select! {recv(self.receiver)->_=>Err(Cancelled.into()),recv(rx)->r=>r.map_err(|_|Cancelled.into())};
        metrics.end(Stage::ReceiveWait, start);
        result
    }
}
pub struct AudioEpoch {
    pub index: usize,
    pub values: Vec<f32>,
    pub valid: usize,
}
pub struct VideoEpoch {
    pub index: usize,
    pub pts: i64,
    pub values: Vec<f32>,
}
pub enum Message<T> {
    Data(T),
    End(usize),
}

/// Payload bytes queued across both tracks, not counting bounded worker/collector
/// working buffers, decoder state, allocator overhead or the Arrow batch.
pub fn queue_capacity(
    depth: usize,
    budget: usize,
    audio_bytes: usize,
    video_bytes: usize,
) -> (usize, usize) {
    let row = audio_bytes + video_bytes;
    let slots = depth.min(budget.checked_div(row).unwrap_or(0));
    (slots, slots * row)
}

pub(crate) fn worker<T, F: FnOnce() -> Result<T>>(
    f: F,
    cancel: &Cancellation,
    label: &str,
) -> Result<T> {
    let result =
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)).unwrap_or_else(|payload| {
            let message = payload
                .downcast_ref::<String>()
                .map(String::as_str)
                .or_else(|| payload.downcast_ref::<&str>().copied())
                .unwrap_or("unknown panic");
            Err(anyhow!("{label} worker panicked: {message}"))
        });
    if result.is_err() {
        cancel.cancel();
    }
    result
}

pub fn run<A, V, C>(
    capacity: usize,
    audio: Option<A>,
    video: Option<V>,
    collector: C,
) -> Result<usize>
where
    A: FnOnce(Sender<Message<AudioEpoch>>, &Cancellation) -> Result<()> + Send,
    V: FnOnce(Sender<Message<VideoEpoch>>, &Cancellation) -> Result<()> + Send,
    C: FnOnce(
        Option<&Receiver<Message<AudioEpoch>>>,
        Option<&Receiver<Message<VideoEpoch>>>,
        &Cancellation,
    ) -> Result<usize>,
{
    let cancel = Cancellation::new();
    std::thread::scope(|scope| {
        let _guard = CancelOnDrop(Some(&cancel));
        let (atx, arx) = bounded(capacity);
        let (vtx, vrx) = bounded(capacity);
        let audio_on = audio.is_some();
        let video_on = video.is_some();
        let ah = audio
            .map(|f| {
                let c = &cancel;
                std::thread::Builder::new()
                    .name("tenzor-audio".into())
                    .spawn_scoped(scope, move || worker(|| f(atx, c), c, "audio"))
            })
            .transpose()?;
        let vh = video
            .map(|f| {
                let c = &cancel;
                std::thread::Builder::new()
                    .name("tenzor-video".into())
                    .spawn_scoped(scope, move || worker(|| f(vtx, c), c, "video"))
            })
            .transpose()?;
        // Catch collector panics too: scoped threads must never be joined while
        // a producer is stranded on a full/rendezvous channel.
        let collected = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            collector(audio_on.then_some(&arx), video_on.then_some(&vrx), &cancel)
        }))
        .unwrap_or_else(|_| Err(anyhow!("collector panicked")));
        if collected.is_err() {
            cancel.cancel();
        }
        drop(arx);
        drop(vrx);
        let audio_result = ah
            .map(|h| {
                h.join()
                    .unwrap_or_else(|_| Err(anyhow!("audio thread panicked outside worker")))
            })
            .unwrap_or(Ok(()));
        let video_result = vh
            .map(|h| {
                h.join()
                    .unwrap_or_else(|_| Err(anyhow!("video thread panicked outside worker")))
            })
            .unwrap_or(Ok(()));
        // Keep the original processing/I/O failure, rather than its cancellation consequence.
        if let Err(ref e) = collected
            && !e.is::<Cancelled>()
        {
            return Err(collected.unwrap_err());
        }
        for result in [audio_result, video_result] {
            if let Err(e) = result
                && !e.is::<Cancelled>()
            {
                return Err(e);
            }
        }
        collected
    })
}

pub fn check_index(actual: usize, expected: usize) -> Result<()> {
    ensure!(
        actual == expected,
        "epoch order mismatch: expected {expected}, got {actual}"
    );
    Ok(())
}

/// Must live inside the thread scope so cancellation precedes implicit joins.
pub(crate) struct CancelOnDrop<'a>(pub Option<&'a Cancellation>);
impl Drop for CancelOnDrop<'_> {
    fn drop(&mut self) {
        if let Some(c) = self.0 {
            c.cancel();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;
    #[test]
    fn byte_budget_scales_with_resolution_and_rendezvous() {
        for resolution in [160, 224, 336, 1024] {
            for budget in [0, 1 << 20, 4 << 20] {
                let row = 3 * resolution * resolution * 4 + 50 * 64 * 4;
                let (slots, bytes) =
                    queue_capacity(2, budget, 50 * 64 * 4, 3 * resolution * resolution * 4);
                assert!(slots <= 2 && bytes <= budget);
                assert_eq!(bytes, slots * row);
            }
        }
    }
    fn failure_case(cap: usize, panic_worker: bool, panic_collector: bool) {
        let (done, received) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let audio = |tx, c: &Cancellation| -> Result<()> {
                for index in 0..1000 {
                    c.send(
                        &tx,
                        Message::Data(AudioEpoch {
                            index,
                            values: vec![0.; 64],
                            valid: 1,
                        }),
                        &Metrics::new(false),
                    )?;
                }
                Ok(())
            };
            let video = |_tx, c: &Cancellation| -> Result<()> {
                if panic_worker {
                    panic!("injected worker failure");
                }
                c.check()?;
                Err(anyhow!("injected decode failure"))
            };
            let result = run(cap, Some(audio), Some(video), |_, _, _| {
                if panic_collector {
                    panic!("injected collector failure");
                }
                Err(anyhow!("injected writer failure"))
            });
            done.send(result.is_err()).unwrap();
        });
        assert!(
            received
                .recv_timeout(Duration::from_secs(2))
                .expect("pipeline deadlocked")
        );
    }
    #[test]
    fn full_and_rendezvous_queues_cancel_without_deadlock() {
        for cap in [0, 1, 2] {
            failure_case(cap, false, false);
        }
    }
    #[test]
    fn worker_and_collector_panics_join_without_deadlock() {
        failure_case(1, true, false);
        failure_case(0, false, true);
    }
    #[test]
    fn cancellation_wakes_blocked_receiver() {
        let c = Cancellation::new();
        let (tx, rx) = bounded::<usize>(0);
        std::thread::scope(|s| {
            s.spawn(|| {
                c.cancel();
            });
            assert!(c.recv(&rx, &Metrics::new(false)).is_err());
            drop(tx);
        });
    }
}
