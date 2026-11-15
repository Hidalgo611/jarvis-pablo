"""Aprendizaje: observa el uso, detecta hábitos, propone rutinas y ejecuta rutinas/recordatorios."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from typing import Callable

import psutil

from .config import settings
from .memory import memory

log = logging.getLogger("learning")

IGNORE = {"jarvis.exe", "python.exe", "pythonw.exe", "explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe",
          "shellexperiencehost.exe", "applicationframehost.exe", "textinputhost.exe", "lockapp.exe", "systemsettings.exe",
          "ollama.exe", "ollama app.exe", "taskmgr.exe", "dwm.exe", "conhost.exe", "cmd.exe", "powershell.exe"}

FRIENDLY = {"chrome.exe": "Chrome", "msedge.exe": "Edge", "spotify.exe": "Spotify", "discord.exe": "Discord",
            "steam.exe": "Steam", "winword.exe": "Word", "excel.exe": "Excel", "code.exe": "Visual Studio Code",
            "whatsapp.exe": "WhatsApp", "firefox.exe": "Firefox", "obs64.exe": "OBS", "epicgameslauncher.exe": "Epic Games"}

DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _foreground_process() -> str | None:
    try:
        import win32gui
        import win32process
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name().lower()
    except Exception:
        return None


class ActivityWatcher(threading.Thread):
    """Cada 30 s mira qué programa está delante; registra cuándo se empieza a usar cada uno (solo local)."""

    def __init__(self) -> None:
        super().__init__(name="activity", daemon=True)
        self.seen_today: dict[str, str] = {}

    def run(self) -> None:
        boot = psutil.boot_time()
        last_boot = memory.get("last_boot_logged", 0)
        if abs(boot - last_boot) > 60:
            memory.q("INSERT INTO events(ts, kind, value) VALUES(?,?,?)", (boot, "boot", ""))
            memory.set("last_boot_logged", boot)
        while True:
            try:
                if settings["learning"]:
                    p = _foreground_process()
                    day = time.strftime("%Y-%m-%d")
                    if p and p not in IGNORE and self.seen_today.get(p) != day:
                        # primera vez hoy que usa este programa (o tras 2 h sin usarlo)
                        self.seen_today[p] = day
                        memory.event("app_use", FRIENDLY.get(p, p.removesuffix(".exe")))
            except Exception:
                log.exception("watcher")
            time.sleep(30)


class PatternDetector:
    def find_patterns(self, min_days: int = 3, window_days: int = 14) -> list[dict]:
        since = time.time() - window_days * 86400
        evs = memory.events(("app_use", "app_open", "site_open", "boot"), since)
        boots = sorted(ts for ts, k, _ in evs if k == "boot")
        by_hour: dict[tuple, set] = defaultdict(set)
        after_boot: dict[str, set] = defaultdict(set)
        for ts, kind, val in evs:
            if kind == "boot" or not val:
                continue
            lt = time.localtime(ts)
            day = time.strftime("%Y-%m-%d", lt)
            by_hour[(kind, val.lower(), lt.tm_hour)].add(day)
            prev = [b for b in boots if 0 <= ts - b <= 600]
            if prev:
                after_boot[val.lower()].add(day)
        out = []
        for (kind, val, hour), days in by_hour.items():
            if len(days) >= min_days:
                what = val.capitalize()
                out.append({"key": f"hour:{val}:{hour}", "kind": kind, "value": val, "hour": hour, "days": len(days),
                            "text": f"sueles abrir {what} sobre las {hour}:00 ({len(days)} días)"})
        for val, days in after_boot.items():
            if len(days) >= min_days:
                out.append({"key": f"boot:{val}", "kind": "boot", "value": val, "days": len(days),
                            "text": f"sueles abrir {val.capitalize()} nada más encender el ordenador ({len(days)} días)"})
        out.sort(key=lambda p: -p["days"])
        return out

    def next_proposal(self) -> dict | None:
        if not settings["learning"]:
            return None
        for p in self.find_patterns():
            st = memory.q("SELECT status FROM proposals WHERE key=?", (p["key"],))
            if not st:
                return p
        return None

    def answer(self, p: dict, accepted: bool) -> str:
        memory.q("INSERT OR REPLACE INTO proposals(key, status, data, ts) VALUES(?,?,?,?)",
                 (p["key"], "accepted" if accepted else "rejected", json.dumps(p, ensure_ascii=False), time.time()))
        if not accepted:
            return ""
        val = p["value"]
        tool_name = "open_website" if p["kind"] == "site_open" else "open_app"
        args = {"target": val} if tool_name == "open_website" else {"name": val}
        if p["kind"] == "boot":
            trig = {"type": "startup"}
            name = f"Abrir {val} al encender"
        else:
            trig = {"type": "time", "time": f"{p['hour']:02d}:00", "days": "todos"}
            name = f"Abrir {val} a las {p['hour']}:00"
        memory.q("INSERT INTO routines(name, trigger, actions, ts) VALUES(?,?,?,?)",
                 (name, json.dumps(trig, ensure_ascii=False),
                  json.dumps({"actions": [{"tool": tool_name, "args": args}],
                              "announce": f"Como cada día, abro {val}."}, ensure_ascii=False), time.time()))
        return name


detector = PatternDetector()


def _day_ok(spec: str, wday: int) -> bool:
    s = (spec or "todos").lower()
    if s in ("todos", "diario", "cada día", "all"):
        return True
    if s == "laborables":
        return wday < 5
    if s in ("finde", "fin de semana"):
        return wday >= 5
    return DAYS[wday] in s


class Scheduler(threading.Thread):
    """Ejecuta rutinas (hora / al arrancar) y recordatorios."""

    def __init__(self, run_actions: Callable[[str, list, str], None], speak: Callable[[str], None]) -> None:
        super().__init__(name="scheduler", daemon=True)
        self.run_actions = run_actions
        self.speak = speak
        self.started = time.time()

    def startup_routines(self) -> None:
        boot = psutil.boot_time()
        for rid, name, trig, acts, last in memory.q("SELECT id, name, trigger, actions, last_run FROM routines WHERE enabled=1"):
            t = json.loads(trig)
            if t.get("type") == "startup" and last < boot:
                self._fire(rid, name, acts)

    def phrase_routine(self, text: str) -> bool:
        low = text.lower()
        for rid, name, trig, acts in memory.q("SELECT id, name, trigger, actions FROM routines WHERE enabled=1"):
            t = json.loads(trig)
            if t.get("type") == "phrase" and t.get("phrase") and t["phrase"] in low:
                self._fire(rid, name, acts)
                return True
        return False

    def _fire(self, rid: int, name: str, acts: str) -> None:
        memory.q("UPDATE routines SET last_run=? WHERE id=?", (time.time(), rid))
        a = json.loads(acts)
        log.info("rutina %s", name)
        self.run_actions(name, a.get("actions", []), a.get("announce", ""))

    def run(self) -> None:
        while True:
            try:
                now = time.time()
                lt = time.localtime(now)
                hm = time.strftime("%H:%M", lt)
                for rid, name, trig, acts, last in memory.q("SELECT id, name, trigger, actions, last_run FROM routines WHERE enabled=1"):
                    t = json.loads(trig)
                    if t.get("type") == "time" and t.get("time") == hm and now - last > 3600 and _day_ok(t.get("days"), lt.tm_wday):
                        self._fire(rid, name, acts)
                for rid, text in memory.q("SELECT id, text FROM reminders WHERE done=0 AND due<=?", (now,)):
                    memory.q("UPDATE reminders SET done=1 WHERE id=?", (rid,))
                    self.speak(f"Disculpe, {settings['address']}. Le recuerdo: {text}")
            except Exception:
                log.exception("scheduler")
            time.sleep(15)
