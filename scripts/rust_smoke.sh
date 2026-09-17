#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cargo build --release --locked
cargo test --locked --all-targets
python3 scripts/generate_matrix.py
python3 scripts/test_matrix.py
