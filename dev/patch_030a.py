def patch(p, pairs):
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert a in s, (p, a[:60])
        s = s.replace(a, b, 1)
    open(p, "w", encoding="utf-8").write(s)


# 1) aviso de Chrome: quitar la bandera no admitida (navigator.webdriver ya se oculta con el init script)
patch("jarvis/tools/browser.py", [
    ('args=["--start-maximized", "--disable-blink-features=AutomationControlled", "--no-first-run",',
     'args=["--start-maximized", "--no-first-run",'),
])

# 2) no leer nunca el bloque de contexto [fecha, hora…]
patch("jarvis/tts.py", [
    ('''    t = re.sub(r"```.*?```", " ", text, flags=re.S)''',
     '''    text = re.sub(r"\\[[^\\]]{0,400}\\]", " ", text)          # bloques [contexto] o [enlaces]
    text = re.sub(r"\\(Contexto[^)]{0,400}\\)", " ", text, flags=re.I)
    t = re.sub(r"```.*?```", " ", text, flags=re.S)'''),
])
patch("jarvis/brain.py", [
    ('''    head = f"[{DIAS[lt.tm_wday]} {lt.tm_mday} de {MESES[lt.tm_mon - 1]} de {lt.tm_year}, {lt.tm_hour:02d}:{lt.tm_min:02d}"''',
     '''    head = f"[contexto, no lo leas: {DIAS[lt.tm_wday]} {lt.tm_mday} de {MESES[lt.tm_mon - 1]} de {lt.tm_year}, {lt.tm_hour:02d}:{lt.tm_min:02d}"'''),
    ('''Cada mensaje del usuario empieza con [fecha y hora] (y a veces cuánto llevabais sin hablar y lo último que te contó). Úsalo: es la hora real. No repitas ese bloque en tus respuestas.''',
     '''Cada mensaje del usuario empieza con un bloque [contexto…] con la fecha y hora reales (y a veces cuánto llevabais sin hablar y lo último que te contó). Es información interna: úsala, pero NUNCA la repitas ni digas la fecha u hora salvo que te las pregunte.'''),
    ('''                content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
                if not calls:''',
     '''                content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
                content = re.sub(r"^\\s*\\[[^\\]]{0,400}\\]\\s*", "", content).strip()
                if not calls:'''),
])
print("ok")
