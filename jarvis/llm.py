"""Conexión con el núcleo de IA (Ollama): local, en el servidor de casa (otro PC) o el de respaldo.

- `ollama_url`: servidor principal. Puede ser el propio portátil (127.0.0.1) o el PC-servidor por Tailscale
  (p. ej. http://pc-marcos:11500, que es el «Jarvis Server» de la carpeta server/).
- `server_key`: clave compartida con el Jarvis Server (cabecera X-Jarvis-Key). Vacía = sin clave.
- `fallback_url`: si el principal no responde (PC apagado), se usa este (p. ej. un Ollama local con un modelo
  pequeño). Vacío = sin respaldo.
"""
from __future__ import annotations

import logging
import threading
import time

import requests

from .config import settings

log = logging.getLogger("llm")

_session = requests.Session()
_state = {"down_until": 0.0, "lock": threading.Lock()}


class ServerBusy(RuntimeError):
    """El servidor responde pero no puede atender ahora (p. ej. modo juego en pausa)."""


def headers() -> dict:
    k = (settings.get("server_key") or "").strip()
    return {"X-Jarvis-Key": k} if k else {}


def base_url() -> str:
    """URL a usar ahora: la principal, o la de respaldo si la principal ha fallado hace poco."""
    fb = (settings.get("fallback_url") or "").strip()
    if fb and time.time() < _state["down_until"]:
        return fb
    return settings["ollama_url"]


def _mark_down() -> None:
    if (settings.get("fallback_url") or "").strip():
        _state["down_until"] = time.time() + 120
        log.warning("servidor de IA principal sin respuesta; uso el de respaldo 2 min")


def _for_fallback(payload: dict) -> dict:
    fm = (settings.get("fallback_model") or "").strip()
    if fm and "model" in payload:
        payload = dict(payload, model=fm)
    return payload


def post(path: str, payload: dict, stream: bool = False, timeout=(5, 180)) -> requests.Response:
    """POST al núcleo con reintento automático en el servidor de respaldo."""
    url = base_url()
    fb = (settings.get("fallback_url") or "").strip()
    if fb and url == fb:
        payload = _for_fallback(payload)
    try:
        r = _session.post(url + path, json=payload, stream=stream, timeout=timeout, headers=headers())
    except (requests.ConnectionError, requests.Timeout):
        if not fb or url == fb:
            raise
        _mark_down()
        r = _session.post(fb + path, json=_for_fallback(payload), stream=stream, timeout=timeout, headers=headers())
    if r.status_code == 503:
        try:
            msg = r.json().get("error", "")
        except Exception:
            msg = ""
        r.close()
        raise ServerBusy(msg or "El servidor de IA está ocupado.")
    return r


def is_remote(url: str | None = None) -> bool:
    u = url or settings["ollama_url"]
    return not ("127.0.0.1" in u or "localhost" in u)
