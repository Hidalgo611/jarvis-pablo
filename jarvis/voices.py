"""Catálogo de voces (Supertonic 3: 31 idiomas, natural, local, gratis sin límite)."""
from __future__ import annotations

from .config import MODELS

SUPERTONIC_DIR = MODELS / "sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
SUPERTONIC_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
                  "sherpa-onnx-supertonic-3-tts-int8-2026-05-11.tar.bz2")

# id -> sid dentro del modelo + descripción (tono medido en Hz)
CATALOG: dict[str, dict] = {
    "jarvis-grave": {"sid": 9, "desc": "masculina muy grave y pausada (88 Hz)"},
    "jarvis-clasico": {"sid": 6, "desc": "masculina grave, elegante (98 Hz) — predeterminada"},
    "jarvis-medio": {"sid": 7, "desc": "masculina de tono medio (109 Hz)"},
    "jarvis-claro": {"sid": 8, "desc": "masculina clara (123 Hz)"},
    "jarvis-joven": {"sid": 5, "desc": "masculina más joven (130 Hz)"},
    "viernes-1": {"sid": 2, "desc": "femenina cálida (173 Hz), estilo «Viernes»"},
    "viernes-2": {"sid": 4, "desc": "femenina serena (173 Hz)"},
    "viernes-3": {"sid": 0, "desc": "femenina (194 Hz)"},
    "viernes-4": {"sid": 3, "desc": "femenina (199 Hz)"},
    "viernes-5": {"sid": 1, "desc": "femenina aguda (209 Hz)"},
}
DEFAULT = "jarvis-clasico"
MORE_INFO = "Demo para escuchar todas las voces: huggingface.co/spaces/Supertone/supertonic-3"


def installed(voice: str = DEFAULT) -> bool:
    return (SUPERTONIC_DIR / "vocoder.int8.onnx").exists()


def sid(voice: str) -> int:
    return CATALOG.get(voice, CATALOG[DEFAULT])["sid"]


def describe() -> str:
    return "\n".join(f"- {vid}: {info['desc']}" for vid, info in CATALOG.items()) + "\n" + MORE_INFO
