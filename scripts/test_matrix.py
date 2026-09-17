#!/usr/bin/env python3
import pathlib,subprocess,json,sys,os,tempfile,argparse
p=argparse.ArgumentParser();p.add_argument("--video-workers",type=int,choices=[1,2,4,8],default=1);args=p.parse_args()
EXTRA=["--video-workers",str(args.video_workers)]
import numpy as np
from verify_media import verify,read,probe
ROOT=pathlib.Path(__file__).resolve().parents[1];BASE=pathlib.Path(os.environ.get('TENZOR_OUTPUT_ROOT',ROOT/'out'));BASE.mkdir(parents=True,exist_ok=True);OUT=pathlib.Path(tempfile.mkdtemp(prefix='matrix-',dir=BASE))
(ROOT/'evidence'/'matrix-output.json').write_text(json.dumps({'directory':str(OUT)},indent=2))
BIN=ROOT/'target'/'release'/'tenzor';FIX=ROOT/'fixtures';results=[]
for name,res in [('baseline720.mp4',224),('high-bframes.mp4',160),('high-bframes.mp4',224),('high-bframes.mp4',336),('short.mp4',224),('silent-video.mp4',224),('wav-44100-2ch.wav',224),('wav-48000-2ch.wav',224),('wav-16000-1ch.wav',224),('wav-48000-6ch.wav',224),('audio-only.mp4',224),('color709.mp4',224),('fullrange.mp4',224),('sync-pulse.mp4',224),('aac-6ch.mp4',224),('tiny.wav',224)]:
    p=FIX/name;out=OUT/f'{p.stem}-{res}.tenzor';out.unlink(missing_ok=True)
    print('VERIFY',name,res,flush=True)
    proc=subprocess.run([str(BIN),*EXTRA,'-i',str(p),'-o',str(out),'--resolution',str(res),'--batch-epochs','2'],capture_output=True,text=True);assert proc.returncode==0,proc.stderr
    results.append(verify(p,out,res,2))
    (ROOT/'evidence'/'matrix.json').write_text(json.dumps(results,indent=2))
data,md,_=read(OUT/'sync-pulse-224.tenzor')
assert np.argmax(data['video_tensor'].mean(axis=1))==2
mel=data['audio_mel_tensor'].reshape(-1,64)
peak=int(np.argmax(mel.max(axis=1)));assert 98<=peak<=111,peak
(ROOT/'evidence'/'synchronization.json').write_text(json.dumps({'flash_epoch_ms':1000,'audio_peak_hop':peak,'audio_peak_ms':peak*10},indent=2))
# Non-default epoch duration must stay on the 10ms hop grid.
out=OUT/'window330.tenzor';out.unlink(missing_ok=True)
subprocess.run([str(BIN),*EXTRA,'-i',str(FIX/'wav-44100-2ch.wav'),'-o',str(out),'--window-sec','.33','--batch-epochs','2'],check=True,capture_output=True)
data,md,_=read(out);assert md['audio_shape']=='33,64';assert np.array_equal(data['timestamp_ms'],[0,330,660,990]);out.unlink()
# PyTorch is independently loaded only after numerical checks (not in benchmark timings).
sys.path.insert(0,str(ROOT/'python'));from tenzor import TenzorDataset
import torch
for p in OUT.glob('*.tenzor'):
    d=TenzorDataset(str(p));n=0
    for i,b in enumerate(d.iter_batches()):
        rb=d.reader.get_batch(i);raw=rb.column('audio_mel_tensor').values.to_numpy(zero_copy_only=True)
        assert b['audio'].data_ptr()==raw.__array_interface__['data'][0]
        assert torch.isfinite(b['audio']).all();n+=len(b['timestamp_ms'])
    assert n==len(d);assert d[-1]['timestamp_ms'].item()==(n-1)*500
    assert d[0]['audio'].shape==(50,64)
    c=TenzorDataset(str(p),copy=True);assert c[0]['audio'].data_ptr()!=d[0]['audio'].data_ptr();c.close();d.close()
failures=[]
for name,extra in [('empty.mp4',[]),('corrupt.mp4',[]),('corrupt-nal.mp4',[]),('unsupported-mpeg4.mp4',[]),('unsupported.mp3',[]),('high-bframes.mp4',['--resolution','0']),('high-bframes.mp4',['--resolution','1025']),('high-bframes.mp4',['--window-sec','0']),('high-bframes.mp4',['--window-sec','nan']),('high-bframes.mp4',['--window-sec','.123']),('high-bframes.mp4',['--batch-epochs','0']),('high-bframes.mp4',['--resolution','1024','--batch-epochs','1024'])]:
    out=OUT/'failure.tenzor';out.unlink(missing_ok=True)
    r=subprocess.run([str(BIN),*EXTRA,'-i',str(FIX/name),'-o',str(out),*extra],capture_output=True,text=True,timeout=30)
    assert r.returncode>0 and not out.exists(),(name,r.returncode,r.stderr)
    assert 'panicked' not in r.stderr
    failures.append({'file':name,'args':extra,'exit_code':r.returncode,'error':r.stderr.strip()})
# Do not destroy an existing artifact, even when decoding fails.
out=OUT/'existing.tenzor';out.write_bytes(b'preserve me')
r=subprocess.run([str(BIN),*EXTRA,'-i',str(FIX/'corrupt.mp4'),'-o',str(out)],capture_output=True)
assert r.returncode!=0 and out.read_bytes()==b'preserve me';out.unlink()
(ROOT/'evidence'/'failures.json').write_text(json.dumps(failures,indent=2))
(ROOT/'evidence'/'python-loader.json').write_text(json.dumps({'files_checked':len(list(OUT.glob('*.tenzor'))),'zero_copy_pointer_checks':True,'all_batches':True,'negative_index':True,'copy_mode':True},indent=2))
print('PASS',len(results),'media cases,',len(failures),'failure cases, PyTorch checks')
import hashlib,datetime
(ROOT/'evidence'/'verification-summary.json').write_text(json.dumps({'binary_sha256':hashlib.sha256(BIN.read_bytes()).hexdigest(),'passed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'media_cases':len(results),'failure_cases':len(failures),'pytorch_all_batch_checks':True,'extra_window_ms':330},indent=2))
