//! `tenzorpipe._engine`: the TenzorPipe engine inside the Python process.
//!
//! Options travel as command-line arguments and are parsed by the same `clap` definition as the
//! `tenzor` binary, so defaults and validation cannot drift between the CLI and Python.
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

pyo3::create_exception!(
    _engine,
    TenzorError,
    PyRuntimeError,
    "A conversion failed. Any partial output file has been removed."
);

fn parse(argv: Vec<String>) -> PyResult<tenzor_pipe::Cli> {
    tenzor_pipe::parse_args(std::iter::once("tenzor".to_owned()).chain(argv))
        .map_err(|e| PyValueError::new_err(e.to_string()))
}

/// Convert one input. `argv` excludes the program name, e.g. `["-i", "a.mp4", "-o", "a.tenzor"]`.
/// Returns `(epochs, seconds, profile_json_or_None)`. The GIL is released while decoding.
/// Nothing is printed except the engine's own diagnostics, which `--quiet` suppresses.
#[pyfunction]
fn convert(py: Python<'_>, argv: Vec<String>) -> PyResult<(usize, f64, Option<String>)> {
    let cli = parse(argv)?;
    let summary = py
        .detach(|| tenzor_pipe::convert(&cli))
        .map_err(|e| TenzorError::new_err(format!("{e:#}")))?;
    Ok((summary.epochs, summary.seconds, summary.profile))
}

/// Behave like the `tenzor` binary: print usage errors, help, the summary line or the error
/// chain, and return the process exit code.
#[pyfunction]
fn run_cli(py: Python<'_>, argv: Vec<String>) -> i32 {
    let cli = match tenzor_pipe::parse_args(std::iter::once("tenzor".to_owned()).chain(argv)) {
        Ok(cli) => cli,
        Err(e) => {
            let _ = e.print();
            return e.exit_code();
        }
    };
    match py.detach(|| tenzor_pipe::convert(&cli)) {
        Ok(summary) => {
            if let Some(profile) = &summary.profile {
                eprintln!("TENZOR_PROFILE {profile}");
            }
            println!(
                "TenzorPipe {}: {} epochs in {:.3}s",
                env!("CARGO_PKG_VERSION"),
                summary.epochs,
                summary.seconds
            );
            0
        }
        Err(e) => {
            eprintln!("Error: {e:?}");
            1
        }
    }
}

#[pymodule]
fn _engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("TenzorError", m.py().get_type::<TenzorError>())?;
    m.add_function(wrap_pyfunction!(convert, m)?)?;
    m.add_function(wrap_pyfunction!(run_cli, m)?)?;
    Ok(())
}
