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
VERSION = "0.4.0"
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
    "address": "Pablo",            # cómo le llama Jarvis
    "assistant_name": "Jarvis",
    "ollama_url": "http://127.0.0.1:11434",
    "model": "qwen3.5:4b",         # cerebro principal: herramientas + visión; cabe entero en 8 GB de VRAM
    "vision_model": "",            # vacío = el mismo modelo (si el cerebro no tiene visión, poner uno que sí)
    "num_ctx": 12288,
    "temperature": 0.5,            # más bajo = más centrado (un modelo pequeño se dispersa con temperaturas altas)
    "server_key": "",              # clave del Jarvis Server (si el cerebro está en otro PC)
    "fallback_url": "",            # núcleo de respaldo si el servidor no responde (p. ej. http://127.0.0.1:11434)
    "fallback_model": "",          # modelo del respaldo (vacío = el mismo)
    "voice": "jarvis-clasico",
    "voice_speed": 1.0,
    "voice_fx": "sutil",          # sutil | robot | off
    "volume": 1.0,
    "wake_threshold": 0.45,
    "end_silence_ms": 800,
    "follow_up_seconds": 7,
    "first_listen_seconds": 8,
    "hotkey": "ctrl+alt+j",
    "dim_opacity": 0.9,
    "keyword_wake": True,
    "notifications": True,         # avisar de WhatsApp / móvil (sin leer el contenido)
    "mail_accounts": [],           # se rellena solo al revisar el correo con sesión iniciada
    "calendar_account": "",
    "city": "",
    "barge_in": True,              # poder interrumpir a Jarvis hablando
    "screen_context": True,        # adjuntar la pantalla cuando la orden habla de «esto», «ahí», «clica»…
    "duck_media": True,            # bajar el volumen de otras apps mientras escucha
    "auto_update": True,           # instalar actualizaciones en cuanto se detectan (avisando)
    "update_check_hours": 1,          # detectar "Jarvis" también con el reconocedor (mejor con acento español)
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
        self._migrate()
        self.save()

    def _migrate(self) -> None:
        """Ajustes de versiones anteriores → actuales (sin perder lo que el usuario cambió)."""
        v = self.data.get("settings_version", 1)
        if v < 2:
            if not str(self.data.get("voice", "")).startswith(("jarvis-", "viernes-")):
                self.data["voice"] = DEFAULTS["voice"]
            if self.data.get("voice_fx") is True:
                self.data["voice_fx"] = "sutil"
            if self.data.get("address") == "señor":
                self.data["address"] = DEFAULTS["address"]
            if self.data.get("wake_threshold") == 0.5:
                self.data["wake_threshold"] = DEFAULTS["wake_threshold"]
        if v < 3:
            # 0.3.0: el cerebro pasa a qwen3.5 (con visión) si se había elegido automáticamente
            if self.data.get("model") == "qwen3:8b":
                self.data["model"] = DEFAULTS["model"]
            if self.data.get("vision_model") == "qwen3.5:4b":
                self.data["vision_model"] = ""
        if v < 4:
            # 0.3.1: el 9B no cabe entero en 8 GB (se iba en parte a la CPU) → 4B, más rápido y también con visión
            if self.data.get("model") in ("qwen3.5:9b", "qwen3:8b"):
                if self.data.get("model") == "qwen3.5:9b":
                    self.data["cleanup_models"] = ["qwen3.5:9b"]
                self.data["model"] = DEFAULTS["model"]
            if self.data.get("num_ctx") == 8192:
                self.data["num_ctx"] = DEFAULTS["num_ctx"]
        if v < 5:
            # 0.4.0: muestreo más centrado
            self.data.setdefault("temperature", DEFAULTS["temperature"])
        self.data["settings_version"] = 5

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
