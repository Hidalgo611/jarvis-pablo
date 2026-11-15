import re

p = "jarvis/app.py"
s = open(p, encoding="utf-8").read()


def rep(old, new, count=1):
    global s
    assert old in s, old[:70]
    s = s.replace(old, new, count)


# --- bandeja: ajustes y presentación
rep('''                ("Ajustes (archivo)", lambda: os.startfile(SETTINGS_PATH)),''',
    '''                ("Ajustes…", self._open_settings),
                ("Ver la presentación", lambda: self.events.put(("intro",))),''')

rep('''    def _typed_command(self) -> None:''', '''    def _open_settings(self) -> None:
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

    def _typed_command(self) -> None:''')

# --- saludo de arranque
rep('''        msg = announce_if_updated()
        if not msg and (first_run or not memory.get("introduced")):
            memory.set("introduced", True)
            msg = (f"Hola, {u}. Soy JARVIS, su asistente personal. Todos mis sistemas funcionan en su ordenador. "
                   f"Para llamarme, diga «Hey Jarvis» o pulse control, alt y jota. Puedo abrir programas, buscar en internet, "
                   f"preparar documentos, controlar YouTube y aprender de sus costumbres. ¿En qué puedo ayudarle?")
        elif not msg and settings["greet_on_start"] and "--quiet" not in self.args:
            msg = f"{greeting_word()}, {a}. Sistemas en línea."''',
    '''        from .experience import Greeter
        self.greeter = Greeter(self.brain, lambda: self.session or self.brain.busy)
        msg = announce_if_updated()
        if not msg and not memory.get("intro_done"):
            # la presentación épica llega con el primer «Hey Jarvis»
            self.tray.showMessage("J.A.R.V.I.S.", f"Todo listo, {u}. Di «Hey Jarvis» para empezar.",
                                  QSystemTrayIcon.Information, 12000)
        elif not msg and settings["greet_on_start"] and "--quiet" not in self.args:
            msg = f"{greeting_word()}, {a}. Sistemas en línea."''')

# --- comprobación periódica de actualizaciones
rep('''    def _check_update_bg(self) -> None:
        while True:
            info = self.updater.check() if self.updater else None
            if info:
                self.events.put(("update_available", info))
                return
            time.sleep(6 * 3600)''', '''    def _check_update_bg(self) -> None:
        time.sleep(20)
        while True:
            info = self.updater.check(force=True) if self.updater else None
            if info:
                self.events.put(("update_available", info))
                return
            time.sleep(max(0.25, float(settings["update_check_hours"])) * 3600)''')

# --- fin de sesión: restaurar volumen y actualización pendiente
rep('''    def _end_session(self) -> None:
        self.session = False
        self.listen_id += 1
        self.audio.stop_listening()
        bus.state.emit("idle")
        bus.mode.emit("hidden")
        bus.transcript.emit("")''', '''    def _end_session(self) -> None:
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
            self.events.put(("update_available", info))''')
rep('''        self.task_id = 0
        self.speaker = self.audio''', '''        self.task_id = 0
        self.pending_update = None
        self.greeter = None
        self.speaker = self.audio''')

# --- final: quitar «Jarvis» del principio
rep('''            text = ev[2].strip()
            if not text:
                self._listen(settings["follow_up_seconds"]) if self.session else self._end_session()
                return
            self._utterance(text)''', '''            raw = ev[2].strip()
            text = strip_wake(raw)
            if not text:
                if raw and self.greeter:  # solo dijo «Jarvis»: saludo y escucho
                    self._say_and_wait(self.greeter.take() or f"Dime, {settings['address']}.")
                self._listen(settings["follow_up_seconds"]) if self.session else self._end_session()
                return
            self._utterance(text)''')

# --- eventos nuevos: presentación y actualización automática
rep('''        elif kind == "update_available":
            info = ev[1]''', '''        elif kind == "intro":
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
            notes = re.sub(r"\\s+", " ", notes).strip()[:400]
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
            info = ev[1]''')

# --- activación: presentación la primera vez, atenuar, saludo con iniciativa
rep('''        self.speaker.play_chime()
        memory.event("wake", source)
        if rest and (source == "typed" or len(rest.split()) >= 2):
            self._utterance(rest)
        else:
            self._listen(settings["first_listen_seconds"])''', '''        memory.event("wake", source)
        if not memory.get("intro_done") and source != "typed":
            self.events.put(("intro",))
            return
        if settings["duck_media"]:
            from . import media
            threading.Thread(target=media.duck, daemon=True).start()
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
        self._listen(settings["first_listen_seconds"])''')

# --- utilidad strip_wake
rep('''def make_icon() -> QIcon:''', '''WAKE_PREFIX = re.compile(r"^\\W*(?:\\S+\\W+){0,4}?(?:hey|oye|ey|eh|hola|vale)?\\W*"
                         r"(jarvis|yarvis|jarvi|harvis|charvis|yarbis|jarbis|garvis|llarvis|jarbys|jarvys)\\b[\\s,.:;!¡¿?]*", re.I)


def strip_wake(text: str) -> str:
    """«Oye Jarvis, abre Spotify» → «abre Spotify». Solo si la palabra está al principio."""
    m = WAKE_PREFIX.match(text)
    return text[m.end():].strip() if m else text


def make_icon() -> QIcon:''')

open(p, "w", encoding="utf-8").write(s)

p = "jarvis/config.py"
s = open(p, encoding="utf-8").read()
rep('''    "keyword_wake": True,''', '''    "keyword_wake": True,
    "duck_media": True,            # bajar el volumen de otras apps mientras escucha
    "auto_update": True,           # instalar actualizaciones en cuanto se detectan (avisando)
    "update_check_hours": 1,''')
open(p, "w", encoding="utf-8").write(s)

p = "jarvis/updater.py"
s = open(p, encoding="utf-8").read()
rep('''        return f"Actualización completada. Ahora estoy en la versión {VERSION}. " + (f"Novedades: {notes}" if notes else "")''',
    '''        from .config import settings
        return (f"Ya estoy de vuelta, {settings['address']}. Me he actualizado a la versión {VERSION}. "
                + (f"Ahora ya tienes esto: {notes}" if notes else ""))''')
open(p, "w", encoding="utf-8").write(s)
print("ok")
