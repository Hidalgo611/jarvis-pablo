"""Detector de "Hey Jarvis" (modelos de openWakeWord ejecutados con onnxruntime).

Implementación mínima propia del flujo de openWakeWord para no arrastrar scipy/sklearn
al .exe: audio int16 → melspectrograma → embeddings (96) → clasificador (16 x 96).
Trabaja con bloques exactos de 1280 muestras (80 ms a 16 kHz).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

CHUNK = 1280


class WakeWord:
    def __init__(self, folder: Path, model_name: str = "hey_jarvis_v0.1") -> None:
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        prov = ["CPUExecutionProvider"]
        self.mel = ort.InferenceSession(str(folder / "melspectrogram.onnx"), so, providers=prov)
        self.emb = ort.InferenceSession(str(folder / "embedding_model.onnx"), so, providers=prov)
        self.cls = ort.InferenceSession(str(folder / f"{model_name}.onnx"), so, providers=prov)
        self.cls_input = self.cls.get_inputs()[0].name
        self.reset()

    def _melspec(self, x: np.ndarray) -> np.ndarray:
        out = self.mel.run(None, {"input": x.astype(np.float32)[None, :]})[0]
        return np.squeeze(out) / 10 + 2

    def _embed(self, window: np.ndarray) -> np.ndarray:
        return self.emb.run(None, {"input_1": window.astype(np.float32)[None, :, :, None]})[0].reshape(-1, 96)

    def reset(self) -> None:
        self.raw = np.zeros(0, dtype=np.int16)
        self.melbuf = np.ones((76, 32), dtype=np.float32)
        noise = np.random.randint(-1000, 1000, 16000 * 4).astype(np.int16)
        spec = self._melspec(noise)
        wins = [spec[i:i + 76] for i in range(0, spec.shape[0], 8) if spec[i:i + 76].shape[0] == 76]
        self.feats = np.vstack([self._embed(w) for w in wins])
        self.warm = 5

    def process(self, chunk_int16: np.ndarray) -> float:
        """Recibe 1280 muestras int16 y devuelve la puntuación 0..1."""
        self.raw = np.concatenate((self.raw, chunk_int16))[-(CHUNK + 480):]
        if self.raw.shape[0] < CHUNK + 480:
            return 0.0
        self.melbuf = np.vstack((self.melbuf, self._melspec(self.raw)))[-970:]
        self.feats = np.vstack((self.feats, self._embed(self.melbuf[-76:])))[-120:]
        score = float(self.cls.run(None, {self.cls_input: self.feats[-16:][None, :, :].astype(np.float32)})[0].squeeze())
        if self.warm > 0:
            self.warm -= 1
            return 0.0
        return score
