def patch(p, pairs):
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert a in s, (p, a[:70])
        s = s.replace(a, b, 1)
    open(p, "w", encoding="utf-8").write(s)


patch("jarvis/audio.py", [
    ('''        self.on_wake: Optional[Callable[[str, str], None]] = None   # (source, resto_del_comando)''',
     '''        self.on_wake: Optional[Callable[[str, str], None]] = None   # (source, resto_del_comando)
        self.on_barge: Optional[Callable[[str], bool]] = None        # ¿lo oído mientras hablo es el usuario?'''),
    ('''        if self.tts_active or not settings["keyword_wake"]:
            return''',
     '''        if self.tts_active:
            # Interrumpir hablando: cada ~0,8 s con voz, reconoce los últimos 2 s y el conductor decide si es eco o el usuario
            self._kw_tick += 1
            if (settings["barge_in"] and now - self._last_speech < 0.4 and self._kw_tick >= 10 and not self._kw_busy
                    and len(self.ring) >= 10):
                self._kw_tick = 0
                self._kw_busy = True
                win = list(self.ring)[-25:]
                self.asr_q.put(("barge", np.concatenate(win), win))
            return
        if not settings["keyword_wake"]:
            return'''),
    ('''                elif kind == "kw" and self.mode == "wake":''',
     '''                elif kind == "barge" and self.tts_active and self.mode == "wake":
                    text = self.asr(job[1])
                    if text and self.on_barge and self.on_barge(text):
                        log.info("interrupción: %r", text[:80])
                        self._seed = (time.time(), job[2])
                        self.ring.clear()
                        self._fire_wake("barge", "")
                elif kind == "kw" and self.mode == "wake":'''),
    ('''                elif kind == "kw":
                    self._kw_busy = False''',
     '''                elif kind in ("kw", "barge"):
                    self._kw_busy = False'''),
])

patch("jarvis/tts.py", [
    ('''        self.on_active: Optional[Callable[[bool], None]] = None''',
     '''        self.on_active: Optional[Callable[[bool], None]] = None
        from collections import deque
        self.recent: deque = deque(maxlen=4)   # frases que se están diciendo (para distinguir eco de interrupción)'''),
    ('''                if text:
                    bus.subtitle.emit(text)''',
     '''                if text:
                    bus.subtitle.emit(text)
                    self.recent.append((time.time(), text))'''),
])

patch("jarvis/app.py", [
    ('''        self.audio.on_wake = self._interrupt_and_wake''',
     '''        self.audio.on_wake = self._interrupt_and_wake
        self.audio.on_barge = self._is_user_speech'''),
    ('''    def _on_speaking(self, active: bool) -> None:''',
     '''    def _is_user_speech(self, heard: str) -> bool:
        """Mientras Jarvis habla: ¿lo que entra por el micro es el usuario (interrumpe) o el eco de su propia voz?"""
        h = heard.lower()
        if BARGE_WORDS.search(h):
            return True
        words = [w for w in re.findall(r"[a-záéíóúñü]{3,}", h)]
        if len(words) < 2:
            return False
        said = " ".join(t for ts, t in self.speaker.recent if time.time() - ts < 25).lower()
        said_words = set(re.findall(r"[a-záéíóúñü]{3,}", said))
        overlap = sum(w in said_words for w in words) / len(words)
        return overlap < 0.35 and len(words) >= 3

    def _on_speaking(self, active: bool) -> None:'''),
    ('''def make_icon() -> QIcon:''',
     '''BARGE_WORDS = re.compile(r"\\b(para|par[aá]te|espera|calla|c[aá]llate|stop|basta|silencio|oye|jarvis|yarvis|vale vale|"
                         r"no no|d[eé]jalo|un momento)\\b", re.I)


def make_icon() -> QIcon:'''),
    ('''        self.speaker.play_chime()
        if rest and (source == "typed" or len(rest.split()) >= 2):''',
     '''        if source != "barge":
            self.speaker.play_chime()
        if rest and (source == "typed" or len(rest.split()) >= 2):'''),
])
patch("jarvis/config.py", [
    ('''    "keyword_wake": True,''', '''    "keyword_wake": True,
    "barge_in": True,              # poder interrumpir a Jarvis hablando'''),
])
print("ok")
