#!/usr/bin/env python3
"""Generated test media. FFmpeg is only a test tool, never a runtime dependency."""
import subprocess, pathlib, argparse
p=argparse.ArgumentParser(); p.add_argument('--long',action='store_true'); p.add_argument('--extended-memory',action='store_true'); a=p.parse_args()
d=pathlib.Path(__file__).resolve().parents[1]/'fixtures'; d.mkdir(exist_ok=True)
def ff(name,args):
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*args,str(d/name)],check=True)
def mp4(name,duration,size='1280x720',profile='high',bf='3',audio=True,codec='libx264'):
    args=['-f','lavfi','-i',f'testsrc2=size={size}:rate=30:duration={duration}']
    if audio: args+=['-f','lavfi','-i',f'aevalsrc=0.3*sin(2*PI*440*t)|0.2*sin(2*PI*880*t):s=48000:d={duration}']
    args+=['-c:v',codec,'-threads','2']
    if codec=='libx264': args+=['-preset','veryfast','-profile:v',profile,'-bf',bf,'-g','60','-pix_fmt','yuv420p']
    if audio: args+=['-c:a','aac','-b:a','128k']
    args+=['-movflags','+faststart']; ff(name,args)
mp4('baseline720.mp4',3.27,profile='baseline',bf='0')
mp4('high-bframes.mp4',3.27)
mp4('short.mp4',0.13)
mp4('silent-video.mp4',1.27,audio=False)
mp4('unsupported-mpeg4.mp4',1,codec='mpeg4')
for rate,ch in [(44100,2),(48000,2),(16000,1),(48000,6)]:
    formula='|'.join(f'{.3/ch}*sin(2*PI*{440+220*i}*t)' for i in range(ch))
    ff(f'wav-{rate}-{ch}ch.wav',['-f','lavfi','-i',f'aevalsrc={formula}:s={rate}:d=1.27','-c:a','pcm_s16le'])
ff('audio-only.mp4',['-i',str(d/'baseline720.mp4'),'-vn','-c:a','copy'])
(d/'empty.mp4').write_bytes(b'')
(d/'corrupt.mp4').write_bytes((d/'high-bframes.mp4').read_bytes()[:300])
(d/'unsupported.mp3').write_bytes(b'not an mp3')
ff('color709.mp4',['-i',str(d/'high-bframes.mp4'),'-t','1.27','-vf','colorspace=iall=bt601-6-625:all=bt709','-c:v','libx264','-threads','2','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-c:a','copy'])
ff('fullrange.mp4',['-i',str(d/'high-bframes.mp4'),'-t','1.27','-vf','scale=in_range=limited:out_range=full','-c:v','libx264','-threads','2','-pix_fmt','yuvj420p','-color_range','pc','-c:a','copy'])
b=bytearray((d/'high-bframes.mp4').read_bytes());idx=b.index(b'mdat')+4;b[idx:idx+4]=bytes([127,255,255,255]);(d/'corrupt-nal.mp4').write_bytes(b)
if a.long:
    for duration in [30,330]: mp4(f'long-{duration}.mp4',duration,size='640x360')
print('Generated media fixtures in',d)
ff('sync-pulse.mp4',['-f','lavfi','-i',"color=black:s=1280x720:r=30:d=2.27,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(t,1,1.1)'",'-f','lavfi','-i','aevalsrc=0.5*sin(2*PI*1000*t)*between(t\\,1\\,1.1)|0.5*sin(2*PI*1000*t)*between(t\\,1\\,1.1):s=44100:d=2.27','-c:v','libx264','-threads','2','-profile:v','high','-bf','3','-c:a','aac'])
ff('aac-6ch.mp4',['-i',str(d/'wav-48000-6ch.wav'),'-c:a','aac'])
ff('tiny.wav',['-f','lavfi','-i','aevalsrc=0.5*sin(2*PI*1000*t):s=48000:d=0.005','-c:a','pcm_f32le'])

if a.extended_memory:
    for duration in [90,660]: mp4(f"long-{duration}.mp4",duration,size="640x360")

if a.extended_memory:
    ff('long-330-video_only.mp4',['-i',str(d/'long-330.mp4'),'-map','0:v:0','-c','copy'])
    ff('long-330-audio_only.mp4',['-i',str(d/'long-330.mp4'),'-map','0:a:0','-c','copy'])
