#!/usr/bin/env python3
"""Streaming baseline: two FFmpeg pipes + NumPy STFT/Mel + batched Arrow.
Benchmark scope: generated CFR 30fps MP4 H.264/AAC, 0.5s epochs, BT.601 limited.
FFmpeg's stock 16k resampler is used, not TenzorPipe's 129-tap phase table.
No intermediate media files. Imports/startup and child processes count in timings.
"""
import argparse,subprocess,json,pathlib,math
import numpy as np
import pyarrow as pa,pyarrow.ipc as ipc
p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');p.add_argument('--resolution',type=int,default=224);p.add_argument('--batch-epochs',type=int,default=32);a=p.parse_args()
info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',a.input]));v=next(s for s in info['streams'] if s['codec_type']=='video');w,h=v['width'],v['height'];res=a.resolution;n=math.ceil(float(info['format']['duration'])*2)
assert v['avg_frame_rate']=='30/1' and v['pix_fmt']=='yuv420p'
video=subprocess.Popen(['ffmpeg','-v','error','-threads','1','-i',a.input,'-an','-vf',"select='not(mod(n,15))'",'-vsync','0','-pix_fmt','yuv420p','-f','rawvideo','-threads','1','pipe:1'],stdout=subprocess.PIPE)
audio=subprocess.Popen(['ffmpeg','-v','error','-threads','1','-i',a.input,'-vn','-af','pan=mono|c0=0.5*c0+0.5*c1','-ar','16000','-t',info['format']['duration'],'-f','f32le','pipe:1'],stdout=subprocess.PIPE)
def read_exact(pipe,n):
    chunks=[];size=0
    while size<n:
        b=pipe.read(n-size)
        if not b:break
        chunks.append(b);size+=len(b)
    return b''.join(chunks)
points=700*(10**(np.linspace(0,2595*np.log10(1+8000/700),66)/2595)-1)*400/16000;k=np.arange(201)[None,:];l,c,r=[points[i:i+64,None] for i in range(3)];fb=np.maximum(0,np.minimum((k-l)/(c-l),(r-k)/(r-c))).astype('float32');hann=np.hanning(400).astype('float32')
md={b'video_shape':f'3,{res},{res}'.encode(),b'audio_shape':b'50,64',b'window_ms':b'500',b'has_audio':b'true',b'tenzor_version':b'baseline',b'video_matrix':b'BT.601 limited'}
schema=pa.schema([('timestamp_ms',pa.int64()),('audio_valid_frames',pa.int64()),('video_timestamp_ms',pa.int64()),('video_tensor',pa.list_(pa.float32(),3*res*res)),('audio_mel_tensor',pa.list_(pa.float32(),3200))],metadata=md)
sy=np.arange(res)*(h-1)//(res-1);sx=np.arange(res)*(w-1)//(res-1);left=np.array([],np.float32);vs=[];aa=[];ts=[];valid=[];last=None
with pa.OSFile(a.output,'wb') as sink,ipc.new_file(sink,schema) as writer:
    for e in range(n):
        raw=read_exact(video.stdout,w*h*3//2)
        if raw:
            f=np.frombuffer(raw,np.uint8).astype('float32');y=f[:w*h].reshape(h,w)[sy][:,sx];u=f[w*h:w*h*5//4].reshape(h//2,w//2)[sy//2][:,sx//2]-128;vv=f[w*h*5//4:].reshape(h//2,w//2)[sy//2][:,sx//2]-128;c=np.maximum(y-16,0)*(255/219);chroma=255/224;kr=.299;kb=.114;kg=1-kr-kb
            last=np.stack([c+(2-2*kr)*vv*chroma,c-2*kb*(1-kb)/kg*u*chroma-2*kr*(1-kr)/kg*vv*chroma,c+(2-2*kb)*u*chroma]).clip(0,255)/127.5-1
        assert last is not None
        pcm=read_exact(audio.stdout,(8240-len(left))*4);x=np.concatenate([left,np.frombuffer(pcm,'<f4')]);count=min(50,math.ceil(len(x)/160));x=np.pad(x,(0,8240-len(x)));left=x[8000:]
        frames=np.lib.stride_tricks.sliding_window_view(x,400)[::160][:50]*hann;power=(abs(np.fft.rfft(frames))**2).astype('float32');mel=np.log10(power@fb.T+1e-10)
        ts.append(e*500);valid.append(count);vs.append(last.reshape(-1));aa.append(mel.reshape(-1))
        if len(ts)==a.batch_epochs or e==n-1:
            batch=pa.record_batch([pa.array(ts,pa.int64()),pa.array(valid,pa.int64()),pa.array(ts,pa.int64()),pa.FixedSizeListArray.from_arrays(pa.array(np.concatenate(vs)),3*res*res),pa.FixedSizeListArray.from_arrays(pa.array(np.concatenate(aa)),3200)],schema=schema);writer.write_batch(batch);vs=[];aa=[];ts=[];valid=[]
# Drain only possible AAC packet padding, then require clean child exits.
audio.stdout.read();video.stdout.read();assert audio.wait()==0 and video.wait()==0
print(json.dumps({'epochs':n,'artifact_bytes':pathlib.Path(a.output).stat().st_size}))
