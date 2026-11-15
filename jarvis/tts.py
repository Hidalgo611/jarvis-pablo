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


def clean_for_speech(text: str) -> str:
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


def jarvis_fx(x: np.ndarray, sr: int) -> np.ndarray:
    """Timbre de IA sutil: desdoblamiento corto + brillo + pequeña sala."""
    x = x.astype(np.float32)
    n = x.shape[0]
    y = x.copy()
    for ms, g in ((9.0, 0.22), (15.0, 0.14)):
        d = int(sr * ms / 1000)
        if d < n:
            y[d:] += g * x[:-d]
    # brillo: realza agudos (pre-énfasis suave)
    hp = np.empty_like(y)
    hp[0] = y[0]
    hp[1:] = y[1:] - 0.85 * y[:-1]
    y = y + 0.18 * hp
    # sala pequeña
    ir_len = int(sr * 0.18)
    rng = np.random.default_rng(7)
    ir = rng.standard_normal(ir_len).astype(np.float32) * np.exp(-np.linspace(0, 7, ir_len)).astype(np.float32)
    ir /= np.abs(ir).sum()
    wet = np.convolve(y, ir)[:n]
    y = y + 0.35 * wet
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
        d = voices.voice_dir(voice)
        onnx = d / f"{voice}.onnx"
        if not onnx.exists():
            log.error("voz %s no instalada", voice)
            return False
        cfg = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(
            vits=so.OfflineTtsVitsModelConfig(model=str(onnx), tokens=str(d / "tokens.txt"),
                                              data_dir=str(d / "espeak-ng-data")),
            num_threads=3), max_num_sentences=1)
        tts = so.OfflineTts(cfg)
        self.tts, self.sr, self.voice_id = tts, tts.sample_rate, voice
        log.info("voz cargada: %s (%d Hz)", voice, self.sr)
        return True

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
                a = self.tts.generate(text, sid=int(settings["voice_speaker"]), speed=float(settings["voice_speed"]))
                x = np.asarray(a.samples, dtype=np.float32)
                if settings["voice_fx"]:
                    x = jarvis_fx(x, self.sr)
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
