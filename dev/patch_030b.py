def patch(p, pairs):
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert a in s, (p, a[:70])
        s = s.replace(a, b, 1)
    open(p, "w", encoding="utf-8").write(s)


# quitar el look_at_screen antiguo de system.py (ahora vive en vision.py)
s = open("jarvis/tools/system.py", encoding="utf-8").read()
i = s.index("# ------------------------------------------------------------------ visión")
open("jarvis/tools/system.py", "w", encoding="utf-8").write(s[:i].rstrip() + "\n")

patch("jarvis/tools/__init__.py", [
    ("from . import system, browser, docs, assistant, report  # noqa: F401",
     "from . import system, browser, docs, assistant, report, vision  # noqa: F401"),
])
patch("jarvis/bus.py", [
    ("    intro = Signal(str)", "    capture = Signal(bool)         # ocultar la interfaz un instante para capturar la pantalla\n    intro = Signal(str)"),
])
patch("jarvis/ui/overlay.py", [
    ("        bus.intro.connect(self.set_intro)",
     "        bus.intro.connect(self.set_intro)\n        bus.capture.connect(lambda on: self.setWindowOpacity(0.0 if on else 1.0))"),
])
patch("jarvis/config.py", [
    ('''    "model": "qwen3:8b",           # cerebro principal (herramientas)
    "vision_model": "qwen3.5:4b",  # para "mira mi pantalla"''',
     '''    "model": "qwen3.5:9b",         # cerebro principal: herramientas + visión (ve la pantalla)
    "vision_model": "",            # vacío = el mismo modelo (si el cerebro no tiene visión, poner uno que sí)'''),
    ('''        self.data["settings_version"] = 2''',
     '''        if v < 3:
            # 0.3.0: el cerebro pasa a qwen3.5 (con visión) si se había elegido automáticamente
            if self.data.get("model") == "qwen3:8b":
                self.data["model"] = DEFAULTS["model"]
            if self.data.get("vision_model") == "qwen3.5:4b":
                self.data["vision_model"] = ""
        self.data["settings_version"] = 3'''),
])
patch("jarvis/installer.py", [
    ('''    if v >= 7500:
        return "qwen3:8b"''', '''    if v >= 7500:
        return "qwen3.5:9b"'''),
])
patch("jarvis/brain.py", [
    ('''- Si te cuenta algo duradero sobre él (gustos, nombres, rutinas, cómo quiere que le hables), llama a `remember` y después contesta con naturalidad.''',
     '''- Si te cuenta algo duradero sobre él («me encanta X», «soy de X», «mi novia se llama X», «trabajo en X», cómo quiere que le hables), llama a `remember` y después contesta con naturalidad.
- PANTALLA: si su mensaje trae una captura, es su pantalla en este momento (el círculo rojo «RATÓN» es el puntero). Úsala para entender «esto», «ahí», «ese botón». Para pulsar cualquier cosa de cualquier programa usa `screen_click` describiendo el elemento tal como se ve. Sin captura y si necesitas ver algo, usa `look_at_screen`.'''),
    ('''        messages = self._base_messages(user_text)
        self._hist_len''', '''        messages = self._base_messages(user_text)
        if settings["screen_context"] and DEICTIC.search(user_text):
            try:
                from .tools.vision import capture
                b64, _, _ = capture(mark_cursor=True)
                messages[-1]["images"] = [b64]
                messages[-1]["content"] += "\\n(Adjunto: captura de mi pantalla ahora mismo.)"
            except Exception:
                log.exception("captura de contexto")
        self._hist_len'''),
    ('''        for m in messages:
            m = dict(m)''', '''        for m in messages:
            m = dict(m)
            m.pop("images", None)'''),
    ('''ACTION_RE = re.compile(''', '''from .tools.vision import DEICTIC  # noqa: E402

ACTION_RE = re.compile('''),
])
patch("jarvis/config.py", [
    ('''    "keyword_wake": True,''', '''    "keyword_wake": True,
    "screen_context": True,        # adjuntar la pantalla cuando la orden habla de «esto», «ahí», «clica»…'''),
])
patch("jarvis/tools/browser.py", [
    ('''"play = buscar y reproducir el mejor resultado ('pon X'). ''',
     '''"play = buscar y reproducir el mejor resultado (SIEMPRE que diga 'pon X' o 'ponme X'). '''),
])
print("ok")
