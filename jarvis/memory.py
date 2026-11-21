"""Memoria persistente (SQLite): conversación, hechos sobre el usuario, eventos de uso, rutinas."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import threading
import time
from typing import Any

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY, text TEXT UNIQUE, ts REAL);
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, role TEXT, text TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, value TEXT);
CREATE INDEX IF NOT EXISTS ev_kind ON events(kind, ts);
CREATE TABLE IF NOT EXISTS routines(id INTEGER PRIMARY KEY, name TEXT, trigger TEXT, actions TEXT,
    enabled INTEGER DEFAULT 1, last_run REAL DEFAULT 0, ts REAL);
CREATE TABLE IF NOT EXISTS proposals(key TEXT PRIMARY KEY, status TEXT, data TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, due REAL, text TEXT, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""


class Memory:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def q(self, sql: str, args: tuple = ()) -> list:
        with self.lock:
            cur = self.db.execute(sql, args)
            rows = cur.fetchall()
            self.db.commit()
            return rows

    # --- hechos ---
    def add_fact(self, text: str) -> None:
        self.q("INSERT OR IGNORE INTO facts(text, ts) VALUES(?,?)", (text.strip(), time.time()))

    def forget_fact(self, text: str) -> int:
        """Olvida los datos que encajan con `text` (literal o por palabras clave: «lo del Barça» → «Le gusta el Barça»)."""
        rows = self.q("SELECT id, text FROM facts")
        low = _norm(text)
        key = _keywords(text)
        ids = []
        for i, t in rows:
            nt = _norm(t)
            if low and (low in nt or nt in low):
                ids.append(i)
            elif key and len(key & _keywords(t)) >= max(1, (len(key) + 1) // 2):
                ids.append(i)
        for i in ids:
            self.q("DELETE FROM facts WHERE id=?", (i,))
        return len(ids)

    def replace_fact(self, old: str, new: str) -> int:
        n = self.forget_fact(old) if old.strip() else 0
        self.add_fact(new)
        return n

    # --- perfil: datos únicos que se corrigen (nombre, ciudad, trabajo…) ---
    def profile(self) -> dict:
        return self.get("profile", {}) or {}

    def set_profile(self, key: str, value: str) -> None:
        p = self.profile()
        if value:
            p[key] = value
        else:
            p.pop(key, None)
        self.set("profile", p)

    # --- resumen de la conversación (lo que ya no cabe en los turnos recientes) ---
    def summary(self) -> dict:
        return self.get("conv_summary", {}) or {}

    def set_summary(self, text: str) -> None:
        self.set("conv_summary", {"ts": time.time(), "day": time.strftime("%Y-%m-%d"), "text": text.strip()})

    def facts(self) -> list[str]:
        return [r[0] for r in self.q("SELECT text FROM facts ORDER BY ts")]

    # --- conversación ---
    def add_history(self, role: str, text: str) -> None:
        self.q("INSERT INTO history(role, text, ts) VALUES(?,?,?)", (role, text, time.time()))

    def recent_history(self, n: int = 8, max_age_h: float = 6) -> list[dict]:
        rows = self.q("SELECT role, text FROM history WHERE ts > ? ORDER BY id DESC LIMIT ?",
                      (time.time() - max_age_h * 3600, n))
        return [{"role": r, "content": t} for r, t in reversed(rows)]

    # --- eventos ---
    def event(self, kind: str, value: str = "") -> None:
        self.q("INSERT INTO events(ts, kind, value) VALUES(?,?,?)", (time.time(), kind, value[:300]))

    def events(self, kinds: tuple, since_ts: float) -> list[tuple]:
        marks = ",".join("?" * len(kinds))
        return self.q(f"SELECT ts, kind, value FROM events WHERE kind IN ({marks}) AND ts > ? ORDER BY ts",
                      (*kinds, since_ts))

    # --- kv ---
    def get(self, key: str, default: Any = None) -> Any:
        r = self.q("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(r[0][0]) if r else default

    def set(self, key: str, value: Any) -> None:
        self.q("INSERT OR REPLACE INTO kv(key, value) VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))


STOP = {"el", "la", "los", "las", "un", "una", "de", "del", "que", "y", "a", "en", "lo", "le", "se", "su", "sus", "es", "me",
        "mi", "por", "con", "para", "al", "usuario", "pablo", "le gusta", "gusta", "tiene", "esta", "este", "eso", "esto",
        "dato", "sobre", "muy", "mas", "no"}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def _keywords(t: str) -> set:
    return {w for w in _norm(t).split() if len(w) >= 3 and w not in STOP}


memory = Memory()
