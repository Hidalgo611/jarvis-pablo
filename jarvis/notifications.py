"""Avisos de notificaciones (WhatsApp, móvil vía «Vincular al móvil», etc.) leyendo la base de datos local de Windows.

Privacidad: solo se anuncia QUIÉN y DE QUÉ APP; el contenido solo se lee si el usuario lo pide.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from typing import Callable
from xml.etree import ElementTree as ET

from .config import settings

log = logging.getLogger("notifications")
DB = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db")

# id del manejador → nombre que se dice
APPS = {
    "whatsapp": "WhatsApp", "yourphone": "el móvil", "phonelink": "el móvil", "telegram": "Telegram",
    "discord": "Discord", "instagram": "Instagram", "messenger": "Messenger", "signal": "Signal",
    "outlook": "Outlook", "mail": "Correo", "teams": "Teams", "slack": "Slack",
}
GENERIC = re.compile(r"(may have new messages|nuevos mensajes|new messages|tienes mensajes)", re.I)

recent: list[dict] = []   # últimas notificaciones (para «¿qué dice?»)


def _app_name(handler: str) -> str | None:
    h = handler.lower()
    for k, v in APPS.items():
        if k in h:
            return v
    return None


def _parse(payload: bytes) -> tuple[list[str], str]:
    try:
        root = ET.fromstring(payload.decode("utf-8", "ignore"))
    except Exception:
        return [], ""
    texts, attribution = [], ""
    for t in root.iter("text"):
        if (t.get("placement") or "") == "attribution":
            attribution = (t.text or "").strip()
        elif t.text and t.text.strip():
            texts.append(t.text.strip())
    return texts, attribution


class NotificationWatcher(threading.Thread):
    def __init__(self, announce: Callable[[str], None]) -> None:
        super().__init__(name="notifications", daemon=True)
        self.announce = announce
        self.last_id = None
        self.pending: list[dict] = []
        self.first_pending = 0.0

    def _read(self) -> list[tuple]:
        if not os.path.exists(DB):
            return []
        d = tempfile.mkdtemp(prefix="jarvis-n-")
        try:
            for ext in ("", "-wal", "-shm"):
                if os.path.exists(DB + ext):
                    shutil.copy2(DB + ext, os.path.join(d, "n.db" + ext))
            db = sqlite3.connect(os.path.join(d, "n.db"))
            rows = db.execute("SELECT n.Id, h.PrimaryId, n.Payload FROM Notification n "
                              "JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
                              "WHERE n.Type = 'toast' ORDER BY n.Id").fetchall()
            db.close()
            return rows
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def run(self) -> None:
        while True:
            try:
                if settings["notifications"]:
                    self._poll()
            except Exception:
                log.exception("notificaciones")
            time.sleep(4)

    def _poll(self) -> None:
        rows = self._read()
        if not rows:
            return
        top = max(r[0] for r in rows)
        if self.last_id is None:          # al arrancar: no anunciar lo antiguo
            self.last_id = top
            return
        for nid, handler, payload in rows:
            if nid <= self.last_id:
                continue
            app = _app_name(handler)
            if not app:
                continue
            texts, attribution = _parse(payload or b"")
            if app == "el móvil" and attribution:          # Vincular al móvil: «WhatsApp • Teléfono»
                app = attribution.split("•")[0].strip() + " del móvil"
            sender = texts[0] if texts and not GENERIC.search(texts[0]) else ""
            body = " ".join(texts[1:]) if len(texts) > 1 else ""
            item = {"ts": time.time(), "app": app, "sender": sender, "body": body}
            recent.append(item)
            del recent[:-30]
            self.pending.append(item)
            if len(self.pending) == 1:
                self.first_pending = time.time()
        self.last_id = max(self.last_id, top)
        # agrupa durante 6 s para no soltar diez avisos seguidos
        if self.pending and time.time() - self.first_pending > 6:
            self.announce(self._phrase(self.pending))
            self.pending = []

    @staticmethod
    def _phrase(items: list[dict]) -> str:
        u = settings["address"]
        by_app: dict[str, list[str]] = {}
        for it in items:
            by_app.setdefault(it["app"], [])
            if it["sender"] and it["sender"] not in by_app[it["app"]]:
                by_app[it["app"]].append(it["sender"])
        parts = []
        for app, senders in by_app.items():
            n = sum(1 for it in items if it["app"] == app)
            what = "un mensaje" if n == 1 else f"{n} mensajes"
            who = (" de " + " y ".join(senders[:3])) if senders else ""
            parts.append(f"{what} de {app}{who}" if not senders else f"{what} en {app}{who}")
        return f"{u}, tienes " + "; y ".join(parts) + "."


def latest(app: str | None = None, n: int = 5) -> list[dict]:
    items = [it for it in recent if not app or app.lower() in it["app"].lower()]
    return items[-n:]
