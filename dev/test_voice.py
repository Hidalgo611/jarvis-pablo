import time, os, numpy as np, sherpa_onnx as so
M=os.path.expandvars(r"%LOCALAPPDATA%\Jarvis\models")
def tts(v):
    d=os.path.join(M,f"vits-piper-{v}")
    cfg=so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(vits=so.OfflineTtsVitsModelConfig(model=os.path.join(d,f"{v}.onnx"),tokens=os.path.join(d,"tokens.txt"),data_dir=os.path.join(d,"espeak-ng-data")),num_threads=4))
    return so.OfflineTts(cfg)
t=time.time(); T=tts("es_ES-davefx-medium"); print("tts load %.2f"%(time.time()-t))
text="Buenas tardes, señor. He encontrado tres noticias sobre la economía europea y he preparado un resumen en Word."
t=time.time(); a=T.generate(text,sid=0,speed=1.0); print("tts gen %.2fs for %.2fs audio, sr=%d"%(time.time()-t,len(a.samples)/a.sample_rate,a.sample_rate))
d=os.path.join(M,"sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8")
t=time.time()
R=so.OfflineRecognizer.from_transducer(encoder=d+"/encoder.int8.onnx",decoder=d+"/decoder.int8.onnx",joiner=d+"/joiner.int8.onnx",tokens=d+"/tokens.txt",model_type="nemo_transducer",num_threads=4)
print("asr load %.2f"%(time.time()-t))
s=np.array(a.samples,dtype=np.float32)
# resample to 16k
import math
x=np.interp(np.arange(0,len(s),a.sample_rate/16000),np.arange(len(s)),s).astype(np.float32)
for i in range(2):
    t=time.time(); st=R.create_stream(); st.accept_waveform(16000,x); R.decode_stream(st); print("asr %.2fs:"%(time.time()-t), st.result.text)
