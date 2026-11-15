"""Señales Qt para que los hilos de trabajo hablen con la interfaz sin riesgos."""
from PySide6.QtCore import QObject, Signal


class Bus(QObject):
    state = Signal(str)            # idle | listening | thinking | acting | speaking | setup | error
    mode = Signal(str)             # hidden | focus | corner
    status = Signal(str)           # texto corto: "Buscando en internet…"
    plan = Signal(list)            # lista de pasos
    step = Signal(int, str)        # índice, estado: active | done | error
    subtitle = Signal(str)         # lo que dice Jarvis
    transcript = Signal(str)       # lo que dice el usuario (si se muestra)
    level = Signal(float)          # 0..1 nivel de audio para animar el núcleo
    progress = Signal(float, str)  # 0..1 o -1, texto (instalación/descargas)
    mouse = Signal(bool)           # Jarvis controla el ratón
    mouse_target = Signal(int, int)  # posición física a la que va el ratón
    intro = Signal(str)            # presentación: start | line:<texto> | title | end
    wake = Signal(str)             # origen de la activación: voice | hotkey | tray
    quit = Signal()


bus = Bus()
