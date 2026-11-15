"""Actualizaciones desde GitHub Releases sin reinstalar nada.

Flujo: check() → (el usuario confirma) → apply(): descarga Jarvis.new.exe, guarda las novedades en
pending_update.json, renombra el .exe en uso a Jarvis.old.exe (Windows lo permite), coloca el nuevo y
se relanza. Al arrancar, announce_if_updated() dice qué ha cambiado.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

from .bus import bus
from .config import EXE_PATH, FROZEN, PENDING_UPDATE, UPDATE_REPO, VERSION
from .memory import memory

log = logging.getLogger("updater")
MUTEX = None  # lo guarda app.single_instance(); se suelta antes de lanzar la versión nueva


def _ver(s: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3]) or (0,)


class Updater:
    def __init__(self, repo: str = UPDATE_REPO) -> None:
        self.repo = repo
        self.last_check = 0.0

    def enabled(self) -> bool:
        return bool(self.repo)

    def check(self, force: bool = False) -> dict | None:
        if not self.repo or (not force and time.time() - self.last_check < 6 * 3600):
            return None
        self.last_check = time.time()
        try:
            r = requests.get(f"https://api.github.com/repos/{self.repo}/releases/latest", timeout=10,
                             headers={"Accept": "application/vnd.github+json", "User-Agent": "Jarvis-Updater"})
            if r.status_code != 200:
                return None
            d = r.json()
            tag = d.get("tag_name", "")
            if _ver(tag) <= _ver(VERSION):
                return None
            asset = next((a for a in d.get("assets", []) if a["name"].lower().endswith(".exe")), None)
            if not asset:
                return None
            return {"version": tag.lstrip("v"), "notes": (d.get("body") or "").strip(),
                    "url": asset["browser_download_url"], "size": asset.get("size", 0)}
        except Exception:
            log.exception("check update")
            return None

    def apply(self, info: dict) -> bool:
        if not FROZEN or not EXE_PATH:
            log.warning("actualizar solo funciona en el .exe")
            return False
        exe = EXE_PATH
        new = exe.with_name("Jarvis.new.exe")
        old = exe.with_name("Jarvis.old.exe")
        try:
            with requests.get(info["url"], stream=True, timeout=30) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", info.get("size", 0)) or 0)
                done = 0
                with open(new, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            bus.progress.emit(done / total, f"Descargando actualización {info['version']}")
            PENDING_UPDATE.write_text(json.dumps({"version": info["version"], "notes": info["notes"],
                                                  "from": VERSION, "ts": time.time()}, ensure_ascii=False), encoding="utf-8")
            if old.exists():
                old.unlink()
            os.replace(exe, old)
            os.replace(new, exe)
            global MUTEX
            if MUTEX is not None:
                import win32api
                win32api.CloseHandle(MUTEX)
                MUTEX = None
            env = dict(os.environ)
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # que el .exe nuevo no herede la carpeta temporal del viejo
            subprocess.Popen([str(exe), "--after-update"], close_fds=True, env=env,
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            log.info("versión nueva lanzada; cerrando esta")
            bus.quit.emit()
            time.sleep(1.5)
            os._exit(0)
        except Exception:
            log.exception("apply update")
            bus.progress.emit(-1, "")
            if not exe.exists() and old.exists():
                os.replace(old, exe)
            return False


def cleanup_old() -> None:
    if FROZEN and EXE_PATH:
        old = EXE_PATH.with_name("Jarvis.old.exe")
        for _ in range(10):
            try:
                if old.exists():
                    old.unlink()
                break
            except PermissionError:
                time.sleep(0.5)


def announce_if_updated() -> str | None:
    """Devuelve el mensaje a decir si acabamos de actualizar (aunque haya habido reinicio)."""
    if not PENDING_UPDATE.exists():
        return None
    try:
        d = json.loads(PENDING_UPDATE.read_text(encoding="utf-8"))
    except Exception:
        PENDING_UPDATE.unlink(missing_ok=True)
        return None
    PENDING_UPDATE.unlink(missing_ok=True)
    if _ver(d.get("version", "0")) == _ver(VERSION):
        memory.event("updated", VERSION)
        notes = re.sub(r"[#*`>-]+", " ", d.get("notes", "")).strip()
        notes = re.sub(r"\s+", " ", notes)[:500]
        from .config import settings
        return (f"Ya estoy de vuelta, {settings['address']}. Me he actualizado a la versión {VERSION}. "
                + (f"Ahora ya tienes esto: {notes}" if notes else ""))
    return "La última actualización no se completó; sigo en la versión anterior."
