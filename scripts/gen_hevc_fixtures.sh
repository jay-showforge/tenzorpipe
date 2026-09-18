#!/usr/bin/env bash
# H.265 fixtures for the decode gates (FFmpeg is a test tool only, never a runtime dependency).
set -euo pipefail
cd "$(dirname "$0")/../fixtures"
F="ffmpeg -hide_banner -loglevel error -y"
# Main profile 8-bit 4:2:0, B-pyramid, 2 s keyframe interval, with AAC audio.
$F -f lavfi -i testsrc2=size=640x360:rate=30:duration=6 \
   -f lavfi -i "aevalsrc=0.3*sin(2*PI*440*t)|0.2*sin(2*PI*880*t):s=48000:d=6" \
   -c:v libx265 -preset medium -x265-params "keyint=60:bframes=4" -pix_fmt yuv420p \
   -tag:v hvc1 -c:a aac -movflags +faststart hevc-av.mp4
# Video only, larger frame, closed GOP.
$F -f lavfi -i testsrc2=size=1280x720:rate=30:duration=4 \
   -c:v libx265 -preset veryfast -x265-params "keyint=30:bframes=3:open-gop=0" \
   -pix_fmt yuv420p -tag:v hvc1 hevc-720.mp4
# Intra only: every frame an IRAP.
$F -f lavfi -i testsrc2=size=320x240:rate=25:duration=3 \
   -c:v libx265 -preset ultrafast -x265-params "keyint=1" -pix_fmt yuv420p hevc-intra.mp4
# BT.709 signalling.
$F -f lavfi -i testsrc2=size=640x360:rate=30:duration=3 \
   -c:v libx265 -preset veryfast -pix_fmt yuv420p \
   -color_primaries bt709 -color_trc bt709 -colorspace bt709 hevc-bt709.mp4
# Unsupported by design: Main 10.
$F -f lavfi -i testsrc2=size=320x240:rate=30:duration=2 \
   -c:v libx265 -preset ultrafast -pix_fmt yuv420p10le hevc-main10.mp4
echo "Generated $(ls hevc-*.mp4 | wc -l) H.265 fixtures"
