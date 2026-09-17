use clap::Parser;

fn main() -> anyhow::Result<()> {
    let summary = tenzor_pipe::convert(&tenzor_pipe::Cli::parse())?;
    println!(
        "TenzorPipe {}: {} epochs in {:.3}s",
        env!("CARGO_PKG_VERSION"),
        summary.epochs,
        summary.seconds
    );
    Ok(())
}
