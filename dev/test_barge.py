import sys, time, threading, numpy as np; sys.path.insert(0,".")
from jarvis.audio import AudioEngine, SR
from jarvis.wakeword import CHUNK
from jarvis.tts import Speaker
from jarvis.app import is_user_speech
from jarvis import voices
sp=Speaker()
def speech(text, voice):
    sp.voice_id=voice; x=sp.synth(text); sr=sp.sr
    y=np.interp(np.arange(0,len(x),sr/SR),np.arange(len(x)),x); return (np.clip(y,-1,1)*12000).astype(np.int16)
def sil(s): return (np.random.randn(int(SR*s))*60).astype(np.int16)
E=AudioEngine(); E._open_stream=lambda: None; threading.Thread(target=E._loop,daemon=True).start()
jarvis_says="Claro, Pablo. He encontrado tres noticias sobre el Real Madrid para ayudarte a preparar el resumen del partido de esta noche."
events=[]
E.on_barge=lambda t: (lambda r: (events.append((t,r)), r)[1])(is_user_speech(t,[jarvis_says]))
E.on_wake=lambda s,r: events.append(("WAKE",s))
def feed(x):
    for i in range(0,len(x)-CHUNK+1,CHUNK): E.q.put(x[i:i+CHUNK]); time.sleep(CHUNK/SR)
E.tts_active=True
print("--- eco: su propia voz por el micro"); feed(np.concatenate([sil(0.5),speech(jarvis_says,"jarvis-clasico"),sil(1)])); time.sleep(1.5)
for e in events: print("  ",e)
events.clear(); E._last_wake=0
print("--- usuario dice 'espera, para un momento'"); feed(np.concatenate([sil(0.5),speech("Espera, espera, para un momento.","viernes-1"),sil(1)])); time.sleep(1.5)
for e in events: print("  ",e)
events.clear(); E._last_wake=0; E.mode="wake"; E.tts_active=True
print("--- usuario pide otra cosa"); feed(np.concatenate([sil(0.5),speech("Mejor pon música de los ochenta en Spotify.","jarvis-joven"),sil(1)])); time.sleep(1.5)
for e in events: print("  ",e)
