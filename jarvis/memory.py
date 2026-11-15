"""Memoria persistente (SQLite): conversación, hechos sobre el usuario, eventos de uso, rutinas."""
from __future__ import annotations

import json
import sqlite3
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
        rows = self.q("SELECT id, text FROM facts")
        low = text.lower()
        ids = [r[0] for r in rows if low in r[1].lower() or r[1].lower() in low]
        for i in ids:
            self.q("DELETE FROM facts WHERE id=?", (i,))
        return len(ids)

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


memory = Memory()
