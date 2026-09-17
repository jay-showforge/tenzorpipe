#!/usr/bin/env bash
# Extra chunked-decoder fixtures. Generate long clips with generate_matrix.py.
set -euo pipefail
cd "$(dirname "$0")/../fixtures"
F="ffmpeg -hide_banner -loglevel error -y"
$F -f lavfi -i "testsrc2=size=1280x720:rate=30:duration=20" -c:v libx264 -threads 2 -preset veryfast -bf 3 -g 250 -keyint_min 10 -sc_threshold 40 -x264-params open-gop=1 -pix_fmt yuv420p gen-open-gop.mp4
$F -f lavfi -i "testsrc2=size=1280x720:rate=30:duration=12" -c:v libx264 -threads 2 -preset veryfast -bf 3 -g 45 -pix_fmt yuv420p gen-720-g45.mp4
$F -f lavfi -i "testsrc2=size=640x360:rate=24:duration=8" -c:v libx264 -threads 2 -preset veryfast -profile:v baseline -bf 0 -g 12 -pix_fmt yuv420p gen-baseline-g12.mp4
echo "Generated v0.1.8 fixtures"
