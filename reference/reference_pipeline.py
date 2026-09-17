#!/usr/bin/env python3
"""Executable correctness oracle for TenzorPipe.

This is intentionally NOT the production engine. It uses the system FFmpeg to independently
decode media so we can validate expected tensor shapes, sampling, non-zero video, and audio
features even before the Rust toolchain is available in a build environment.
"""
from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
import numpy as np

SR=16000; NFFT=400; HOP=160; NMELS=64

def run(cmd):
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

def duration(path):
    raw=run(["ffprobe","-v","error","-show_entries","format=duration","-of","json",str(path)])
    return float(json.loads(raw)["format"]["duration"])

def audio16k(path):
    raw=run(["ffmpeg","-v","error","-i",str(path),"-map","0:a:0?","-ac","1","-ar",str(SR),"-f","f32le","pipe:1"])
    return np.frombuffer(raw,dtype="<f4").copy()

def frames(path,res,fps):
    raw=run(["ffmpeg","-v","error","-i",str(path),"-map","0:v:0","-vf",f"fps={fps},scale={res}:{res}:flags=bilinear","-pix_fmt","rgb24","-f","rawvideo","pipe:1"])
    a=np.frombuffer(raw,dtype=np.uint8)
    one=res*res*3
    n=len(a)//one
    a=a[:n*one].reshape(n,res,res,3).astype(np.float32)
    return (a/127.5-1.0).transpose(0,3,1,2)

def hz2mel(x): return 2595*np.log10(1+x/700)
def mel2hz(x): return 700*(10**(x/2595)-1)
def melbank():
    half=NFFT//2+1
    m=np.linspace(hz2mel(0),hz2mel(SR/2),NMELS+2)
    bins=(NFFT+1)*mel2hz(m)/SR
    f=np.zeros((NMELS,half),np.float32)
    for j in range(NMELS):
        l,c,r=bins[j:j+3]
        for k in range(half):
            if l<k<c: f[j,k]=(k-l)/(c-l)
            elif c<=k<r: f[j,k]=(r-k)/(r-c)
    return f

def logmel(x):
    if len(x)<NFFT: return np.zeros((0,NMELS),np.float32)
    n=1+(len(x)-NFFT)//HOP
    win=np.hanning(NFFT).astype(np.float32)
    fb=melbank()
    out=np.empty((n,NMELS),np.float32)
    for i in range(n):
        z=x[i*HOP:i*HOP+NFFT]*win
        p=np.abs(np.fft.rfft(z))**2
        out[i]=np.log10(fb@p+1e-10)
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("input"); ap.add_argument("output")
    ap.add_argument("--window",type=float,default=.5); ap.add_argument("--resolution",type=int,default=224)
    a=ap.parse_args(); p=Path(a.input); d=duration(p); epochs=max(1,int(np.ceil(d/a.window)))
    aud=audio16k(p); mel=logmel(aud); fpe=round(a.window*SR/HOP)
    vid=frames(p,a.resolution,1/a.window) if p.suffix.lower() in {'.mp4','.mov','.m4v'} else None
    aa=np.zeros((epochs,fpe,NMELS),np.float32)
    for e in range(epochs):
        part=mel[e*fpe:(e+1)*fpe]; aa[e,:len(part)]=part
    payload={"timestamps_ms":np.arange(epochs,dtype=np.int64)*round(a.window*1000),"audio":aa}
    if vid is not None:
        if len(vid)<epochs:
            vid=np.concatenate([vid,np.repeat(vid[-1:],epochs-len(vid),axis=0)],axis=0)
        payload["video"]=vid[:epochs]
    np.savez(a.output,**payload)
    print(json.dumps({"epochs":epochs,"audio_shape":list(aa.shape),"video_shape":None if vid is None else list(payload['video'].shape),"audio_nonzero":bool(np.any(aa)),"video_nonzero":None if vid is None else bool(np.any(np.abs(payload['video'])>.01))},indent=2))
if __name__=="__main__": main()
