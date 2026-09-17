#!/usr/bin/env python3
"""Independent FFmpeg decode + NumPy DSP oracle; assertions are release gates."""
import sys, subprocess, pathlib, json, math
import numpy as np
import pyarrow as pa, pyarrow.ipc as ipc
from PIL import Image,ImageDraw
ROOT=pathlib.Path(__file__).resolve().parents[1]
def run(args): return subprocess.check_output(list(map(str,args)),stderr=subprocess.PIPE)
def probe(p): return json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',p]))
def read(p):
    r=ipc.open_file(pa.memory_map(str(p)));md={k.decode():v.decode() for k,v in r.schema.metadata.items()}
    cols={k:[] for k in r.schema.names}
    for b in range(r.num_record_batches):
        rb=r.get_batch(b)
        for k in cols:
            c=rb.column(k);cols[k].append(c.values.to_numpy(zero_copy_only=True).reshape(rb.num_rows,-1) if pa.types.is_fixed_size_list(c.type) else c.to_numpy(zero_copy_only=True))
    return {k:np.concatenate(v) for k,v in cols.items()},md,r.num_record_batches

def visual(p,data,md):
    info=probe(p);v=next(s for s in info['streams'] if s['codec_type']=='video');w,h=v['width'],v['height'];res=int(md['video_shape'].split(',')[1]);
    fs=json.loads(run(['ffprobe','-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=best_effort_timestamp_time,pict_type','-of','json',p]))['frames']
    pts=np.array([float(f['best_effort_timestamp_time']) for f in fs]);targets=data['timestamp_ms']/1000
    indices=[int(np.argmin(abs(pts-t))) for t in targets];unique=sorted(set(indices))
    assert np.array_equal(data['video_timestamp_ms'],np.floor(pts[indices]*1000+.5).astype('int64'))
    expression='+'.join(f'eq(n,{i})' for i in unique)
    raw=run(['ffmpeg','-v','error','-i',p,'-an','-vf',f"select='{expression}'",'-vsync','0','-pix_fmt',v['pix_fmt'],'-f','rawvideo','-threads','1','pipe:1'])
    count=w*h*3//2;assert len(raw)==count*len(unique)
    refs={};sy=np.arange(res)*(h-1)//(res-1);sx=np.arange(res)*(w-1)//(res-1)
    for j,idx in enumerate(unique):
        f=np.frombuffer(raw[j*count:(j+1)*count],np.uint8).astype(np.float32)
        y=f[:w*h].reshape(h,w)[sy][:,sx];u=f[w*h:w*h*5//4].reshape(h//2,w//2)[sy//2][:,sx//2]-128;vv=f[w*h*5//4:].reshape(h//2,w//2)[sy//2][:,sx//2]-128;c=np.maximum(y-16,0)
        full='full' in md['video_matrix']; bt709='709' in md['video_matrix']
        c=y if full else np.maximum(y-16,0)*(255/219);chroma=1 if full else 255/224
        kr,kb=(.2126,.0722) if bt709 else (.299,.114);kg=1-kr-kb
        refs[idx]=np.stack([c+(2-2*kr)*vv*chroma,c-2*kb*(1-kb)/kg*u*chroma-2*kr*(1-kr)/kg*vv*chroma,c+(2-2*kb)*u*chroma]).clip(0,255)/127.5-1
    actual=data['video_tensor'].reshape(-1,3,res,res);errors=[];tiles=[]
    for e,idx in enumerate(indices):
        ref=refs[idx];delta=abs(actual[e]-ref)*127.5;mae=float(delta.mean());errors.append({'epoch':e,'source_frame':idx,'type':fs[idx]['pict_type'],'mae_255':mae,'p99_255':float(np.quantile(delta,.99))})
        assert mae<3.,f'{p.name} epoch {e} ({fs[idx]["pict_type"]}): MAE {mae}'
        if e in [0,len(indices)//2,len(indices)-1]:
            a=((np.concatenate([ref,actual[e]],axis=2).transpose(1,2,0)+1)*127.5).clip(0,255).astype('uint8');tiles.append(a)
    out=ROOT/'evidence'/f'{p.stem}-{res}-visual.png';Image.fromarray(np.concatenate(tiles)).save(out)
    return errors

def sinc_oracle(x,rate):
    if rate==16000:return x
    n=len(x)*16000//rate;out=np.zeros(n,np.float32);cutoff=min(1,16000/rate)*.95
    for start in range(0,n,4096):
        pos=np.arange(start,min(n,start+4096))*rate/16000;center=np.floor(pos).astype(int);frac=np.minimum(np.round((pos-center)*1024),1023)/1024
        tap=np.arange(-64,65);z=tap[None,:]-frac[:,None];norm=(z+64)/128
        window=np.where((norm>=0)&(norm<=1),.42-.5*np.cos(2*np.pi*norm)+.08*np.cos(4*np.pi*norm),0)
        weights=np.sinc(z*cutoff)*window*cutoff;weights/=weights.sum(axis=1)[:,None]
        idx=center[:,None]+tap;values=x[np.clip(idx,0,len(x)-1)];values=np.where((idx>=0)&(idx<len(x)),values,0)
        out[start:start+len(pos)]=(values*weights).sum(axis=1)
    return out

def mel_oracle(x,nframes):
    points=700*(10**(np.linspace(0,2595*np.log10(1+8000/700),66)/2595)-1)*400/16000
    k=np.arange(201)[None,:];left,center,right=[points[i:i+64,None] for i in range(3)]
    bank=np.maximum(0,np.minimum((k-left)/(center-left),(right-k)/(right-center)))
    x=np.pad(x,(0,max(0,(nframes-1)*160+400-len(x))))
    frames=np.lib.stride_tricks.sliding_window_view(x,400)[::160][:nframes]*np.hanning(400)
    power=abs(np.fft.rfft(frames))**2
    return np.log10(power@bank.T+1e-10)

def audio(p,data,md):
    s=next(s for s in probe(p)['streams'] if s['codec_type']=='audio');rate=int(s['sample_rate']);ch=s['channels']
    raw=run(['ffmpeg','-v','error','-i',p,'-vn','-c:a','pcm_f32le','-f','f32le','pipe:1']);pcm=np.frombuffer(raw,'<f4').reshape(-1,ch).mean(axis=1)
    # Container edit duration excludes AAC padding; decode output can retain its final packet tail.
    if p.suffix=='.mp4':pcm=pcm[:round(float(s['duration'])*rate)]
    x=sinc_oracle(pcm,rate);actual=data['audio_mel_tensor'].reshape(-1,64);expected=mel_oracle(x,len(actual));valid=int(data['audio_valid_frames'].sum());assert valid==math.ceil(len(x)/160),(valid,len(x))
    # Compare energetic bins: near-silent log bins amplify tiny decoder roundoff.
    mask=expected[:valid]>-5;err=abs(actual[:valid]-expected[:valid]);mae=float(err[mask].mean());p99=float(np.quantile(err[mask],.99));assert mae<.08 and p99<.5,(p.name,mae,p99)
    assert np.all(np.isfinite(actual));assert np.any(actual>-5)
    assert np.max(abs(actual[valid:]+10),initial=0)<1e-5
    return {'rate':rate,'channels':ch,'valid_hops':valid,'active_logmel_mae':mae,'active_logmel_p99':p99,'maximum_logmel_error':float(err.max())}

def verify(p,out,res,batch):
    data,md,batches=read(out);n=len(data['timestamp_ms']);assert n>0;assert np.array_equal(data['timestamp_ms'],np.arange(n)*int(md['window_ms']));assert batches==math.ceil(n/batch)
    result={'file':p.name,'resolution':res,'epochs':n,'record_batches':batches,'artifact_bytes':out.stat().st_size}
    if 'video_tensor' in data:assert md['video_shape']==f'3,{res},{res}';result['visual']=visual(p,data,md)
    else:assert 'video_shape' not in md
    if md['has_audio']=='true':result['audio']=audio(p,data,md)
    else:assert np.all(data['audio_valid_frames']==0)
    return result
if __name__=='__main__':
    p=pathlib.Path(sys.argv[1]);out=pathlib.Path(sys.argv[2]);print(json.dumps(verify(p,out,int(sys.argv[3]) if len(sys.argv)>3 else 224,2),indent=2))
