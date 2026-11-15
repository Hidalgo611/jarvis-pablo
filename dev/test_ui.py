import sys, time, threading, os; sys.path.insert(0,".")
from PySide6.QtWidgets import QApplication
app=QApplication([])
from jarvis.ui.overlay import Overlay
from jarvis.bus import bus
from PIL import ImageGrab
o=Overlay()
def shot(n): ImageGrab.grab().resize((1280,720)).save(f"dev/ui_{n}.png")
def seq():
    try:
        bus.intro.emit("start"); 
        for l in ["Inicializando núcleo J.A.R.V.I.S.", "Módulo de voz ........ OK", "Red neuronal local .... OK"]:
            bus.intro.emit("line:"+l); time.sleep(0.6)
        bus.intro.emit("title"); time.sleep(2.5); bus.subtitle.emit("Hola, Pablo. Soy Jarvis, tu asistente."); time.sleep(0.5); shot("intro")
        bus.mode.emit("focus"); bus.state.emit("listening")
        for i in range(60): bus.level.emit(0.2+0.5*abs(((i%10)-5)/5)); time.sleep(0.03)
        shot("focus2")
        bus.state.emit("thinking"); time.sleep(0.8); shot("think")
        bus.mode.emit("corner"); bus.state.emit("acting"); bus.plan.emit(["Buscar noticias","Leer fuentes","Redactar","Guardar en Word"])
        bus.step.emit(0,"done"); bus.step.emit(1,"active"); bus.status.emit("Leyendo marca.com"); time.sleep(1.5); shot("corner2")
        bus.mode.emit("hidden"); time.sleep(0.8)
    finally:
        os._exit(0)
threading.Thread(target=seq,daemon=True).start()
app.exec()
