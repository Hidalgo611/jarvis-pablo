"""Ventana de ajustes de Jarvis."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from .. import voices
from ..config import VERSION, settings

STYLE = """
QDialog { background: #050d16; }
QLabel { color: #9fe6ff; font: 10pt 'Segoe UI'; }
QLabel#title { color: #40d2ff; font: 600 16pt 'Segoe UI'; letter-spacing: 6px; }
QLabel#sub { color: #4f7f95; font: 9pt 'Segoe UI'; }
QLineEdit, QComboBox { background: #0a1a28; color: #e6f8ff; border: 1px solid #1d4a63; border-radius: 6px; padding: 5px 8px;
                       font: 10pt 'Segoe UI'; }
QComboBox QAbstractItemView { background: #0a1a28; color: #e6f8ff; selection-background-color: #12506e; }
QCheckBox { color: #c9f1ff; font: 10pt 'Segoe UI'; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #40d2ff; border-radius: 3px; background: #0a1a28; }
QCheckBox::indicator:checked { background: #40d2ff; }
QPushButton { background: #0c2a3d; color: #bff0ff; border: 1px solid #40d2ff; border-radius: 6px; padding: 6px 14px;
              font: 600 10pt 'Segoe UI'; }
QPushButton:hover { background: #12506e; }
QPushButton#primary { background: #40d2ff; color: #031018; }
QSlider::groove:horizontal { height: 4px; background: #1d4a63; border-radius: 2px; }
QSlider::handle:horizontal { background: #40d2ff; width: 14px; margin: -6px 0; border-radius: 7px; }
"""


class SettingsWindow(QDialog):
    def __init__(self, on_test_voice: Callable[[str], None], on_saved: Callable[[], None],
                 on_replay_intro: Callable[[], None]) -> None:
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setWindowTitle("J.A.R.V.I.S. — Ajustes")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(520)
        self.on_test_voice, self.on_saved, self.on_replay_intro = on_test_voice, on_saved, on_replay_intro

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 22)
        t = QLabel("J.A.R.V.I.S.")
        t.setObjectName("title")
        s = QLabel(f"Ajustes · versión {VERSION} · todo funciona en local")
        s.setObjectName("sub")
        root.addWidget(t)
        root.addWidget(s)
        root.addSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setVerticalSpacing(10)
        self.name = QLineEdit(settings["user_name"])
        self.address = QLineEdit(settings["address"])
        form.addRow("Tu nombre", self.name)
        form.addRow("Cómo te llama", self.address)

        self.voice = QComboBox()
        for vid, info in voices.CATALOG.items():
            self.voice.addItem(f"{vid} — {info['desc']}", vid)
        self.voice.setCurrentIndex(max(0, self.voice.findData(settings["voice"])))
        test = QPushButton("Probar")
        test.clicked.connect(self._test)
        row = QHBoxLayout()
        row.addWidget(self.voice, 1)
        row.addWidget(test)
        w = QWidget()
        w.setLayout(row)
        form.addRow("Voz", w)

        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(75, 130)
        self.speed.setValue(int(float(settings["voice_speed"]) * 100))
        form.addRow("Velocidad", self.speed)
        self.fx = QComboBox()
        for k, label in (("sutil", "Sutil (natural, recomendado)"), ("robot", "IA marcada (más robótica)"), ("off", "Sin efecto")):
            self.fx.addItem(label, k)
        cur = settings["voice_fx"]
        cur = "sutil" if cur is True else ("off" if cur is False else cur)
        self.fx.setCurrentIndex(max(0, self.fx.findData(cur)))
        form.addRow("Efecto de voz", self.fx)

        self.sens = QSlider(Qt.Horizontal)
        self.sens.setRange(20, 80)
        self.sens.setValue(int((1 - float(settings["wake_threshold"])) * 100))
        form.addRow("Sensibilidad «Hey Jarvis»", self.sens)

        self.mic = QComboBox()
        self.mic.addItem("Predeterminado de Windows", None)
        try:
            import sounddevice as sd
            seen = set()
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and d["name"] not in seen and d["hostapi"] == 0:
                    seen.add(d["name"])
                    self.mic.addItem(d["name"], i)
        except Exception:
            pass
        self.mic.setCurrentIndex(max(0, self.mic.findData(settings["mic_device"])))
        form.addRow("Micrófono", self.mic)
        self.server = QLineEdit(settings["ollama_url"])
        self.server.setPlaceholderText("http://127.0.0.1:11434  o  http://pc-servidor:11500")
        self.server.setToolTip("Dónde está el cerebro de Jarvis: en este PC (127.0.0.1:11434) o en el PC-servidor "
                               "(su nombre de Tailscale y el puerto del Jarvis Server, 11500).")
        form.addRow("Servidor de IA", self.server)
        self.key = QLineEdit(settings["server_key"])
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("La que muestra el Jarvis Server al instalarlo")
        form.addRow("Clave del servidor", self.key)
        root.addLayout(form)
        root.addSpacing(6)

        self.checks = {}
        for key, label in (("keyword_wake", "Entender «Jarvis» dentro de frases («Oye Jarvis, abre…»)"),
                           ("duck_media", "Bajar el volumen de otras apps mientras te escucho"),
                           ("auto_update", "Instalar actualizaciones automáticamente (te aviso)"),
                           ("autostart", "Arrancar con Windows"),
                           ("show_subtitles", "Mostrar subtítulos de lo que digo"),
                           ("greet_on_start", "Saludar al encender"),
                           ("learning", "Aprender de mis costumbres para proponer rutinas")):
            cb = QCheckBox(label)
            cb.setChecked(bool(settings[key]))
            self.checks[key] = cb
            root.addWidget(cb)

        root.addSpacing(12)
        btns = QHBoxLayout()
        replay = QPushButton("Ver presentación")
        replay.clicked.connect(self._replay)
        save = QPushButton("Guardar")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        btns.addWidget(replay)
        btns.addStretch(1)
        btns.addWidget(save)
        root.addLayout(btns)

    def _test(self) -> None:
        settings.set("voice_speed", self.speed.value() / 100)
        settings.set("voice_fx", self.fx.currentData())
        self.on_test_voice(self.voice.currentData())

    def _replay(self) -> None:
        self._save()
        self.on_replay_intro()

    def _save(self) -> None:
        settings.set("user_name", self.name.text().strip() or "Pablo")
        settings.set("address", self.address.text().strip() or settings["user_name"])
        settings.set("voice", self.voice.currentData())
        settings.set("voice_speed", self.speed.value() / 100)
        settings.set("voice_fx", self.fx.currentData())
        settings.set("wake_threshold", round(1 - self.sens.value() / 100, 2))
        settings.set("mic_device", self.mic.currentData())
        url = self.server.text().strip().rstrip("/") or "http://127.0.0.1:11434"
        if not url.startswith("http"):
            url = "http://" + url
        settings.set("ollama_url", url)
        settings.set("server_key", self.key.text().strip())
        try:
            from ..memory import memory
            memory.set_profile("nombre", settings["user_name"])
        except Exception:
            pass
        for k, cb in self.checks.items():
            settings.set(k, cb.isChecked())
        self.on_saved()
        self.close()
