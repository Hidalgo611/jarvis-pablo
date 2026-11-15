"""Rutas, versión y ajustes de Jarvis."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "Jarvis"
VERSION = "0.1.3"
# Repositorio público de GitHub donde se publican las versiones ("usuario/repo").
# Vacío = actualizaciones desactivadas.
UPDATE_REPO = "Hidalgo611/jarvis-pablo"

FROZEN = getattr(sys, "frozen", False)
EXE_PATH = Path(sys.executable) if FROZEN else None

DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
MODELS = DATA / "models"
SKILLS = DATA / "skills"
LOGS = DATA / "logs"
BROWSER_PROFILE = DATA / "browser"
DB_PATH = DATA / "memory.db"
SETTINGS_PATH = DATA / "settings.json"
PENDING_UPDATE = DATA / "pending_update.json"

DOCS_OUT = Path.home() / "Documents" / "Jarvis"
SHOTS_OUT = Path.home() / "Pictures" / "Jarvis"

for _d in (DATA, MODELS, SKILLS, LOGS, DOCS_OUT, SHOTS_OUT):
    _d.mkdir(parents=True, exist_ok=True)

DEFAULTS: dict = {
    "user_name": "Pablo",
    "address": "señor",            # cómo le llama Jarvis
    "assistant_name": "Jarvis",
    "ollama_url": "http://127.0.0.1:11434",
    "model": "qwen3:8b",           # cerebro principal (herramientas)
    "vision_model": "qwen3.5:4b",  # para "mira mi pantalla"
    "num_ctx": 8192,
    "voice": "es_ES-davefx-medium",
    "voice_speaker": 0,
    "voice_speed": 1.0,
    "voice_fx": True,
    "volume": 1.0,
    "wake_threshold": 0.5,
    "end_silence_ms": 800,
    "follow_up_seconds": 7,
    "first_listen_seconds": 8,
    "hotkey": "ctrl+alt+j",
    "dim_opacity": 0.9,
    "keyword_wake": True,          # detectar "Jarvis" también con el reconocedor (mejor con acento español)
    "corner": "top-right",
    "show_subtitles": True,
    "show_transcript": False,
    "autostart": True,
    "greet_on_start": True,
    "learning": True,
    "mic_device": None,
    "browser": "auto",             # auto | chrome | msedge
}


class Settings:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        if SETTINGS_PATH.exists():
            try:
                self.data.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
            except Exception:
                logging.exception("settings.json corrupto; uso valores por defecto")
        self.save()

    def __getitem__(self, k):
        return self.data.get(k, DEFAULTS.get(k))

    def get(self, k, default=None):
        return self.data.get(k, default)

    def set(self, k, v) -> None:
        with self._lock:
            self.data[k] = v
        self.save()

    def save(self) -> None:
        with self._lock:
            tmp = SETTINGS_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, SETTINGS_PATH)


settings = Settings()


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOGS / "jarvis.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if not FROZEN:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
