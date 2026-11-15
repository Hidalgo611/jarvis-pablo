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
        self._listen_cb = None
        self._stream = None
        self._last_wake = 0.0
        self._partial_busy = False
        self.paused = False

    # ---------- API ----------
    def listen(self, timeout_s: float, on_partial, on_final, on_timeout) -> None:
        self._listen_cb = (time.time() + timeout_s, on_partial, on_final, on_timeout)
        self._reset_utt()
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
        score = self.wake.process(chunk)
        thr = float(settings["wake_threshold"]) + (0.3 if self.tts_active else 0.0)
        if score >= thr:
            self._fire_wake("voice")
            return
        if self.tts_active or not settings["keyword_wake"]:
            self.kw_buf = []
            return
        # Vía 2: buscar "Jarvis" en lo reconocido (sirve con acento español y "Jarvis, abre…")
        if speech:
            self.kw_buf.append(chunk)
            self.kw_sil = 0
            if len(self.kw_buf) > 12 * SR // CHUNK:  # frase demasiado larga: no es una llamada
                self.kw_buf = self.kw_buf[-int(12 * SR / CHUNK):]
        elif self.kw_buf:
            self.kw_buf.append(chunk)
            self.kw_sil += 80
            if self.kw_sil >= 400:
                pcm = np.concatenate(self.kw_buf)
                self.kw_buf = []
                if 0.35 * SR <= pcm.shape[0] <= 12 * SR:
                    self.asr_q.put(("kw", pcm))

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
                elif kind == "kw" and self.mode == "wake":
                    text = self.asr(job[1])
                    m = WAKE_RE.search(text)
                    if m and len(text[:m.start()].split()) <= 3:
                        rest = text[m.end():].strip(" ,.;:¡!¿?")
                        self._fire_wake("keyword", rest)
            except Exception:
                log.exception("error ASR")
            finally:
                if kind == "partial":
                    self._partial_busy = False
