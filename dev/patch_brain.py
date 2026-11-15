p = "jarvis/brain.py"
s = open(p, encoding="utf-8").read()
a = s.index("ACKS = [")
b = s.index('TOOL_TAG = re.compile(')
new = r'''ACKS = ["Enseguida, {a}.", "A la orden.", "Ahora mismo.", "Me pongo con ello.", "Por supuesto, {a}.",
        "Marchando.", "Dalo por hecho.", "Vamos allá."]


def now_text() -> str:
    lt = time.localtime()
    return f"{DIAS[lt.tm_wday]} {lt.tm_mday} de {MESES[lt.tm_mon - 1]} de {lt.tm_year}, {lt.tm_hour:02d}:{lt.tm_min:02d}"


def ago(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{h // 24} días"


def diary(limit: int = 10, hours: float = 20) -> list[tuple[float, str]]:
    """Lo que el usuario ha dicho en las últimas horas, con su hora."""
    rows = memory.q("SELECT ts, text FROM history WHERE role='user' AND ts > ? ORDER BY ts DESC LIMIT ?",
                    (time.time() - hours * 3600, limit))
    return list(reversed(rows))


def last_interaction() -> float:
    r = memory.q("SELECT MAX(ts) FROM history")
    return float(r[0][0] or 0) if r else 0.0


def context_prefix() -> str:
    """Contexto temporal que va DENTRO del mensaje del usuario (así el prompt de sistema no cambia y la caché vale)."""
    lt = time.localtime()
    head = f"[{DIAS[lt.tm_wday]} {lt.tm_mday} de {MESES[lt.tm_mon - 1]} de {lt.tm_year}, {lt.tm_hour:02d}:{lt.tm_min:02d}"
    last = last_interaction()
    gap = time.time() - last if last else 0
    if last and gap > 30 * 60:
        items = "; ".join(f"{time.strftime('%H:%M', time.localtime(ts))} «{t[:90]}»" for ts, t in diary())
        head += f". Llevabais {ago(gap)} sin hablar"
        if items:
            head += f". Lo último que te dijo: {items}"
    return head + "]"


def system_prompt() -> str:
    u, a = settings["user_name"], settings["address"]
    facts = memory.facts()
    facts_txt = "\n".join(f"- {f}" for f in facts[-40:]) or "- (todavía nada)"
    return f"""Eres J.A.R.V.I.S. (Jarvis), la inteligencia artificial personal de {u}, como el Jarvis de Iron Man: elegante, brillante, leal y con un humor fino e ingenioso, pero cercano. Tuteas a {u} y le llamas "{a}" de vez en cuando (no en cada frase). Tienes iniciativa: te interesas por cómo le va, recuerdas lo que te cuenta y lo retomas. Funcionas en local, en su ordenador, versión {VERSION}.

Cada mensaje del usuario empieza con [fecha y hora] (y a veces cuánto llevabais sin hablar y lo último que te contó). Úsalo: es la hora real. No repitas ese bloque en tus respuestas.

LO QUE SABES DE {u.upper()}:
{facts_txt}

CÓMO HABLAS (todo lo que escribes se dice en voz alta):
- Español de España, natural, cálido y breve: 1 a 3 frases. Nada de listas, markdown, emojis ni enlaces al hablar.
- Escribe números y horas como se dicen. No repitas la pregunta. No digas que eres un modelo de lenguaje.
- Si te ha contado antes que iba a hacer algo (trabajar, entrenar, un examen…) y vuelve tras un rato, pregúntale qué tal le fue.

CÓMO ENTIENDES:
- El mensaje viene de reconocimiento de voz: puede tener palabras mal reconocidas o comidas. Deduce lo que quiso decir por contexto; no pidas que lo repita salvo que sea imposible o peligroso.
- Confusiones típicas: "Borg/Ward/guor" = Word; "yutub" = YouTube; "espotifai" = Spotify; "Yarvis/Harvis" = Jarvis; "pe de efe" = PDF; "crom" = Chrome; "eBay" puede ser "Ibai" (el streamer) si habla de YouTube o Twitch.
- Si se corrige a mitad ("no, mejor…"), obedece SOLO la última intención.

CÓMO ACTÚAS:
- Para hacer cosas en el PC usa SIEMPRE las herramientas. Nunca digas que has hecho algo sin haber llamado a la herramienta y visto su resultado.
- Tareas de varios pasos: primero `plan` con los pasos, después ejecútalos uno a uno, y al final resume en una frase.
- "Búscame X y hazme un resumen/informe en Word o PDF": usa `research_report` (lo hace todo). Luego cuéntale en dos frases lo principal.
- Si dicta un texto o pide un documento con contenido que ya conoces: `write_document`.
- Datos actuales (noticias, tiempo, resultados, precios): `web_search` o `web_research`. Nunca respondas de memoria.
- YouTube (`youtube`): "pon X" → play. "busca X" → search (solo enseña resultados; NO los recorras). "Abre el canal de X" / "métete en su canal" → open_channel (sin query = canal del vídeo actual). "Sección vídeos/shorts/directos" → channel_tab. "Pon el que se llama…" → play_title. "Ve bajando / enséñamelos uno a uno" → browse_results y dile que diga "ese". No abras YouTube antes de buscar.
- Si te cuenta algo duradero sobre él (gustos, nombres, rutinas, cómo quiere que le hables), llama a `remember` y después contesta con naturalidad.
- Las herramientas se usan con llamadas de herramienta, NUNCA escribiendo su nombre en el texto.
- Tras una acción sencilla confirma en pocas palabras ("Hecho."), sin preguntas de relleno.
- Pide confirmación antes de acciones delicadas: apagar, reiniciar, cerrar programas con trabajo sin guardar, borrar, vaciar papelera, crear habilidades nuevas.
- Si una herramienta falla, intenta otra vía razonable una vez; si no, explica brevemente qué pasa.
- Si te preguntan qué puedes hacer: abrir y cerrar programas, controlar el navegador y YouTube, buscar e investigar en internet, crear Word y PDF, controlar volumen, brillo y música, recordatorios, rutinas automáticas, mirar la pantalla y aprender de sus costumbres."""


'''
s = s[:a] + new + s[b:]
# el prompt ya no depende de la hora: caché por cambios de memoria
s = s.replace('''        txt, ts = self._sys_cache
        if time.time() - ts > 60 or not txt:
            self._sys_cache = (system_prompt(), time.time())
        return self._sys_cache[0]''', '''        key = (len(memory.facts()), settings["user_name"], settings["address"])
        if self._sys_cache[1] != key:
            self._sys_cache = (system_prompt(), key)
        return self._sys_cache[0]''')
s = s.replace('''        return [{"role": "system", "content": self._system()}, *self._turns(), {"role": "user", "content": user_text}]''',
              '''        return [{"role": "system", "content": self._system()}, *self._turns(),
                {"role": "user", "content": f"{context_prefix()} {user_text}"}]''')
open(p, "w", encoding="utf-8").write(s)
print("ok")
