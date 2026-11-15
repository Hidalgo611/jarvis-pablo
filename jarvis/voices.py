"""Catálogo de voces descargables (gratis, sin límite)."""
from __future__ import annotations

from .config import MODELS

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"

# id -> (descripción hablada, archivo, nº de hablantes)
CATALOG: dict[str, dict] = {
    "es_ES-davefx-medium": {"desc": "masculina, castellano, grave y clara (la predeterminada)", "speakers": 1},
    "es_ES-sharvard-medium": {"desc": "castellano, tiene una voz masculina (0) y otra femenina (1)", "speakers": 2},
    "es_ES-carlfm-x_low": {"desc": "masculina, castellano, sencilla y muy rápida", "speakers": 1},
    "es_MX-ald-medium": {"desc": "masculina, acento mexicano", "speakers": 1},
    "es_MX-claude-high": {"desc": "masculina, acento mexicano, alta calidad", "speakers": 1},
    "es_AR-daniela-high": {"desc": "femenina, acento argentino, alta calidad", "speakers": 1},
}

MORE_INFO = ("Muestras para escuchar: rhasspy.github.io/piper-samples. "
             "Catálogo completo: github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models")


def voice_dir(voice: str):
    return MODELS / f"vits-piper-{voice}"


def installed(voice: str) -> bool:
    return (voice_dir(voice) / f"{voice}.onnx").exists()


def url(voice: str) -> str:
    return f"{BASE}vits-piper-{voice}.tar.bz2"


def describe() -> str:
    lines = []
    for vid, info in CATALOG.items():
        mark = " (instalada)" if installed(vid) else ""
        lines.append(f"- {vid}: {info['desc']}{mark}")
    return "\n".join(lines) + "\n" + MORE_INFO
