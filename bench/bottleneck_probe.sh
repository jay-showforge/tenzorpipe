#!/usr/bin/env bash
# Follow-up measurements for the BENCHMARKS.md analysis (decoder speed, frame types, per-worker memory).
. ~/.tenzor-bench/env.sh
cd "$(dirname "$0")/.."
C=${1:-$HOME/.tenzor-bench/clips/synthetic-1080p30-h264-20s-video-only.mp4}
lscpu | grep -E "^Model name|^Thread\(s\) per core|^Core\(s\) per socket|^CPU\(s\)"
echo "== frame types"; ffprobe -v error -select_streams v -show_entries frame=pict_type -of csv=p=0 "$C" | sort | uniq -c
echo "== frames left after skipping non-reference frames"
ffprobe -v error -skip_frame noref -select_streams v -count_frames -show_entries stream=nb_read_frames -of csv=p=0 "$C"
echo "== libavcodec decode, 1 thread";   /usr/bin/time -f "%e s wall, %P CPU" ffmpeg -v error -threads 1 -i "$C" -f null - 2>&1
echo "== libavcodec decode, 1 thread, skip noref"; /usr/bin/time -f "%e s wall, %P CPU" ffmpeg -v error -threads 1 -skip_frame noref -i "$C" -f null - 2>&1
echo "== libavcodec decode, auto threads"; /usr/bin/time -f "%e s wall, %P CPU" ffmpeg -v error -i "$C" -f null - 2>&1
echo "== tenzor worker sweep: 3 runs each, wall s and maxrss MiB"
for w in 1 2 4 6 8 10 12 14; do
  line="workers=$w:"
  for r in 1 2 3; do
    rm -f /dev/shm/probe.tenzor
    out=$( { /usr/bin/time -f "%e %M %P" bin/tenzor-linux-x86_64 -i "$C" -o /dev/shm/probe.tenzor --video-workers "$w" 2>/dev/shm/probe.err >/dev/null; } 2>&1; tail -1 /dev/shm/probe.err)
    line="$line $(echo "$out" | awk '{printf "%ss/%dMiB/%s", $1, $2/1024, $3}')"
  done
  mode=$(grep -o "video_mode=[a-z_]* workers=[0-9]* window=[0-9]* chunks=[0-9]*" /dev/shm/probe.err | head -1)
  echo "$line  [$mode]"
done
rm -f /dev/shm/probe.tenzor
