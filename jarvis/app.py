"""Orquestador de Jarvis: estados, sesión de conversación, servicios y bandeja del sistema."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import queue
import random
import re
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import installer, tools
from .bus import bus
from .config import DATA, DOCS_OUT, EXE_PATH, FROZEN, LOGS, SETTINGS_PATH, VERSION, settings
from .memory import memory

log = logging.getLogger("app")

YES = re.compile(r"\b(s[ií]|vale|claro|hazlo|adelante|por supuesto|venga|dale|ok|okay|de acuerdo|correcto|perfecto|afirmativo|exacto|eso es)\b", re.I)
NO = re.compile(r"\b(no|nah|para nada|d[ée]jalo|ahora no|luego|m[aá]s tarde|nunca|negativo)\b", re.I)
CANCEL = re.compile(r"^\W*(nada|cancela|cancelar|olv[ií]dalo|d[ée]jalo|eso es todo|nada m[aá]s|silencio|c[aá]llate|stop|ya est[aá]|"
                    r"adi[oó]s|hasta luego|gracias(,? jarvis)?(,? eso es todo| nada m[aá]s)?|muchas gracias)\W*$", re.I)
SELECT = re.compile(r"^\W*(ese|esa|este|esta|[ée]se|[ée]ste|ah[ií]|quiero ese|pon ese|ponme ese|ese mismo|ese de ah[ií]|ese es|"
                    r"ese v[ií]deo|para ah[ií]|ese quiero|ese me vale|abre ese|ese,? s[ií])\b", re.I)
STOP_SCROLL = re.compile(r"^\W*(para|stop|alto|quieto|det[eé]nte|vale para)\W*$", re.I)


WAKE_PREFIX = re.compile(r"^\W*(?:\S+\W+){0,4}?(?:hey|oye|ey|eh|hola|vale)?\W*"
                         r"(jarvis|yarvis|jarvi|harvis|charvis|yarbis|jarbis|garvis|llarvis|jarbys|jarvys)\b[\s,.:;!¡¿?]*", re.I)


def strip_wake(text: str) -> str:
    """«Oye Jarvis, abre Spotify» → «abre Spotify». Solo si la palabra está al principio."""
    m = WAKE_PREFIX.match(text)
    return text[m.end():].strip() if m else text


BARGE_WORDS = re.compile(r"\b(para|par[aá]te|espera|calla|c[aá]llate|stop|basta|silencio|oye|jarvis|yarvis|vale vale|"
                         r"no no|d[eé]jalo|un momento)\b", re.I)


def is_user_speech(heard: str, said_sentences: list[str]) -> bool:
    W = r"[a-záéíóúñü]{3,}"
    h = heard.lower()
    said_words = set(re.findall(W, " ".join(said_sentences).lower()))
    for m in BARGE_WORDS.finditer(h):
        if not set(re.findall(W, m.group(0))) <= said_words:   # palabra de parada que Jarvis no está diciendo
            return True
    words = re.findall(W, h)
    if len(words) < 3:
        return False
    overlap = sum(w in said_words for w in words) / len(words)
    return overlap < 0.35


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QRadialGradient(32, 32, 30)
    g.setColorAt(0, QColor(235, 250, 255))
    g.setColorAt(0.3, QColor(64, 210, 255))
    g.setColorAt(1, QColor(0, 40, 70, 0))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    p.setPen(QPen(QColor(64, 210, 255), 4))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(6, 6, 52, 52)
    p.end()
    return QIcon(pm)


def parse_hotkey(s: str) -> tuple[int, int]:
    mods, vk = 0x4000, 0x4A  # NOREPEAT, J
    for part in s.lower().split("+"):
        part = part.strip()
        if part in ("ctrl", "control"):
            mods |= 0x2
        elif part == "alt":
            mods |= 0x1
        elif part == "shift":
            mods |= 0x4
        elif part in ("win", "windows"):
            mods |= 0x8
        elif re.fullmatch(r"f\d{1,2}", part):
            vk = 0x70 + int(part[1:]) - 1
        elif len(part) == 1:
            vk = ord(part.upper())
    return mods, vk


def hotkey_thread(callback) -> None:
    u = ctypes.windll.user32
    mods, vk = parse_hotkey(settings["hotkey"])
    if not u.RegisterHotKey(None, 1, mods, vk):
        log.warning("no se pudo registrar el atajo %s", settings["hotkey"])
        return
    msg = wt.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:
            callback()


def set_autostart() -> None:
    if not FROZEN:
        return
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
    if settings["autostart"]:
        winreg.SetValueEx(key, "Jarvis", 0, winreg.REG_SZ, f'"{EXE_PATH}" --autostart')
    else:
        try:
            winreg.DeleteValue(key, "Jarvis")
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)


def greeting_word() -> str:
    h = time.localtime().tm_hour
    return "Buenos días" if 6 <= h < 14 else ("Buenas tardes" if 14 <= h < 21 else "Buenas noches")


class Jarvis(QObject):
    ready = Signal()
    failed = Signal(str)

    def __init__(self, qapp: QApplication, args: list[str]) -> None:
        super().__init__()
        from .ui.overlay import Overlay
        self.qapp = qapp
        self.args = args
        self.overlay = Overlay()
        self.events: "queue.Queue" = queue.Queue()
        self.listen_id = 0
        self.session = False
        self.pending_q: dict | None = None
        self.last_proposal_ask = 0.0
        self.step_idx = 0
        self.plan_len = 0
        self.task_id = 0
        self.pending_update = None
        self.greeter = None
        self.speaker = self.audio = self.brain = self.updater = self.skills = self.scheduler = None
        self._make_tray()
        bus.quit.connect(self.quit)
        bus.wake.connect(lambda src: self.events.put(("wake", src, "")))
        self.failed.connect(self._on_failed)
        threading.Thread(target=self._boot, name="boot", daemon=True).start()

    # ------------------------------------------------------------------ bandeja
    def _make_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon(), self.qapp)
        self.tray.setToolTip(f"J.A.R.V.I.S. {VERSION} — di «Hey Jarvis» o pulsa {settings['hotkey'].upper()}")
        m = QMenu()
        acts = [("Activar Jarvis", lambda: self.events.put(("wake", "tray", ""))),
                ("Escribir una orden…", self._typed_command),
                ("Abrir documentos de Jarvis", lambda: os.startfile(DOCS_OUT)),
                ("Ajustes…", self._open_settings),
                ("Ver la presentación", lambda: self.events.put(("intro",))),
                ("Ver registro", lambda: os.startfile(LOGS / "jarvis.log")),
                ("Buscar actualizaciones", lambda: self.events.put(("check_update",))),
                ("Reintentar instalación", lambda: threading.Thread(target=self._boot, daemon=True).start()),
                (None, None), ("Salir", self.quit)]
        for text, fn in acts:
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, m)
            a.triggered.connect(fn)
            m.addAction(a)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.events.put(("wake", "tray", "")) if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        self._menu = m

    def _open_settings(self) -> None:
        from .ui.settings_window import SettingsWindow

        def test_voice(vid: str) -> None:
            if self.speaker:
                self.speaker.load_voice(vid)
                self.speaker.say(f"Hola, {settings['address']}. Así suena esta voz. ¿Te gusta?")

        def saved() -> None:
            if self.speaker:
                self.speaker.load_voice(settings["voice"])
            try:
                set_autostart()
            except Exception:
                log.exception("autostart")
        self._settings_win = SettingsWindow(test_voice, saved, lambda: self.events.put(("intro",)))
        self._settings_win.show()
        self._settings_win.raise_()

    def _typed_command(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(None, "J.A.R.V.I.S.", "¿Qué necesita, señor?")
        if ok and text.strip():
            self._interrupt_and_wake("typed", text.strip())

    def _dev_inbox(self) -> None:
        """Solo en desarrollo: órdenes de prueba escritas en dev_cmd.txt."""
        f = DATA / "dev_cmd.txt"
        while True:
            time.sleep(0.5)
            if f.exists():
                try:
                    text = f.read_text(encoding="utf-8-sig").strip()
                    f.unlink()
                    if text:
                        self._interrupt_and_wake("typed", text)
                except Exception:
                    log.exception("dev inbox")

    def _on_failed(self, msg: str) -> None:
        self.tray.showMessage("Jarvis", msg, QSystemTrayIcon.Warning, 15000)

    def quit(self) -> None:
        try:
            if self.speaker:
                self.speaker.stop()
        finally:
            self.tray.hide()
            self.qapp.quit()
            os._exit(0)

    # ------------------------------------------------------------------ arranque
    def _boot(self) -> None:
        from . import updater as upd
        upd.cleanup_old()
        need_setup = not installer.models_ready() or not installer.ollama_up() or \
            settings["model"] not in installer.installed_models()
        if need_setup:
            bus.mode.emit("setup")
            bus.state.emit("setup")
        try:
            installer.run_setup()
        except Exception as e:
            log.exception("setup")
            bus.progress.emit(-2, "")
            bus.state.emit("error")
            bus.status.emit("No he podido completar la instalación")
            self.failed.emit(f"No se pudo completar la instalación: {e}. Revisa la conexión y pulsa «Reintentar instalación».")
            time.sleep(6)
            bus.mode.emit("hidden")
            return
        bus.progress.emit(-2, "")
        threading.Thread(target=installer.ensure_vision_background, daemon=True).start()
        self._start_services(first_run=need_setup)

    def _start_services(self, first_run: bool) -> None:
        if self.speaker is not None:
            return
        from .audio import AudioEngine
        from .brain import Brain
        from .learning import ActivityWatcher, Scheduler
        from .skills import SkillManager
        from .tts import Speaker
        from .tools import assistant, system
        from .updater import Updater, announce_if_updated

        tools.load_all()
        system.build_index()
        self.speaker = Speaker()
        self.brain = Brain()
        self.skills = SkillManager()
        self.updater = Updater()
        assistant.CTX.update(speaker=self.speaker, updater=self.updater, skills=self.skills)
        self.audio = AudioEngine()
        self.audio.on_wake = self._interrupt_and_wake
        self.audio.on_barge = self._is_user_speech
        self.speaker.on_active = self._on_speaking
        self.audio.start()
        self.scheduler = Scheduler(self._routine_actions, lambda t: self.events.put(("say", t)))
        self.scheduler.start()
        ActivityWatcher().start()
        from .notifications import NotificationWatcher
        NotificationWatcher(lambda t: self.events.put(("notify", t))).start()
        threading.Thread(target=hotkey_thread, args=(lambda: self._interrupt_and_wake("hotkey", ""),),
                         name="hotkey", daemon=True).start()
        threading.Thread(target=self._instance_listener, name="instance", daemon=True).start()
        try:
            set_autostart()
        except Exception:
            log.exception("autostart")
        threading.Thread(target=self._conductor, name="conductor", daemon=True).start()
        if not FROZEN:
            threading.Thread(target=self._dev_inbox, name="dev-inbox", daemon=True).start()

        # saludo
        u, a = settings["user_name"], settings["address"]
        from .experience import Greeter
        self.greeter = Greeter(self.brain, lambda: self.session or self.brain.busy)
        msg = announce_if_updated()
        if not msg and not memory.get("intro_done"):
            # la presentación épica llega con el primer «Hey Jarvis»
            self.tray.showMessage("J.A.R.V.I.S.", f"Todo listo, {u}. Di «Hey Jarvis» para empezar.",
                                  QSystemTrayIcon.Information, 12000)
        elif not msg and settings["greet_on_start"] and "--quiet" not in self.args:
            msg = f"{greeting_word()}, {a}. Sistemas en línea."
        if msg:
            self.events.put(("greet", msg))
        # deja calculado en la caché de Ollama el prefijo (system + herramientas): la 1ª orden será rápida
        self.brain.prefill("Hola, Jarvis.")
        if "--command" in self.args:  # p.ej. acceso directo: Jarvis.exe --command "abre YouTube"
            i = self.args.index("--command")
            if i + 1 < len(self.args):
                self.events.put(("wake", "typed", self.args[i + 1]))
        self.scheduler.startup_routines()
        threading.Thread(target=self._check_update_bg, daemon=True).start()

    def _instance_listener(self) -> None:
        import win32event
        ev = win32event.CreateEvent(None, False, False, "JarvisAssistant.Activate")
        while True:
            win32event.WaitForSingleObject(ev, win32event.INFINITE)
            pc = DATA / "pending_command.txt"
            if pc.exists():
                text = pc.read_text(encoding="utf-8").strip()
                pc.unlink()
                self._interrupt_and_wake("typed", text)
            else:
                self.events.put(("wake", "tray", ""))

    def _check_update_bg(self) -> None:
        time.sleep(20)
        while True:
            info = self.updater.check(force=True) if self.updater else None
            if info:
                self.events.put(("update_available", info))
                return
            time.sleep(max(0.25, float(settings["update_check_hours"])) * 3600)

    def _interrupt_and_wake(self, source: str, rest: str) -> None:
        """Se llama desde hilos de audio/atajo: corta voz y tarea al instante y avisa al conductor."""
        if self.speaker:
            self.speaker.stop()
        if self.brain and self.brain.busy:
            self.brain.cancel.set()
        self.events.put(("wake", source, rest))

    def _is_user_speech(self, heard: str) -> bool:
        """Mientras Jarvis habla: ¿lo que entra por el micro es el usuario (interrumpe) o el eco de su propia voz?"""
        return is_user_speech(heard, [t for ts, t in self.speaker.recent if time.time() - ts < 25])

    def _on_speaking(self, active: bool) -> None:
        if self.audio:
            self.audio.tts_active = active

    # ------------------------------------------------------------------ conductor (un único hilo decide)
    def _conductor(self) -> None:
        while True:
            ev = self.events.get()
            try:
                self._handle(ev)
            except Exception:
                log.exception("conductor %s", ev[0])

    def _say_and_wait(self, text: str) -> None:
        self.speaker.say(text)
        self.speaker.wait()

    def _listen(self, seconds: float) -> None:
        self.listen_id += 1
        lid = self.listen_id
        bus.state.emit("listening")
        self.audio.listen(seconds,
                          on_partial=lambda t: self.events.put(("partial", lid, t)),
                          on_final=lambda t: self.events.put(("final", lid, t)),
                          on_timeout=lambda: self.events.put(("timeout", lid)))

    def _end_session(self) -> None:
        self.session = False
        self.listen_id += 1
        self.audio.stop_listening()
        from . import media
        threading.Thread(target=media.unduck, daemon=True).start()
        bus.state.emit("idle")
        bus.mode.emit("hidden")
        bus.transcript.emit("")
        if self.pending_update:
            info, self.pending_update = self.pending_update, None
            self.events.put(("update_available", info))

    def _handle(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "wake":
            self._on_wake(ev[1], ev[2])
        elif kind == "partial":
            if ev[1] != self.listen_id:
                return
            text = ev[2]
            bus.transcript.emit(text)
            from .tools.browser import browsing_active
            if browsing_active() and (SELECT.match(text) or STOP_SCROLL.match(text)) and len(text.split()) <= 5:
                self.listen_id += 1
                self.audio.stop_listening()
                self._utterance(text)
            else:
                self.brain.prefill(text)
        elif kind == "final":
            if ev[1] != self.listen_id:
                return
            raw = ev[2].strip()
            text = strip_wake(raw)
            if not text:
                if raw and self.greeter:  # solo dijo «Jarvis»: saludo y escucho
                    self._say_and_wait(self.greeter.take() or f"Dime, {settings['address']}.")
                self._listen(settings["follow_up_seconds"]) if self.session else self._end_session()
                return
            self._utterance(text)
        elif kind == "timeout":
            if ev[1] == self.listen_id:
                from .tools.browser import browsing_active
                if browsing_active():
                    self._listen(30)
                else:
                    self._end_session()
        elif kind == "greet":
            bus.mode.emit("focus")
            bus.state.emit("speaking")
            self._say_and_wait(ev[1])
            if self.pending_q:
                self.session = True
                self._listen(10)
            else:
                time.sleep(0.4)
                bus.mode.emit("hidden")
                bus.state.emit("idle")
        elif kind == "notify":
            if self.session or (self.brain and self.brain.busy):
                bus.status.emit(ev[1])  # en mitad de una conversación: solo se muestra
                return
            bus.mode.emit("corner")
            bus.state.emit("speaking")
            bus.status.emit("Notificación")
            self._say_and_wait(ev[1])
            # ventana breve para responder («léemelo») sin decir «Jarvis»
            self.session = True
            self._listen(6)
        elif kind == "say":
            show = not self.session
            if show:
                bus.mode.emit("corner")
                bus.state.emit("speaking")
            bus.status.emit("Aviso")
            self._say_and_wait(ev[1])
            if show:
                bus.mode.emit("hidden")
                bus.state.emit("idle")
        elif kind == "routine":
            name, actions, announce = ev[1], ev[2], ev[3]
            bus.mode.emit("corner")
            bus.state.emit("acting")
            bus.status.emit(f"Rutina: {name}")
            if announce:
                self.speaker.say(announce)
            for a in actions:
                bus.status.emit(tools.label_for(a.get("tool", ""), a.get("args", {})))
                tools.execute(a.get("tool", ""), a.get("args", {}))
            self.speaker.wait()
            time.sleep(1.5)
            if not self.session:
                bus.mode.emit("hidden")
                bus.state.emit("idle")
        elif kind == "intro":
            from .experience import run_intro
            self.speaker.stop()
            self.session = True
            run_intro(self.speaker)
            bus.mode.emit("focus")
            self._listen(15)
        elif kind == "update_available" and settings["auto_update"]:
            info = ev[1]
            if self.session or (self.brain and self.brain.busy):
                self.pending_update = info  # al terminar la conversación
                return
            notes = re.sub(r"[#*`>-]+", " ", info["notes"])
            notes = re.sub(r"\s+", " ", notes).strip()[:400]
            bus.mode.emit("focus")
            bus.state.emit("speaking")
            self._say_and_wait(f"Hey, {settings['address']}. Tengo una actualización nueva disponible, la versión {info['version']}. "
                               + (f"Incluye lo siguiente: {notes}. " if notes else "")
                               + "Me actualizo ahora mismo y vuelvo en unos segundos.")
            bus.state.emit("acting")
            bus.status.emit(f"Actualizando a la versión {info['version']}…")
            if not self.updater.apply(info):
                self._say_and_wait("Vaya, no he podido actualizarme. Lo intentaré más tarde.")
                bus.mode.emit("hidden")
                bus.state.emit("idle")
        elif kind == "update_available":
            info = ev[1]
            notes = re.sub(r"[#*`>-]+", " ", info["notes"])
            notes = re.sub(r"\s+", " ", notes).strip()[:300]

            def yes():
                self.speaker.say("Perfecto. Me actualizo y vuelvo en unos segundos.")
                self.speaker.wait()
                bus.mode.emit("corner")
                bus.state.emit("acting")
                bus.status.emit("Actualizando…")
                if not self.updater.apply(info):
                    self.speaker.say("No he podido actualizar. Lo intentaré más tarde.")
            self.pending_q = {"text": f"Por cierto, {settings['address']}: hay una actualización disponible, la versión "
                                      f"{info['version']}. {('Incluye: ' + notes + '. ') if notes else ''}¿Quieres que me actualice ahora?",
                              "yes": yes, "no": lambda: self.speaker.say("De acuerdo, te lo recuerdo más tarde."),
                              "asked": False}
            if not self.session:
                self.events.put(("ask_pending",))
        elif kind == "ask_pending":
            if self.pending_q and not self.pending_q["asked"]:
                self.session = True
                bus.mode.emit("focus")
                bus.state.emit("speaking")
                self.pending_q["asked"] = True
                self._say_and_wait(self.pending_q["text"])
                self._listen(10)
        elif kind == "brain_done":
            if ev[1] == self.task_id:
                self._after_task()
        elif kind == "check_update":
            info = self.updater.check(force=True)
            if info:
                self.events.put(("update_available", info))
            else:
                self.tray.showMessage("Jarvis", f"Estás en la última versión ({VERSION}).", QSystemTrayIcon.Information, 5000)

    def _on_wake(self, source: str, rest: str) -> None:
        if self.brain is None:
            return
        if self.brain.busy:
            self.brain.cancel.set()
        self.task_id += 1
        self.speaker.stop()
        self.session = True
        bus.mode.emit("focus")
        bus.state.emit("listening")
        bus.subtitle.emit("")
        memory.event("wake", source)
        if not memory.get("intro_done") and source != "typed":
            self.events.put(("intro",))
            return
        if settings["duck_media"]:
            from . import media
            threading.Thread(target=media.duck, daemon=True).start()
        if source != "barge":
            self.speaker.play_chime()
        if rest and (source == "typed" or len(rest.split()) >= 2):
            self._utterance(rest)
            return
        seeded = self.audio._seed is not None
        if not seeded and self.greeter:
            hello = self.greeter.take()
            if hello:
                bus.state.emit("speaking")
                self._say_and_wait(hello)
        self._listen(settings["first_listen_seconds"])

    def _utterance(self, text: str) -> None:
        from .tools.browser import browser, browsing_active
        bus.transcript.emit(text)
        log.info("usuario: %s", text)
        # 1) respuesta a una pregunta pendiente (propuesta de rutina, actualización)
        if self.pending_q and self.pending_q.get("asked"):
            q = self.pending_q
            y, n = bool(YES.search(text)), bool(NO.search(text))
            if y != n and len(text.split()) <= 8:
                self.pending_q = None
                (q["yes"] if y else q["no"])()
                self.speaker.wait()
                self._listen(settings["follow_up_seconds"])
                return
            self.pending_q = None
        # 2) despedidas / cancelar
        if CANCEL.match(text):
            self._say_and_wait(random.choice(["Como quieras.", "Aquí estaré.", f"Cuando quieras, {settings['address']}."]))
            self._end_session()
            return
        # 3) elegir en lo que se está recorriendo ("ese")
        if browsing_active():
            if STOP_SCROLL.match(text):
                browser.scroll = None
                if browser.browse:
                    browser.browse["next"] = time.time() + 3600
                self.speaker.say("Parado.")
                self.speaker.wait()
                self._listen(20)
                return
            if SELECT.match(text) and browser.browse:
                bus.mode.emit("corner")
                bus.state.emit("acting")
                bus.status.emit("Abriendo el vídeo elegido")
                self.speaker.say(random.choice(["Excelente elección.", "Ahí va.", "Marchando."]))
                r = tools.execute("youtube", {"action": "select_highlighted"})
                bus.status.emit(r[:80])
                self.speaker.wait()
                self._listen(settings["follow_up_seconds"] + 5)
                return
        # 4) rutinas por frase
        if self.scheduler.phrase_routine(text):
            self._listen(settings["follow_up_seconds"])
            return
        # 5) el cerebro
        bus.state.emit("thinking")
        self.step_idx = 0
        self.plan_len = 0
        self.task_id += 1
        threading.Thread(target=self._run_brain, args=(text, self.task_id), name="brain", daemon=True).start()

    def _run_brain(self, text: str, tid: int) -> None:
        moved = {"corner": False}

        def say(s: str) -> None:
            self.speaker.say(s)

        def on_tool(name: str, args: dict, label: str) -> None:
            if not moved["corner"]:
                moved["corner"] = True
                bus.mode.emit("corner")
            bus.state.emit("acting")
            bus.status.emit(label)
            if name == "plan":
                self.plan_len = len(args.get("steps", []) or [])
                self.step_idx = 0
                return
            if self.plan_len:
                if self.step_idx > 0:
                    bus.step.emit(self.step_idx - 1, "done")
                if self.step_idx < self.plan_len:
                    bus.step.emit(self.step_idx, "active")
                self.step_idx = min(self.step_idx + 1, self.plan_len)

        def on_done(name: str, result: str) -> None:
            if result.startswith("ERROR"):
                bus.status.emit("Hubo un problema; buscando otra vía")
            bus.state.emit("thinking")

        try:
            self.brain.run(text, say, on_tool, on_done)
        finally:
            for i in range(self.plan_len):
                bus.step.emit(i, "done")
            self.events.put(("brain_done", tid))

    def _after_task(self) -> None:
        bus.state.emit("speaking")
        self.speaker.wait()
        if not self.session:
            return
        from .learning import detector
        from .tools.browser import browsing_active
        # momento natural para proponer una rutina aprendida
        if not self.pending_q and time.time() - self.last_proposal_ask > 6 * 3600:
            p = detector.next_proposal()
            if p:
                self.last_proposal_ask = time.time()

                def yes(p=p):
                    name = detector.answer(p, True)
                    self.speaker.say(f"Hecho. He creado la rutina «{name}». Puedes pedirme que la quite cuando quieras.")

                def no(p=p):
                    detector.answer(p, False)
                    self.speaker.say("Entendido. No volveré a preguntarlo.")
                self.pending_q = {"text": f"Por cierto, {settings['address']}: he notado que {p['text'].split(' (')[0]}. "
                                          f"¿Quieres que lo haga yo automáticamente?", "yes": yes, "no": no, "asked": True}
                self._say_and_wait(self.pending_q["text"])
        if self.pending_q and not self.pending_q.get("asked"):
            self.pending_q["asked"] = True
            self._say_and_wait(self.pending_q["text"])
        self._listen(30 if browsing_active() else settings["follow_up_seconds"])

    def _routine_actions(self, name: str, actions: list, announce: str) -> None:
        self.events.put(("routine", name, actions, announce))


def single_instance() -> bool:
    import win32api
    import win32event
    import winerror
    from . import updater
    for _ in range(90):
        h = win32event.CreateMutex(None, False, "JarvisAssistant.Singleton")
        if win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS:
            updater.MUTEX = h
            return True
        if "--after-update" not in sys.argv:
            if "--command" in sys.argv and sys.argv.index("--command") + 1 < len(sys.argv):
                (DATA / "pending_command.txt").write_text(sys.argv[sys.argv.index("--command") + 1], encoding="utf-8")
            try:
                ev = win32event.OpenEvent(win32event.EVENT_MODIFY_STATE, False, "JarvisAssistant.Activate")
                win32event.SetEvent(ev)
            except Exception:
                pass
            return False
        win32api.CloseHandle(h)
        time.sleep(0.5)
    log.warning("otra instancia no se cerró a tiempo")
    return False


def main() -> int:
    from .config import setup_logging
    setup_logging()
    log.info("Jarvis %s arrancando (%s) datos=%s", VERSION, "exe" if FROZEN else "dev", DATA)
    if not single_instance():
        return 0
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName("Jarvis")
    j = Jarvis(qapp, sys.argv[1:])  # noqa: F841
    return qapp.exec()
