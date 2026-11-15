import sys, glob, os, numpy as np, soundfile as sf
sys.path.insert(0,".")
from jarvis.audio import Recognizer
R=Recognizer()
def f0(x,sr):
    fr=int(sr*0.04); vals=[]
    for i in range(0,len(x)-fr,fr):
        s=x[i:i+fr]; 
        if np.sqrt(np.mean(s**2))<0.02: continue
        c=np.correlate(s,s,"full")[fr-1:]; lo,hi=int(sr/300),int(sr/70)
        k=lo+np.argmax(c[lo:hi]); 
        if c[k]>0.3*c[0]: vals.append(sr/k)
    return np.median(vals) if vals else 0
for f in sorted(glob.glob(sys.argv[1]+"/*.wav")):
    x,sr=sf.read(f,dtype="float32")
    if x.ndim>1: x=x.mean(1)
    y=np.interp(np.arange(0,len(x),sr/16000),np.arange(len(x)),x)
    print(os.path.basename(f), "F0=%3.0fHz"%f0(x,sr), "|", R((y*32767).astype(np.int16)))
