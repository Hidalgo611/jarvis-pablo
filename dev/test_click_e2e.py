import sys, time, threading, os; sys.path.insert(0,".")
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QGridLayout, QLabel
from PySide6.QtCore import Qt
app=QApplication([])
w=QWidget(); w.setWindowTitle("Panel de pruebas Jarvis"); w.setWindowFlags(Qt.WindowStaysOnTopHint); w.setStyleSheet("font-size:22px")
g=QGridLayout(w); clicked=[]
lab=QLabel("Pulsado: -"); g.addWidget(lab,0,0,1,3)
for i,(t,c) in enumerate([("Aceptar","#2a7"),("Cancelar","#c33"),("Descargar informe","#27c"),("Ajustes","#777"),("Enviar mensaje","#a5c"),("Salir","#333")]):
    b=QPushButton(t); b.setStyleSheet(f"background:{c};color:white;padding:22px"); g.addWidget(b,1+i//3,i%3)
    b.clicked.connect(lambda _=False,t=t:(clicked.append(t), lab.setText("Pulsado: "+t)))
w.resize(900,360); w.move(500,300); w.show(); w.raise_()
from jarvis import tools; tools.load_all()
from jarvis.tools.vision import screen_click, locate
def run():
    time.sleep(1.5)
    for t in ["el botón Descargar informe","el botón rojo Cancelar","el botón Enviar mensaje","el botón Comprar ahora"]:
        t0=time.time(); r=screen_click(t); print(f"{t:28s} -> {r[:70]}  ({time.time()-t0:.1f}s) registrados={clicked}", flush=True)
        time.sleep(0.6)
    os._exit(0)
threading.Thread(target=run,daemon=True).start()
app.exec()
