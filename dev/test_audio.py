import sys, time, os, threading, numpy as np; sys.path.insert(0,".")
import sherpa_onnx as so
from jarvis.audio import AudioEngine, SR
from jarvis.wakeword import CHUNK
from jarvis.config import MODELS
d=MODELS/"vits-piper-es_MX-ald-medium"
T=so.OfflineTts(so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(vits=so.OfflineTtsVitsModelConfig(model=str(d/"es_MX-ald-medium.onnx"),tokens=str(d/"tokens.txt"),data_dir=str(d/"espeak-ng-data")),num_threads=4)))
def speech(text):
    a=T.generate(text,sid=0,speed=1.0); s=np.array(a.samples,np.float32)
    x=np.interp(np.arange(0,len(s),a.sample_rate/SR),np.arange(len(s)),s)
    return (np.clip(x,-1,1)*14000).astype(np.int16)
def sil(sec): return (np.random.randn(int(SR*sec))*60).astype(np.int16)
E=AudioEngine(); E._open_stream=lambda: None
threading.Thread(target=E._loop,daemon=True).start()
t0=time.time()
def feed(x, realtime=True):
    for i in range(0,len(x)-CHUNK+1,CHUNK):
        E.q.put(x[i:i+CHUNK]);
        if realtime: time.sleep(CHUNK/SR)
ev=[]
E.on_wake=lambda s,r: ev.append(("WAKE",s,r,round(time.time()-t0,2)))
print("--- 1) palabra clave en modo espera")
feed(np.concatenate([sil(1), speech("Oye Jarvis, abre Spotify y pon algo de música."), sil(1.5)])); time.sleep(2)
print(ev)
print("--- 2) escucha con parciales")
done=threading.Event()
E.listen(8, on_partial=lambda t: print(f"  [{time.time()-t0:5.1f}] parcial: {t}"),
         on_final=lambda t: (print(f"  [{time.time()-t0:5.1f}] FINAL: {t}"), done.set()),
         on_timeout=lambda: (print("timeout"), done.set()))
long="Quiero que busques en internet qué ha pasado hoy con el Real Madrid, que me hagas un resumen en un documento de Word, y que después me pongas el resumen del partido en YouTube."
feed(np.concatenate([sil(0.6), speech(long), sil(2)])); done.wait(10)
print(f"fin de audio a {time.time()-t0:.1f}s")
