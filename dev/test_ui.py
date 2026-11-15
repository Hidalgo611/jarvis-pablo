import sys, time, threading; sys.path.insert(0,".")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
app=QApplication([])
from jarvis.ui.overlay import Overlay
from jarvis.bus import bus
from PIL import ImageGrab
o=Overlay()
shots=[]
def seq():
    bus.mode.emit("focus"); bus.state.emit("listening")
    for i in range(30): bus.level.emit(0.3+0.3*((i%6)/6)); time.sleep(0.05)
    ImageGrab.grab().resize((1280,720)).save("dev/ui_focus.png")
    bus.subtitle.emit("Buenas tardes, señor. ¿En qué puedo ayudarle?"); bus.state.emit("speaking"); time.sleep(0.8)
    ImageGrab.grab().resize((1280,720)).save("dev/ui_speak.png")
    bus.mode.emit("corner"); bus.state.emit("acting")
    bus.plan.emit(["Buscar noticias del Real Madrid","Leer las fuentes","Redactar el informe","Guardar en Word"])
    bus.step.emit(0,"done"); bus.step.emit(1,"active"); bus.status.emit("Leyendo marca.com")
    bus.subtitle.emit("Enseguida, señor. Estoy leyendo las fuentes.")
    bus.mouse.emit(True); time.sleep(1.4)
    ImageGrab.grab().resize((1280,720)).save("dev/ui_corner.png")
    bus.mode.emit("hidden"); time.sleep(1); app.quit()
threading.Thread(target=seq,daemon=True).start()
app.exec()
