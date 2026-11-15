def patch(p, pairs):
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert a in s, (p, a[:70])
        s = s.replace(a, b, 1)
    open(p, "w", encoding="utf-8").write(s)


patch("jarvis/tools/__init__.py", [
    ("from . import system, browser, docs, assistant, report, vision  # noqa: F401",
     "from . import system, browser, docs, assistant, report, vision, accounts  # noqa: F401"),
])
patch("jarvis/config.py", [
    ('''    "keyword_wake": True,''', '''    "keyword_wake": True,
    "notifications": True,         # avisar de WhatsApp / móvil (sin leer el contenido)
    "mail_accounts": [],           # se rellena solo al revisar el correo con sesión iniciada
    "calendar_account": "",
    "city": "",'''),
])
patch("jarvis/tools/assistant.py", [
    ('''      {"key": S("Ajuste", ["user_name", "address", "follow_up_seconds", "end_silence_ms", "show_subtitles", "corner", "model",
                           "wake_threshold", "greet_on_start"]),''',
     '''      {"key": S("Ajuste", ["user_name", "address", "city", "follow_up_seconds", "end_silence_ms", "show_subtitles", "corner",
                           "model", "wake_threshold", "greet_on_start", "notifications", "barge_in"]),'''),
])
patch("jarvis/brain.py", [
    ('''- Si te cuenta algo duradero sobre él''',
     '''- Correo, agenda, Drive: `email_check`, `calendar_today`, `drive_search` (usan tu navegador; si no ha iniciado sesión, díselo). «¿Qué tengo hoy?», «resumen del día», «buenos días» → `daily_briefing` y cuéntalo en 3-5 frases naturales.
- Notificaciones: anuncia de quién son, pero NO leas el contenido de mensajes privados salvo que él te lo pida («léemelo», «¿qué dice?») → `notifications` con read_content=true.
- Si te dice su ciudad, guárdala con `settings` (key=city) además de `remember`.
- Si te cuenta algo duradero sobre él'''),
])
patch("jarvis/app.py", [
    ('''        ActivityWatcher().start()''',
     '''        ActivityWatcher().start()
        from .notifications import NotificationWatcher
        NotificationWatcher(lambda t: self.events.put(("notify", t))).start()'''),
    ('''        elif kind == "say":''',
     '''        elif kind == "notify":
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
        elif kind == "say":'''),
])
print("ok")
