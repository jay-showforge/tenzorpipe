# Source from WSL: user-level Rust 1.98.1 + NASM, build output on the Linux filesystem (fast I/O).
. "$HOME/.cargo/env"
export PATH="$HOME/.local/bin:$PATH"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.tenzor-build/target}"
