"""Atenuar (duck) el audio de otras aplicaciones mientras Jarvis escucha o habla."""
from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("media")
_saved: dict[int, float] = {}
_lock = threading.Lock()


def duck(level: float = 0.15) -> None:
    """Baja el volumen de las demás aplicaciones (no el maestro) al `level` de lo que tenían."""
    try:
        import comtypes
        comtypes.CoInitialize()
        from pycaw.pycaw import AudioUtilities
        me = os.getpid()
        with _lock:
            for s in AudioUtilities.GetAllSessions():
                if not s.Process or s.Process.pid == me:
                    continue
                vol = s.SimpleAudioVolume
                key = s.ProcessId
                if key in _saved:
                    continue
                cur = vol.GetMasterVolume()
                _saved[key] = cur
                vol.SetMasterVolume(max(0.0, cur * level), None)
    except Exception:
        log.exception("duck")


def unduck() -> None:
    try:
        import comtypes
        comtypes.CoInitialize()
        from pycaw.pycaw import AudioUtilities
        with _lock:
            if not _saved:
                return
            for s in AudioUtilities.GetAllSessions():
                if s.ProcessId in _saved:
                    try:
                        s.SimpleAudioVolume.SetMasterVolume(_saved[s.ProcessId], None)
                    except Exception:
                        pass
            _saved.clear()
    except Exception:
        log.exception("unduck")
