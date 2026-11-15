p = "jarvis/brain.py"
s = open(p, encoding="utf-8").read()


def rep(old, new):
    global s
    assert old in s, old[:60]
    s = s.replace(old, new, 1)


rep('''TOOL_TAG = re.compile(''', '''ACTION_RE = re.compile(r"\\b(abre|abrir|ábreme|cierra|cerrar|pon|ponme|ponlo|quita|sube|baja|busca|búscame|buscar|haz|hazme|crea|"
                       r"escribe|reproduce|para|pausa|apaga|reinicia|bloquea|cambia|activa|desactiva|silencia|mete|métete|"
                       r"entra|ve a|llévame|navega|descarga|guarda|exporta|recuérdame|programa|enséñame|muéstrame|mira|"
                       r"salta|avanza|retrocede|minimiza|maximiza|captura|vacía|elimina|borra|manda|envía|lee|"
                       r"selecciona|clica|haz clic|pulsa)\\b", re.I)
CLAIM_RE = re.compile(r"\\b(hecho|listo|abiert[oa]|cerrad[oa]|puesto|ya está|he abierto|he puesto|he cerrado|"
                      r"he buscado|en marcha|reproduciendo|ahí lo tienes|ahí tienes)\\b", re.I)

TOOL_TAG = re.compile(''')

# en peticiones de acción, el primer turno no se pronuncia hasta saber si ha usado herramientas
rep('''        buf = {"t": "", "think": False}

        def on_text(chunk: str) -> None:
            nonlocal spoken_any
            buf["t"] += chunk''', '''        buf = {"t": "", "think": False, "hold": bool(ACTION_RE.search(user_text))}

        def on_text(chunk: str) -> None:
            nonlocal spoken_any
            buf["t"] += chunk
            if buf["hold"]:
                return''')

rep('''                rest = re.sub(r"<think>.*?</think>", "", buf["t"], flags=re.S).strip()
                if rest and "<think>" not in rest and not TOOL_TAG.search(rest):
                    say(rest)
                    spoken_any = True''', '''                if buf["hold"]:
                    buf["hold"] = False
                    if not calls and CLAIM_RE.search(content) and step == 0:
                        # dice que lo ha hecho sin usar ninguna herramienta: no se dice y se le obliga a actuar
                        log.warning("acción inventada evitada: %r", content[:120])
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "user", "content": "(Sistema: NO has ejecutado ninguna herramienta, así que "
                                         "no has hecho nada. Llama ahora a la herramienta adecuada para lo que se pidió.)"})
                        continue
                rest = re.sub(r"<think>.*?</think>", "", buf["t"], flags=re.S).strip()
                if rest and "<think>" not in rest and not TOOL_TAG.search(rest):
                    for sent in ([rest] if len(rest) < 120 else __import__("jarvis.tts", fromlist=["x"]).split_sentences(rest)):
                        say(sent)
                    spoken_any = True''')

rep('''- Tras una acción sencilla confirma en pocas palabras ("Hecho."), sin preguntas de relleno.''',
    '''- Si te pide hacer algo, tu respuesta tiene que ser una LLAMADA A HERRAMIENTA. Solo después de ver el resultado confirmas en pocas palabras, sin preguntas de relleno. Jamás digas "hecho" sin haber llamado a la herramienta.''')
open(p, "w", encoding="utf-8").write(s)
print("ok")
