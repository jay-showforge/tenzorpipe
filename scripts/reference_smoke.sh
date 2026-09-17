#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p out
python3 reference/ffmpeg_arrow_baseline.py fixtures/long-30.mp4 out/baseline-smoke.tenzor
