import sys,pathlib,json,wave
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.research-deps'))
from faster_whisper import WhisperModel
import numpy as np
model=WhisperModel('base.en',device='cpu',compute_type='int8',cpu_threads=4,download_root=str(ROOT/'.research-deps'/'models'))
for path in (ROOT/'research'/'videos').glob('*.wav'):
 out=path.with_suffix('.transcript.json')
 if out.exists():continue
 if path.stat().st_size<100:continue
 with wave.open(str(path),'rb') as wav:
  samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype=np.int16).astype(np.float32)/32768.0
 segments,info=model.transcribe(samples,beam_size=3,vad_filter=True)
 data=[{'start':round(s.start,2),'end':round(s.end,2),'text':s.text.strip()} for s in segments]
 out.write_text(json.dumps({'engine':'faster-whisper base.en; machine transcript; verify uncertain terms','segments':data},ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'video':path.stem,'segments':len(data),'file':str(out)}),flush=True)
