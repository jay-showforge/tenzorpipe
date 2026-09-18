//! TenzorPipe engine: MP4 H.264/H.265/AAC-LC and WAV to synchronized RGB/CHW and Log-Mel tensors
//! in batched Apache Arrow IPC. The `tenzor` binary and the Python bindings both call
//! [`convert`].
mod audio;
pub mod cli;
mod concurrency;
mod media;
mod metrics;
mod storage;
mod video;
mod video_hevc;
mod video_parallel;

pub use cli::{Cli, Summary, convert, parse_args};
