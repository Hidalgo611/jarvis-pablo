"""Momentos especiales: presentación cinematográfica (primer «Hey Jarvis») y saludos con iniciativa."""
from __future__ import annotations

import logging
import threading
import time

from .brain import ago, diary, last_interaction
from .bus import bus
from .config import VERSION, settings
from .memory import memory

log = logging.getLogger("experience")

INTRO_SONG = "https://www.youtube.com/watch?v=pAgnJDJN4VA"   # AC/DC - Back In Black (vídeo oficial)
INTRO_SONG_SEARCH = "AC/DC Back In Black official video"


def intro_script() -> str:
    u = settings["user_name"]
    return (f"Hey, {u}. ¿Qué tal? Soy Jarvis, tu asistente personal. "
            f"¿Estás preparado para realizar cualquier tipo de función o modificación dentro de tu ordenador? "
            f"Estaré disponible para todo lo que quieras: solo tienes que llamarme. "
            f"¿Qué deseas hacer? Aunque antes, cuéntame: ¿cómo te ha ido hoy? ¿Qué has hecho?")


def run_intro(speaker) -> None:
    """Pantalla en negro, Back in Black de fondo, arranque de sistemas y presentación. Bloquea hasta terminar."""
    from .tools.browser import browser
    bus.intro.emit("start")
    bus.state.emit("intro")
    music = {"ok": False}

    def start_music():
        try:
            music["ok"] = browser.call(browser._music_start, INTRO_SONG, INTRO_SONG_SEARCH, timeout=40)
        except Exception:
            log.exception("música de la presentación")
    th = threading.Thread(target=start_music, daemon=True)
    th.start()
    lines = [f"Inicializando núcleo J.A.R.V.I.S. v{VERSION}", "Cargando matriz de voz ............ OK",
             "Calibrando reconocimiento auditivo  OK", "Conectando red neuronal local ..... OK",
             "Cifrando canal privado ............ OK", f"Identificando usuario ............. {settings['user_name'].upper()}",
             "Todos los sistemas en línea."]
    for ln in lines:
        bus.intro.emit("line:" + ln)
        time.sleep(0.75)
    th.join(timeout=15)
    keep = {"on": music["ok"]}

    def keeper():  # durante toda la presentación: anuncios en silencio, canción con sonido
        while keep["on"]:
            try:
                browser.call(browser._music_state, timeout=5)
            except Exception:
                pass
            time.sleep(0.5)
    threading.Thread(target=keeper, daemon=True).start()
    # espera a que la música suene de verdad (sin anuncio) hasta 15 s más
    t0 = time.time()
    while music["ok"] and time.time() - t0 < 15:
        try:
            st = browser.call(browser._music_state, timeout=5)
        except Exception:
            break
        if st.get("playing") and not st.get("ad") and st.get("t", 0) > 0.3:
            log.info("música sonando (%.1fs)", time.time() - t0)
            break
        time.sleep(0.4)
    log.info("presentación: música=%s", music["ok"])
    bus.intro.emit("title")
    time.sleep(6.5 if music["ok"] else 2.5)   # que se luzca el riff
    if music["ok"]:
        _music_fade(0.22, 1.2)
    speaker.say(intro_script())
    speaker.wait(90)
    if music["ok"]:
        _music_fade(0.0, 3.5)
        try:
            browser.call(browser._music_stop, timeout=10)
        except Exception:
            pass
    keep["on"] = False
    memory.set("intro_done", True)
    bus.intro.emit("end")


def _music_fade(target: float, seconds: float) -> None:
    from .tools.browser import browser
    try:
        cur = browser.call(browser._music_state, timeout=5).get("vol", 1.0)
    except Exception:
        return
    steps = max(1, int(seconds / 0.15))
    for i in range(1, steps + 1):
        v = cur + (target - cur) * i / steps
        try:
            browser.call(browser._music_volume, v, timeout=5)
        except Exception:
            return
        time.sleep(0.15)


class Greeter:
    """Prepara en segundo plano el saludo con iniciativa (p.ej. «¿Qué tal en el trabajo?») para decirlo al instante."""

    MIN_GAP = 40 * 60

    def __init__(self, brain, is_busy) -> None:
        self.brain = brain
        self.is_busy = is_busy
        self.cache: tuple[float, str] | None = None
        threading.Thread(target=self._loop, name="greeter", daemon=True).start()

    def needed(self) -> bool:
        last = last_interaction()
        return bool(last) and time.time() - last >= self.MIN_GAP

    def _prompt(self) -> str:
        return ("(Instrucción interna: Pablo acaba de llamarte después de un rato. Salúdale tú primero, con naturalidad, "
                "en DOS frases cortas como máximo (unas 25 palabras), tomando la iniciativa según la hora y lo último que te contó "
                "(por ejemplo, pregúntale qué tal le fue). Termina ofreciéndote a ayudar. No uses herramientas.)")

    def _generate(self) -> str:
        txt = self.brain.quick(self._prompt(), max_tokens=55)
        txt = txt.replace("\n", " ").strip()
        return txt if 8 < len(txt) < 300 and "<" not in txt else ""

    def _loop(self) -> None:
        while True:
            time.sleep(90)
            try:
                if self.is_busy() or not self.needed() or not diary():
                    continue
                last = last_interaction()
                if self.cache and self.cache[0] > last and time.time() - self.cache[0] < 25 * 60:
                    continue
                txt = self._generate()
                if txt:
                    self.cache = (time.time(), txt)
                    log.info("saludo preparado: %s", txt)
            except Exception:
                log.exception("greeter")

    def take(self) -> str | None:
        """Saludo para este momento (o None si acabamos de hablar)."""
        if not self.needed():
            return None
        last = last_interaction()
        if self.cache and self.cache[0] > last and time.time() - self.cache[0] < 25 * 60:
            txt = self.cache[1]
            self.cache = None
            return txt
        if diary():
            txt = self._generate()
            if txt:
                return txt
        h = time.localtime().tm_hour
        hola = "Buenos días" if 6 <= h < 14 else ("Buenas tardes" if 14 <= h < 21 else "Buenas noches")
        return f"{hola}, {settings['address']}. ¿Qué tal todo? ¿En qué te ayudo?"
