use crate::metrics::{Metrics, Stage};
use anyhow::{Context, Result};
use arrow::array::{ArrayRef, FixedSizeListArray, Float32Array, Int64Array, RecordBatch};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::ipc::writer::FileWriter;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::sync::Arc;

#[derive(Debug, Clone)]
pub struct TenzorMetadata {
    pub has_audio: bool,
    pub video_matrix: String,
    pub window_ms: u64,
    pub video_shape: Option<(usize, usize, usize)>,
    pub audio_shape: (usize, usize),
}

pub struct TenzorWriter {
    schema: Arc<Schema>,
    writer: FileWriter<BufWriter<File>>,
    has_video: bool,
    video_dim: usize,
    audio_dim: usize,
    metrics: Metrics,
}

impl TenzorWriter {
    pub fn create(file: File, meta: &TenzorMetadata, metrics: Metrics) -> Result<Self> {
        let mut fields = vec![Field::new("timestamp_ms", DataType::Int64, false)];
        let mut metadata = HashMap::new();
        metadata.insert("tenzor_version".into(), env!("CARGO_PKG_VERSION").into());
        metadata.insert("window_ms".into(), meta.window_ms.to_string());
        metadata.insert(
            "audio_shape".into(),
            format!("{},{}", meta.audio_shape.0, meta.audio_shape.1),
        );
        metadata.insert("audio_sample_rate".into(), "16000".into());
        metadata.insert("audio_feature".into(), "log10-mel-power".into());
        metadata.insert("video_normalization".into(), "RGB CHW [-1,1]".into());
        metadata.insert("video_matrix".into(), meta.video_matrix.clone());

        metadata.insert("has_audio".into(), meta.has_audio.to_string());
        metadata.insert("audio_hop_samples".into(), "160".into());
        metadata.insert("audio_fft_samples".into(), "400".into());
        metadata.insert(
            "audio_window".into(),
            "symmetric Hann, left-aligned, zero-pad EOF".into(),
        );
        metadata.insert(
            "audio_mel_scale".into(),
            "HTK, 0..8000Hz, 64 triangular bands, no area normalization".into(),
        );
        metadata.insert("audio_log_floor".into(), "log10(power + 1e-10)".into());
        metadata.insert(
            "audio_resampler".into(),
            "129-tap Blackman sinc, 1024 phases, cutoff 0.95*min(1,16000/source_rate)".into(),
        );
        metadata.insert("audio_downmix".into(), "arithmetic channel mean".into());
        metadata.insert(
            "video_sampling".into(),
            "nearest presentation frame, earlier on tie; repeat last through audio tail".into(),
        );
        metadata.insert(
            "video_resize".into(),
            "nearest, align-corners, square stretch".into(),
        );
        metadata.insert(
            "timestamp_origin".into(),
            "movie presentation timeline after edit lists; epoch starts".into(),
        );
        fields.push(Field::new("audio_valid_frames", DataType::Int64, false));
        let (has_video, video_dim) = if let Some((c, h, w)) = meta.video_shape {
            let dim = c * h * w;
            metadata.insert("video_shape".into(), format!("{c},{h},{w}"));
            fields.push(Field::new("video_timestamp_ms", DataType::Int64, false));
            fields.push(Field::new(
                "video_tensor",
                DataType::FixedSizeList(
                    Arc::new(Field::new("item", DataType::Float32, false)),
                    dim as i32,
                ),
                false,
            ));
            (true, dim)
        } else {
            (false, 0)
        };

        let audio_dim = meta.audio_shape.0 * meta.audio_shape.1;
        fields.push(Field::new(
            "audio_mel_tensor",
            DataType::FixedSizeList(
                Arc::new(Field::new("item", DataType::Float32, false)),
                audio_dim as i32,
            ),
            false,
        ));

        let schema = Arc::new(Schema::new_with_metadata(fields, metadata));
        let writer = FileWriter::try_new(BufWriter::new(file), &schema)
            .context("create Arrow IPC writer")?;
        Ok(Self {
            schema,
            writer,
            has_video,
            video_dim,
            audio_dim,
            metrics,
        })
    }

    pub fn write_batch(
        &mut self,
        timestamps: Vec<i64>,
        video_flat: Option<Vec<f32>>,
        audio_flat: Vec<f32>,
        valid_frames: Vec<i64>,
        video_pts: Vec<i64>,
    ) -> Result<()> {
        let started = self.metrics.start();
        let rows = timestamps.len();
        if rows == 0 {
            return Ok(());
        }
        anyhow::ensure!(
            audio_flat.len() == rows * self.audio_dim,
            "audio batch shape mismatch"
        );
        if self.has_video {
            anyhow::ensure!(
                video_flat.as_ref().map_or(0, Vec::len) == rows * self.video_dim,
                "video batch shape mismatch"
            );
        }

        let mut arrays: Vec<ArrayRef> = Vec::with_capacity(if self.has_video { 3 } else { 2 });
        arrays.push(Arc::new(Int64Array::from(timestamps)));
        arrays.push(Arc::new(Int64Array::from(valid_frames)));

        if self.has_video {
            arrays.push(Arc::new(Int64Array::from(video_pts)));
            let values = Float32Array::from(video_flat.expect("validated video batch"));
            let list = FixedSizeListArray::try_new(
                Arc::new(Field::new("item", DataType::Float32, false)),
                self.video_dim as i32,
                Arc::new(values),
                None,
            )?;
            arrays.push(Arc::new(list));
        }

        let values = Float32Array::from(audio_flat);
        let list = FixedSizeListArray::try_new(
            Arc::new(Field::new("item", DataType::Float32, false)),
            self.audio_dim as i32,
            Arc::new(values),
            None,
        )?;
        arrays.push(Arc::new(list));

        let batch = RecordBatch::try_new(self.schema.clone(), arrays)?;
        self.metrics.end(Stage::ArrowPack, started);
        let started = self.metrics.start();
        self.writer.write(&batch)?;
        self.metrics.end(Stage::ArrowWrite, started);
        Ok(())
    }

    pub fn finish(mut self) -> Result<()> {
        let started = self.metrics.start();
        self.writer.finish()?;
        self.writer.into_inner()?.flush()?;
        self.metrics.end(Stage::ArrowWrite, started);
        Ok(())
    }
}

pub type BatchBuffers = (Vec<i64>, Option<Vec<f32>>, Vec<f32>, Vec<i64>, Vec<i64>);

pub struct BatchAccumulator {
    pub timestamps: Vec<i64>,
    pub valid_frames: Vec<i64>,
    pub video_pts: Vec<i64>,
    pub video: Option<Vec<f32>>,
    pub audio: Vec<f32>,
    cap_rows: usize,
    video_dim: usize,
    audio_dim: usize,
}

impl BatchAccumulator {
    pub fn new(cap_rows: usize, video_dim: Option<usize>, audio_dim: usize) -> Self {
        Self {
            timestamps: Vec::with_capacity(cap_rows),
            valid_frames: Vec::with_capacity(cap_rows),
            video_pts: Vec::with_capacity(cap_rows),
            video: video_dim.map(|d| Vec::with_capacity(cap_rows * d)),
            audio: Vec::with_capacity(cap_rows * audio_dim),
            cap_rows,
            video_dim: video_dim.unwrap_or(0),
            audio_dim,
        }
    }

    pub fn push(
        &mut self,
        timestamp_ms: i64,
        video: Option<&[f32]>,
        audio: &[f32],
        valid_frames: i64,
        video_pts: i64,
    ) -> Result<()> {
        anyhow::ensure!(audio.len() == self.audio_dim, "audio row shape mismatch");
        self.timestamps.push(timestamp_ms);
        self.valid_frames.push(valid_frames);
        self.video_pts.push(video_pts);
        self.audio.extend_from_slice(audio);
        if let Some(dst) = self.video.as_mut() {
            let src = video.context("missing video tensor for multimodal batch")?;
            anyhow::ensure!(src.len() == self.video_dim, "video row shape mismatch");
            dst.extend_from_slice(src);
        }
        Ok(())
    }

    pub fn is_full(&self) -> bool {
        self.timestamps.len() >= self.cap_rows
    }
    pub fn is_empty(&self) -> bool {
        self.timestamps.is_empty()
    }

    pub fn drain(&mut self) -> BatchBuffers {
        let ts = std::mem::replace(&mut self.timestamps, Vec::with_capacity(self.cap_rows));
        let video = self
            .video
            .as_mut()
            .map(|v| std::mem::replace(v, Vec::with_capacity(self.cap_rows * self.video_dim)));
        let audio = std::mem::replace(
            &mut self.audio,
            Vec::with_capacity(self.cap_rows * self.audio_dim),
        );
        (
            ts,
            video,
            audio,
            std::mem::take(&mut self.valid_frames),
            std::mem::take(&mut self.video_pts),
        )
    }
}
