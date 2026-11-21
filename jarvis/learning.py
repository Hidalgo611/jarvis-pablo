"""Aprendizaje: observa el uso, detecta hábitos, propone rutinas y ejecuta rutinas/recordatorios."""
from __future__ import annotations

import json
import logging
import re
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


BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
BROWSER_SUFFIX = re.compile(r"\s*[-—–]\s*(google chrome|microsoft\u200b? edge|mozilla firefox|brave|opera|vivaldi)\s*$", re.I)
PROFILE_SUFFIX = re.compile(r"\s*[-—–]\s*(personal|trabajo|work|perfil \d+|profile \d+)\s*$", re.I)
SKIP_TITLES = {"nueva pestaña", "new tab", "pestaña nueva", "", "inicio", "untitled", "sin título"}


def _foreground() -> tuple[str | None, str]:
    """(proceso, título de la ventana) de lo que está delante."""
    try:
        import win32gui
        import win32process
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None, ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name().lower(), win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return None, ""


def _foreground_process() -> str | None:
    return _foreground()[0]


def site_from_title(title: str) -> str | None:
    """«Bandeja de entrada (3) - pablo@gmail.com - Gmail - Google Chrome» → «Gmail»; «Vídeo X - YouTube - …» → «YouTube»."""
    t = BROWSER_SUFFIX.sub("", title or "").strip()
    t = PROFILE_SUFFIX.sub("", t).strip()
    t = re.sub(r"^\(\d+\+?\)\s*", "", t)
    if t.lower() in SKIP_TITLES:
        return None
    parts = [x.strip() for x in re.split(r"\s[-—–|·]\s", t) if x.strip()]
    if not parts:
        return None
    site = parts[-1] if len(parts) > 1 and len(parts[-1]) <= 28 else parts[0]
    if len(site) > 40 or "@" in site:
        return None
    return site


def norm_value(v: str) -> str:
    v = (v or "").lower().strip()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    v = re.sub(r"\.(com|es|org|net|tv|io)(/.*)?$", "", v)
    return v.strip(" /")


class ActivityWatcher(threading.Thread):
    """Cada 20 s mira qué programa (y qué web) está delante; registra la primera vez del día que se usa (solo local)."""

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
                    p, title = _foreground()
                    day = time.strftime("%Y-%m-%d")
                    if p and p not in IGNORE and self.seen_today.get(p) != day:
                        self.seen_today[p] = day
                        memory.event("app_use", FRIENDLY.get(p, p.removesuffix(".exe")))
                    if p in BROWSERS:
                        site = site_from_title(title)
                        k = "site:" + norm_value(site or "")
                        if site and self.seen_today.get(k) != day:
                            self.seen_today[k] = day
                            memory.event("site_visit", site)
            except Exception:
                log.exception("watcher")
            time.sleep(20)


class PatternDetector:
    WINDOW_MIN = 35   # 8:55 y 9:20 cuentan como «sobre las 9»

    def find_patterns(self, min_days: int = 3, window_days: int = 14) -> list[dict]:
        since = time.time() - window_days * 86400
        evs = memory.events(("app_use", "app_open", "site_open", "site_visit", "boot"), since)
        boots = sorted(ts for ts, k, _ in evs if k == "boot")
        # (categoría, valor normalizado) -> {día: primer minuto del día}
        first: dict[tuple, dict] = defaultdict(dict)
        labels: dict[tuple, dict] = defaultdict(lambda: defaultdict(int))
        wdays: dict[tuple, dict] = defaultdict(dict)
        after_boot: dict[tuple, set] = defaultdict(set)
        for ts, kind, val in evs:
            if kind == "boot" or not val:
                continue
            cat = "site" if kind.startswith("site") else "app"
            key = (cat, norm_value(val))
            if not key[1]:
                continue
            lt = time.localtime(ts)
            day = time.strftime("%Y-%m-%d", lt)
            minute = lt.tm_hour * 60 + lt.tm_min
            if day not in first[key] or minute < first[key][day]:
                first[key][day] = minute
                wdays[key][day] = lt.tm_wday
            labels[key][val] += 1
            if any(0 <= ts - b <= 600 for b in boots):
                after_boot[key].add(day)
        out = []
        for key, per_day in first.items():
            mins = sorted(per_day.values())
            best_days: list = []
            for c in mins:
                hit = [d for d, m in per_day.items() if abs(m - c) <= self.WINDOW_MIN]
                if len(hit) > len(best_days):
                    best_days = hit
            if len(best_days) < min_days:
                continue
            ms = sorted(per_day[d] for d in best_days)
            med = ms[len(ms) // 2]
            med = max(0, med - med % 5)
            hh, mm = divmod(med, 60)
            laborables = all(wdays[key][d] < 5 for d in best_days) and len(best_days) >= 3
            label = max(labels[key].items(), key=lambda kv: kv[1])[0]
            what = label if label[:1].isupper() else label.capitalize()
            span = min(window_days, max(7, int((time.time() - since) / 86400)))
            hora = f"{hh}:{mm:02d}"
            out.append({"key": f"time:{key[0]}:{key[1]}:{hh}", "kind": key[0], "value": label, "time": f"{hh:02d}:{mm:02d}",
                        "hour": hh, "days": len(best_days), "weekdays": laborables,
                        "text": (f"{'entre semana ' if laborables else ''}sueles abrir {what} sobre las {hora} "
                                 f"({len(best_days)} días de los últimos {span})")})
        for key, days in after_boot.items():
            if len(days) >= min_days:
                label = max(labels[key].items(), key=lambda kv: kv[1])[0]
                out.append({"key": f"boot:{key[0]}:{key[1]}", "kind": "boot", "cat": key[0], "value": label, "days": len(days),
                            "text": f"sueles abrir {label} nada más encender el ordenador ({len(days)} días)"})
        out.sort(key=lambda p: -p["days"])
        return out

    def _already_automated(self, value: str) -> bool:
        v = norm_value(value)
        for (acts,) in memory.q("SELECT actions FROM routines WHERE enabled=1"):
            if v and v in acts.lower():
                return True
        return False

    def next_proposal(self) -> dict | None:
        if not settings["learning"]:
            return None
        for p in self.find_patterns(min_days=int(settings.get("habit_min_days") or 4)):
            st = memory.q("SELECT status FROM proposals WHERE key=?", (p["key"],))
            if not st and not self._already_automated(p["value"]):
                return p
        return None

    def answer(self, p: dict, accepted: bool) -> str:
        memory.q("INSERT OR REPLACE INTO proposals(key, status, data, ts) VALUES(?,?,?,?)",
                 (p["key"], "accepted" if accepted else "rejected", json.dumps(p, ensure_ascii=False), time.time()))
        if not accepted:
            return ""
        val = p["value"]
        site = p["kind"] == "site" or p.get("cat") == "site"
        action = {"tool": "open_website", "args": {"target": val}} if site else {"tool": "open_app", "args": {"name": val}}
        if p["kind"] == "boot":
            trig = {"type": "startup"}
            name = f"Abrir {val} al encender"
        else:
            days = "laborables" if p.get("weekdays") else "todos"
            trig = {"type": "time", "time": p["time"], "days": days}
            name = f"Abrir {val} a las {p['time'].lstrip('0') or '0:00'}"
        memory.q("INSERT INTO routines(name, trigger, actions, ts) VALUES(?,?,?,?)",
                 (name, json.dumps(trig, ensure_ascii=False),
                  json.dumps({"actions": [action], "announce": f"Como cada día, te abro {val}."}, ensure_ascii=False),
                  time.time()))
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
