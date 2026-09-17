//! Common MP4 presentation timeline, including encoder priming edits.
use anyhow::{Result, ensure};
use mp4io::Track;

#[derive(Clone, Copy)]
pub struct Timeline {
    pub skip_sec: f64,
    pub delay_sec: f64,
    pub duration_sec: f64,
}
impl Timeline {
    pub fn for_track(track: &Track<'_>, movie_scale: u32) -> Result<Self> {
        ensure!(
            track.timescale() > 0 && movie_scale > 0,
            "invalid MP4 timescale"
        );
        ensure!(
            track.sample_entries().len() == 1,
            "multiple sample descriptions are unsupported"
        );
        let edits = track.edits();
        let mut skip_sec = 0.;
        let mut delay_sec = 0.;
        let mut duration_sec = track.duration_secs();
        if !edits.is_empty() {
            ensure!(edits.len() <= 2, "complex MP4 edit lists are unsupported");
            let mut active = 0;
            for (i, e) in edits.iter().enumerate() {
                ensure!(
                    e.media_rate == 1.0,
                    "non-unit MP4 edit rates are unsupported"
                );
                if e.media_time == -1 {
                    ensure!(i == 0 && edits.len() == 2, "unsupported empty MP4 edit");
                    delay_sec = e.segment_duration as f64 / movie_scale as f64;
                } else {
                    ensure!(e.media_time >= 0, "invalid MP4 media time");
                    active += 1;
                    skip_sec = e.media_time as f64 / track.timescale() as f64;
                    duration_sec = if e.segment_duration > 0 {
                        e.segment_duration as f64 / movie_scale as f64
                    } else {
                        track.duration_secs() - skip_sec
                    };
                }
            }
            ensure!(active == 1, "multiple active MP4 edits are unsupported");
        }
        duration_sec += delay_sec;
        ensure!(
            duration_sec.is_finite() && duration_sec > 0.,
            "empty or invalid track duration"
        );
        Ok(Self {
            skip_sec,
            delay_sec,
            duration_sec,
        })
    }
    pub fn pts_sec(&self, cts: i64, scale: u32) -> f64 {
        cts as f64 / scale as f64 - self.skip_sec + self.delay_sec
    }
}
