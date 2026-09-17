#!/usr/bin/env bash
# Varied AAC/WAV media for the v0.2.0 audio byte-identity checks (FFmpeg is a test tool only).
set -euo pipefail
D="$(dirname "$0")/../fixtures/audio-v020"; mkdir -p "$D"; cd "$D"
F="ffmpeg -hide_banner -loglevel error -y"
$F -f lavfi -i "aevalsrc=0.25*sin(2*PI*220*t)*(1+0.5*sin(2*PI*0.5*t))+0.15*sin(2*PI*1330*t+3*sin(2*PI*5*t))+0.3*(random(0)-0.5)*gt(mod(t\,1.3)\,1.2)+0.1*(random(1)-0.5)|0.2*sin(2*PI*330*t)+0.3*(random(2)-0.5)*gt(mod(t\,0.7)\,0.65):s=48000:d=40" -c:a pcm_s16le music48k.wav
$F -i music48k.wav -c:a aac -b:a 192k -movflags +faststart m48-192k.mp4
$F -i music48k.wav -c:a aac -b:a 32k -movflags +faststart m48-32k.mp4
$F -i music48k.wav -c:a aac -b:a 256k -aac_pns 1 -aac_tns 1 -aac_is 1 -aac_ms 1 -movflags +faststart m48-tools.mp4
$F -i music48k.wav -ar 44100 -c:a aac -b:a 128k m441.mp4
$F -i music48k.wav -ar 22050 -ac 1 -c:a aac -b:a 64k m22-mono.mp4
$F -i music48k.wav -ar 8000 -ac 1 -c:a aac -b:a 24k m8-mono.mp4
$F -i music48k.wav -ar 96000 -c:a aac -b:a 256k m96.mp4
$F -f lavfi -i "anoisesrc=d=15:c=white:r=48000:a=0.5:s=12345" -ac 1 -c:a aac -b:a 320k noise-320k.mp4
$F -f lavfi -i "anullsrc=r=48000:cl=stereo" -t 5 -c:a aac silence.mp4
$F -f lavfi -i "anoisesrc=d=12:c=pink:r=48000:a=0.4:s=54321" -af "pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0" -c:a aac -b:a 384k pink-51.mp4
$F -f lavfi -i "testsrc2=size=640x360:rate=30:duration=40" -i m48-192k.mp4 -map 0:v -map 1:a -c:v libx264 -preset veryfast -g 60 -bf 3 -pix_fmt yuv420p -c:a copy -shortest av-music.mp4
$F -i music48k.wav -c:a pcm_f32le music-f32.wav
$F -i music48k.wav -ar 44100 -ac 1 -c:a pcm_s24le music-441-s24.wav
echo "Generated $(ls | wc -l) files in fixtures/audio-v020"

# Valid fixtures must probe successfully; error agreement is not decode success.
for media in *.mp4 *.wav; do ffprobe -v error -show_entries format=duration -of csv=p=0 "$media" >/dev/null; done
