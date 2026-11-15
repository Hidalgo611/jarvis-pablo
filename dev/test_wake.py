import os, sys, numpy as np, sherpa_onnx as so
sys.path.insert(0, ".")
from pathlib import Path
from jarvis.wakeword import WakeWord
M=Path(os.path.expandvars(r"%LOCALAPPDATA%\Jarvis\models"))
def tts(v):
    d=M/f"vits-piper-{v}"
    return so.OfflineTts(so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(vits=so.OfflineTtsVitsModelConfig(model=str(d/f"{v}.onnx"),tokens=str(d/"tokens.txt"),data_dir=str(d/"espeak-ng-data")),num_threads=4)))
def to16(a):
    s=np.array(a.samples,dtype=np.float32); x=np.interp(np.arange(0,len(s),a.sample_rate/16000),np.arange(len(s)),s)
    return (np.clip(x,-1,1)*20000).astype(np.int16)
W=WakeWord(M/"wakeword")
import openwakeword
ref=openwakeword.Model(wakeword_models=[str(M/"wakeword/hey_jarvis_v0.1.onnx")],inference_framework="onnx",melspec_model_path=str(M/"wakeword/melspectrogram.onnx"),embedding_model_path=str(M/"wakeword/embedding_model.onnx"))
for v in ["es_ES-davefx-medium","es_MX-ald-medium"]:
    T=tts(v)
    for text in ["Hey Jarvis.","Jei yarvis.","Hola, ¿qué tal estás hoy?","Abre el navegador, por favor."]:
        x=to16(T.generate(text,sid=0,speed=1.0)); x=np.concatenate([np.zeros(16000,np.int16),x,np.zeros(16000,np.int16)])
        W.reset(); ref.reset(); mine=[]; theirs=[]
        for i in range(0,len(x)-1280,1280):
            c=x[i:i+1280]; mine.append(W.process(c)); theirs.append(list(ref.predict(c).values())[0])
        print(v,repr(text),"mine max %.3f"%max(mine),"ref max %.3f"%max(theirs))
