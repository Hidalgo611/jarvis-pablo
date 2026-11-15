"""Primer arranque: descarga de modelos, instalación/arranque de Ollama y del modelo de lenguaje."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import requests

from . import voices
from .bus import bus
from .config import MODELS, settings

log = logging.getLogger("installer")
CREATE_NO_WINDOW = 0x08000000
SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"

ASSETS = [
    # (comprobación, url, destino, tipo, etiqueta)
    (MODELS / "silero_vad.onnx", SHERPA + "asr-models/silero_vad.onnx", MODELS / "silero_vad.onnx", "file", "detector de voz"),
    (MODELS / "wakeword/melspectrogram.onnx", OWW + "melspectrogram.onnx", MODELS / "wakeword/melspectrogram.onnx", "file", "activación (1/3)"),
    (MODELS / "wakeword/embedding_model.onnx", OWW + "embedding_model.onnx", MODELS / "wakeword/embedding_model.onnx", "file", "activación (2/3)"),
    (MODELS / "wakeword/hey_jarvis_v0.1.onnx", OWW + "hey_jarvis_v0.1.onnx", MODELS / "wakeword/hey_jarvis_v0.1.onnx", "file", "activación (3/3)"),
    (MODELS / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/encoder.int8.onnx",
     SHERPA + "asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2", MODELS, "tar", "módulo de oído"),
]


def _download(url: str, dest: Path, label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with requests.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        t0 = time.time()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if time.time() - t0 > 0.25:
                    t0 = time.time()
                    bus.progress.emit(done / total if total else -1,
                                      f"Descargando {label}  {done / 2**20:.0f} / {total / 2**20:.0f} MB" if total else f"Descargando {label}")
    os.replace(tmp, dest)


def _fetch(url: str, dest: Path, kind: str, label: str) -> None:
    if kind == "file":
        _download(url, dest, label)
        return
    with tempfile.TemporaryDirectory(dir=MODELS) as td:
        arc = Path(td) / "pkg.tar.bz2"
        _download(url, arc, label)
        bus.progress.emit(-1, f"Instalando {label}…")
        with tarfile.open(arc, "r:bz2") as t:
            t.extractall(dest, filter="data")


def download_voice(vid: str) -> bool:
    try:
        _fetch(voices.url(vid), MODELS, "tar", f"voz {vid}")
        return voices.installed(vid)
    except Exception:
        log.exception("voz %s", vid)
        return False


def models_ready() -> bool:
    return all(chk.exists() for chk, *_ in ASSETS) and voices.installed(settings["voice"])


def ensure_models() -> None:
    for chk, url, dest, kind, label in ASSETS:
        if not chk.exists():
            _fetch(url, dest, kind, label)
    if not voices.installed(settings["voice"]):
        if not download_voice(settings["voice"]):
            raise RuntimeError("no se pudo descargar la voz")


# ------------------------------------------------------------- Ollama
def ollama_up(url: str | None = None) -> bool:
    try:
        return requests.get((url or settings["ollama_url"]) + "/api/version", timeout=2).ok
    except Exception:
        return False


def _ollama_exe() -> Path | None:
    for p in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe",
              Path(os.environ.get("PROGRAMFILES", "")) / "Ollama/ollama.exe"):
        if p.exists():
            return p
    w = shutil.which("ollama")
    return Path(w) if w else None


def _start_ollama(exe: Path) -> None:
    env = dict(os.environ)
    env.setdefault("OLLAMA_KEEP_ALIVE", "-1")
    subprocess.Popen([str(exe), "serve"], env=env, creationflags=CREATE_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_ollama() -> None:
    url = settings["ollama_url"]
    if ollama_up(url):
        return
    if "127.0.0.1" not in url and "localhost" not in url:
        raise RuntimeError(f"El servidor de IA remoto ({url}) no responde.")
    exe = _ollama_exe()
    if not exe:
        inst = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
        _download("https://ollama.com/download/OllamaSetup.exe", inst, "motor de IA (Ollama)")
        bus.progress.emit(-1, "Instalando el motor de IA…")
        subprocess.run([str(inst), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"], timeout=900)
        exe = _ollama_exe()
        if not exe:
            raise RuntimeError("No se pudo instalar Ollama.")
    bus.progress.emit(-1, "Arrancando el motor de IA…")
    if not ollama_up(url):
        _start_ollama(exe)
    for _ in range(60):
        if ollama_up(url):
            return
        time.sleep(1)
    raise RuntimeError("Ollama no arranca.")


def _vram_mb() -> int:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return 0


def pick_model() -> str:
    v = _vram_mb()
    if v >= 7500:
        return "qwen3:8b"
    if v >= 5000:
        return "qwen3.5:4b"
    return "qwen3.5:4b"


def installed_models() -> set[str]:
    try:
        r = requests.get(settings["ollama_url"] + "/api/tags", timeout=5).json()
        return {m["name"] for m in r.get("models", [])}
    except Exception:
        return set()


def pull(model: str, label: str) -> None:
    with requests.post(settings["ollama_url"] + "/api/pull", json={"model": model, "stream": True}, stream=True,
                       timeout=(10, 600)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            d = json.loads(line)
            if d.get("error"):
                raise RuntimeError(d["error"])
            if d.get("total"):
                bus.progress.emit(d.get("completed", 0) / d["total"],
                                  f"Descargando {label}  {d.get('completed', 0) / 2**30:.1f} / {d['total'] / 2**30:.1f} GB")


def ensure_llm() -> None:
    have = installed_models()
    if not settings.get("model_chosen"):
        settings.set("model", pick_model())
        settings.set("model_chosen", True)
    m = settings["model"]
    if m not in have and f"{m}:latest" not in have:
        pull(m, "cerebro (modelo de lenguaje)")


def ensure_vision_background() -> None:
    """El módulo de visión se descarga después, sin bloquear el arranque."""
    vm = settings["vision_model"]
    if vm and vm not in installed_models():
        try:
            with requests.post(settings["ollama_url"] + "/api/pull", json={"model": vm, "stream": False}, timeout=None):
                pass
        except Exception:
            log.exception("visión no instalada")


def run_setup() -> None:
    bus.progress.emit(-1, "Iniciando sistemas…")
    ensure_models()
    ensure_ollama()
    ensure_llm()
    bus.progress.emit(-1, "Calentando el núcleo…")
    try:
        requests.post(settings["ollama_url"] + "/api/generate",
                      json={"model": settings["model"], "prompt": "", "keep_alive": -1}, timeout=120)
    except Exception:
        pass
    bus.progress.emit(1.0, "Sistemas en línea")
