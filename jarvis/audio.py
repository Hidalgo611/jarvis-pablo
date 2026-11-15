"""Micrófono, activación, detección de voz y reconocimiento (con parciales)."""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import sherpa_onnx as so

from .bus import bus
from .config import MODELS, settings
from .wakeword import CHUNK, WakeWord

log = logging.getLogger("audio")
SR = 16000
PARAKEET = MODELS / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
WAKE_RE = re.compile(r"\b(jarvis|yarvis|jarvi|harvis|charvis|yarbis|jarbis|garvis|llarvis|jarbys|jarvys)\b", re.I)


class Recognizer:
    """Parakeet v3 (multilingüe, español incluido) en CPU."""

    def __init__(self) -> None:
        d = PARAKEET
        self.rec = so.OfflineRecognizer.from_transducer(
            encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"),
            joiner=str(d / "joiner.int8.onnx"), tokens=str(d / "tokens.txt"),
            model_type="nemo_transducer", num_threads=4)
        self.lock = threading.Lock()

    def __call__(self, pcm_int16: np.ndarray) -> str:
        x = pcm_int16.astype(np.float32) / 32768.0
        peak = float(np.abs(x).max() or 1.0)
        if peak < 0.3:  # micro bajo: normaliza un poco
            x = x * min(0.3 / peak, 8.0)
        with self.lock:
            st = self.rec.create_stream()
            st.accept_waveform(SR, x)
            self.rec.decode_stream(st)
            return st.result.text.strip()


class AudioEngine(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="audio", daemon=True)
        self.q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=400)
        self.wake = WakeWord(MODELS / "wakeword")
        vcfg = so.VadModelConfig()
        vcfg.silero_vad.model = str(MODELS / "silero_vad.onnx")
        vcfg.silero_vad.threshold = 0.5
        vcfg.silero_vad.min_silence_duration = 0.25
        vcfg.silero_vad.min_speech_duration = 0.2
        vcfg.silero_vad.window_size = 512
        vcfg.sample_rate = SR
        vcfg.num_threads = 1
        self.vad = so.VoiceActivityDetector(vcfg, buffer_size_in_seconds=60)
        self.asr = Recognizer()
        self.asr_q: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._asr_worker, name="asr", daemon=True).start()

        self.mode = "wake"           # wake | listen | off
        self.tts_active = False
        self.on_wake: Optional[Callable[[str, str], None]] = None   # (source, resto_del_comando)
        self.on_barge: Optional[Callable[[str], bool]] = None        # ¿lo oído mientras hablo es el usuario?
        self._listen_cb = None
        self._stream = None
        self._last_wake = 0.0
        self._partial_busy = False
        self._kw_busy = False
        self.ring: deque = deque(maxlen=40)   # últimos ~3,2 s (modo espera)
        self._kw_tick = 0
        self._last_speech = 0.0
        self._seg_max = 0.0
        self._seed: tuple | None = None      # audio de la llamada «Jarvis, …» para empezar la orden
        self.paused = False

    # ---------- API ----------
    def listen(self, timeout_s: float, on_partial, on_final, on_timeout) -> None:
        self._listen_cb = (time.time() + timeout_s, on_partial, on_final, on_timeout)
        self._reset_utt()
        seed, self._seed = self._seed, None
        if seed and time.time() - seed[0] < 2.5:
            # la orden empezó en la misma frase que «Jarvis»: se conserva ese audio
            self.utt = list(seed[1])
            self.started = True
        self.mode = "listen"

    def stop_listening(self) -> None:
        self.mode = "wake"
        self._listen_cb = None

    # ---------- interno ----------
    def _reset_utt(self) -> None:
        self.preroll: deque = deque(maxlen=10)  # ~800 ms antes de empezar a hablar
        self.utt: list = []
        self.started = False
        self.silence_ms = 0
        self.last_partial = 0.0
        self.kw_buf: list = []
        self.kw_sil = 0

    def _open_stream(self) -> None:
        dev = settings["mic_device"]

        def cb(indata, frames, t, status):
            try:
                self.q.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass

        self._stream = sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=CHUNK,
                                      device=dev, callback=cb)
        self._stream.start()
        log.info("micrófono abierto: %s", sd.query_devices(dev, "input")["name"] if dev is not None else "predeterminado")

    def run(self) -> None:
        while True:
            try:
                self._open_stream()
                self._loop()
            except Exception:
                log.exception("fallo de audio; reintento en 3 s")
                try:
                    if self._stream:
                        self._stream.close()
                except Exception:
                    pass
                time.sleep(3)

    def _loop(self) -> None:
        self._reset_utt()
        while True:
            chunk = self.q.get()
            if chunk.shape[0] != CHUNK or self.paused:
                continue
            self.vad.accept_waveform(chunk.astype(np.float32) / 32768.0)
            speech = self.vad.is_speech_detected()
            while not self.vad.empty():
                self.vad.pop()
            if self.mode == "wake":
                self._wake_step(chunk, speech)
            elif self.mode == "listen":
                self._listen_step(chunk, speech)

    def _fire_wake(self, source: str, rest: str = "") -> None:
        now = time.time()
        if now - self._last_wake < 1.5:
            return
        self._last_wake = now
        self.wake.reset()
        self.kw_buf = []
        log.info("activación por %s %r", source, rest)
        if self.on_wake:
            self.on_wake(source, rest)

    def _wake_step(self, chunk: np.ndarray, speech: bool) -> None:
        self.ring.append(chunk)
        score = self.wake.process(chunk)
        now = time.time()
        if speech:
            self._last_speech = now
            self._seg_max = max(self._seg_max, score)
        elif self._seg_max > 0 and now - self._last_speech > 0.6:
            if self._seg_max > 0.12:
                log.info("wake: puntuación máx. %.2f (umbral %.2f)", self._seg_max, float(settings["wake_threshold"]))
            self._seg_max = 0.0
        thr = float(settings["wake_threshold"]) + (0.3 if self.tts_active else 0.0)
        if score >= thr:
            self._fire_wake("voice")
            return
        if self.tts_active:
            # Interrumpir hablando: cada ~0,8 s con voz, reconoce los últimos 2 s y el conductor decide si es eco o el usuario
            self._kw_tick += 1
            if (settings["barge_in"] and now - self._last_speech < 0.4 and self._kw_tick >= 10 and not self._kw_busy
                    and len(self.ring) >= 10):
                self._kw_tick = 0
                self._kw_busy = True
                win = list(self.ring)[-25:]
                self.asr_q.put(("barge", np.concatenate(win), win))
            return
        if not settings["keyword_wake"]:
            return
        # Vía 2: cada ~1 s mientras hay voz, reconoce los últimos 2,5 s y busca «Jarvis».
        # Funciona aunque haya voz de fondo continua (vídeos, directos) y con acento español.
        self._kw_tick += 1
        if now - self._last_speech < 0.5 and self._kw_tick >= 12 and not self._kw_busy and len(self.ring) >= 8:
            self._kw_tick = 0
            self._kw_busy = True
            win = list(self.ring)[-31:]
            self.asr_q.put(("kw", np.concatenate(win), win))

    def _listen_step(self, chunk: np.ndarray, speech: bool) -> None:
        deadline, on_partial, on_final, on_timeout = self._listen_cb
        rms = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32768.0) ** 2)))
        bus.level.emit(min(1.0, rms * 12))
        if not self.started:
            self.preroll.append(chunk)
            if speech:
                self.started = True
                self.utt = list(self.preroll)
            elif time.time() > deadline:
                self.mode = "wake"
                on_timeout()
            return
        self.utt.append(chunk)
        self.silence_ms = 0 if speech else self.silence_ms + 80
        dur = len(self.utt) * CHUNK / SR
        end = self.silence_ms >= int(settings["end_silence_ms"]) or dur > 40
        if end:
            pcm = np.concatenate(self.utt)
            self.mode = "wake"
            self._reset_utt()
            self.asr_q.put(("final", pcm, on_final))
            return
        every = 1.2 if dur < 12 else 2.5
        if time.time() - self.last_partial > every and not self._partial_busy and dur > 1.0:
            self.last_partial = time.time()
            self._partial_busy = True
            self.asr_q.put(("partial", np.concatenate(self.utt), on_partial))

    def _asr_worker(self) -> None:
        while True:
            job = self.asr_q.get()
            kind = job[0]
            try:
                if kind == "partial":
                    # descarta parciales viejos si hay un final esperando
                    if any(j[0] == "final" for j in list(self.asr_q.queue)):
                        continue
                    text = self.asr(job[1])
                    if text:
                        job[2](text)
                elif kind == "final":
                    t0 = time.time()
                    text = self.asr(job[1])
                    log.info("ASR final %.2fs (%.1fs audio): %s", time.time() - t0, job[1].shape[0] / SR, text)
                    job[2](text)
                elif kind == "barge" and self.tts_active and self.mode == "wake":
                    text = self.asr(job[1])
                    if text and self.on_barge and self.on_barge(text):
                        log.info("interrupción: %r", text[:80])
                        self._seed = (time.time(), job[2])
                        self.ring.clear()
                        self._fire_wake("barge", "")
                elif kind == "kw" and self.mode == "wake":
                    text = self.asr(job[1])
                    if re.search(r"(j|y|h|ch|g|ll)ar|vis\b|bis\b", text, re.I):
                        log.info("wake kw: %r", text[:80])
                    m = WAKE_RE.search(text)
                    if m:
                        self._seed = (time.time(), job[2])
                        self.ring.clear()
                        self._fire_wake("keyword", "")
            except Exception:
                log.exception("error ASR")
            finally:
                if kind == "partial":
                    self._partial_busy = False
                elif kind in ("kw", "barge"):
                    self._kw_busy = False
