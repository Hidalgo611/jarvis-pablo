"""Voz de Jarvis: síntesis local (Piper vía sherpa-onnx), filtro "IA" y reproducción por frases."""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import sherpa_onnx as so

from . import voices
from .bus import bus
from .config import settings

log = logging.getLogger("tts")

_SENT_RE = re.compile(r"(?<=[\.\!\?…;:])\s+|\n+")


_U = ["cero", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce", "trece",
      "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidós",
      "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve"]
_D = {3: "treinta", 4: "cuarenta", 5: "cincuenta"}


def _num(n: int) -> str:
    if n < 30:
        return _U[n]
    d, u = divmod(n, 10)
    return _D[d] + ("" if u == 0 else " y " + ("un" if u == 1 else _U[u]))


def _hora(m: re.Match) -> str:
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return m.group(0)
    h12 = h % 12 or 12
    franja = "del mediodía" if h == 12 else "de la mañana" if 6 <= h < 12 else ("de la tarde" if 13 <= h < 21 else "de la noche")
    if mi == 0:
        txt = f"{_num(h12)} en punto"
    elif mi == 15:
        txt = f"{_num(h12)} y cuarto"
    elif mi == 30:
        txt = f"{_num(h12)} y media"
    else:
        txt = f"{_num(h12)} y {_num(mi)}"
    return txt + " " + franja


def clean_for_speech(text: str) -> str:
    text = re.sub(r"\b[Ee]s las\b", "Son las", text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _hora, text)
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"https?://\S+", "el enlace", t)
    t = re.sub(r"[*_#`>|~]+", " ", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    t = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", t)
    t = t.replace("J.A.R.V.I.S.", "Jarvis").replace("JARVIS", "Jarvis")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_RE.split(text) if p and p.strip()]
    out: list[str] = []
    for p in parts:  # agrupa fragmentos muy cortos
        if out and len(out[-1]) < 25:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


def _room_ir(sr: int, seconds: float, decay: float) -> np.ndarray:
    n = int(sr * seconds)
    rng = np.random.default_rng(7)
    ir = rng.standard_normal(n).astype(np.float32) * np.exp(-np.linspace(0, decay, n)).astype(np.float32)
    return ir / np.abs(ir).sum()


def jarvis_fx(x: np.ndarray, sr: int, mode: str = "sutil") -> np.ndarray:
    """Timbre de IA. «sutil»: presencia + sala pequeña (natural). «robot»: el efecto antiguo, más metálico."""
    x = x.astype(np.float32)
    n = x.shape[0]
    if mode in ("off", False, "no"):
        return x
    y = x.copy()
    if mode == "robot":
        for ms, g in ((9.0, 0.22), (15.0, 0.14)):
            d = int(sr * ms / 1000)
            if d < n:
                y[d:] += g * x[:-d]
    hp = np.empty_like(y)
    hp[0] = y[0]
    hp[1:] = y[1:] - 0.9 * y[:-1]
    y = y + (0.18 if mode == "robot" else 0.08) * hp          # presencia / brillo
    wet = np.convolve(y, _room_ir(sr, 0.22, 8))[:n]
    y = y + (0.35 if mode == "robot" else 0.14) * wet         # sala de la «mansión»
    peak = float(np.abs(y).max() or 1.0)
    return (y / peak * 0.92).astype(np.float32)


def chime(sr: int = 22050) -> np.ndarray:
    """Dos notas suaves ascendentes (sonido de activación)."""
    out = []
    for f, dur in ((880.0, 0.11), (1318.5, 0.22)):
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        env = np.minimum(1, t / 0.008) * np.exp(-t * 14)
        out.append((0.22 * np.sin(2 * np.pi * f * t) + 0.06 * np.sin(4 * np.pi * f * t)) * env)
    return np.concatenate(out).astype(np.float32)


class Speaker:
    def __init__(self) -> None:
        self.tts: Optional[so.OfflineTts] = None
        self.sr = 22050
        self.voice_id = ""
        self.text_q: "queue.Queue" = queue.Queue()
        self.audio_q: "queue.Queue" = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._gen = 0
        self._pending = 0
        self._lock = threading.Lock()
        self.on_active: Optional[Callable[[bool], None]] = None
        self.load_voice(settings["voice"])
        threading.Thread(target=self._synth_loop, name="tts-synth", daemon=True).start()
        threading.Thread(target=self._play_loop, name="tts-play", daemon=True).start()

    def load_voice(self, voice: str) -> bool:
        d = voices.SUPERTONIC_DIR
        if not voices.installed():
            log.error("motor de voz no instalado")
            return False
        if self.tts is None:
            cfg = so.OfflineTtsSupertonicModelConfig(
                duration_predictor=str(d / "duration_predictor.int8.onnx"), text_encoder=str(d / "text_encoder.int8.onnx"),
                vector_estimator=str(d / "vector_estimator.int8.onnx"), vocoder=str(d / "vocoder.int8.onnx"),
                tts_json=str(d / "tts.json"), unicode_indexer=str(d / "unicode_indexer.bin"), voice_style=str(d / "voice.bin"))
            self.tts = so.OfflineTts(so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(supertonic=cfg, num_threads=4)))
            self.sr = self.tts.sample_rate
        if voice not in voices.CATALOG:
            voice = voices.DEFAULT
        self.voice_id = voice
        log.info("voz cargada: %s (sid %d, %d Hz)", voice, voices.sid(voice), self.sr)
        return True

    def synth(self, text: str) -> np.ndarray:
        g = so.GenerationConfig()
        g.sid = voices.sid(self.voice_id)
        g.speed = float(settings["voice_speed"])
        g.num_steps = 8
        g.extra = {"lang": "es"}
        a = self.tts.generate(text, g)
        return np.asarray(a.samples, dtype=np.float32)

    # ---------- API ----------
    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        for s in split_sentences(text):
            with self._lock:
                self._pending += 1
            self.text_q.put((self._gen, s))

    def play_chime(self) -> None:
        with self._lock:
            self._pending += 1
        self.audio_q.put((self._gen, chime(self.sr), ""))

    def stop(self) -> None:
        self._gen += 1
        self._stop.set()
        for q in (self.text_q, self.audio_q):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        with self._lock:
            self._pending = 0
        self._set_active(False)

    def busy(self) -> bool:
        return self._pending > 0

    def wait(self, timeout: float = 120) -> None:
        t0 = time.time()
        while self.busy() and time.time() - t0 < timeout:
            time.sleep(0.05)

    # ---------- interno ----------
    def _done_one(self) -> None:
        with self._lock:
            self._pending = max(0, self._pending - 1)

    def _set_active(self, v: bool) -> None:
        if self.on_active:
            try:
                self.on_active(v)
            except Exception:
                pass

    def _synth_loop(self) -> None:
        while True:
            gen, text = self.text_q.get()
            if gen != self._gen:
                self._done_one()
                continue
            try:
                x = self.synth(text)
                fx = settings["voice_fx"]
                x = jarvis_fx(x, self.sr, "sutil" if fx is True else ("off" if fx is False else str(fx)))
                x = np.concatenate([x, np.zeros(int(self.sr * 0.12), np.float32)])
                self.audio_q.put((gen, x * float(settings["volume"]), text))
            except Exception:
                log.exception("error sintetizando")
                self._done_one()

    def _play_loop(self) -> None:
        stream = None
        stream_sr = 0
        idle_since = time.time()
        while True:
            try:
                gen, x, text = self.audio_q.get(timeout=0.3)
            except queue.Empty:
                if stream is not None and time.time() - idle_since > 1.0:
                    stream.close()
                    stream = None
                    self._set_active(False)
                    bus.level.emit(0.0)
                continue
            if gen != self._gen:
                self._done_one()
                continue
            self._stop.clear()
            try:
                if stream is None or stream_sr != self.sr:
                    if stream is not None:
                        stream.close()
                    stream = sd.OutputStream(samplerate=self.sr, channels=1, dtype="float32", blocksize=1024)
                    stream.start()
                    stream_sr = self.sr
                self._set_active(True)
                if text:
                    bus.subtitle.emit(text)
                block = 1024
                for i in range(0, x.shape[0], block):
                    if self._stop.is_set() or gen != self._gen:
                        break
                    b = x[i:i + block]
                    bus.level.emit(min(1.0, float(np.sqrt(np.mean(b ** 2))) * 6))
                    stream.write(b.reshape(-1, 1))
            except Exception:
                log.exception("error reproduciendo")
                stream = None
            finally:
                self._done_one()
                idle_since = time.time()
