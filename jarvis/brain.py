"""Cerebro: agente con Ollama (planifica, usa herramientas, habla por frases mientras genera)."""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from typing import Callable

import requests

from . import llm, tools
from .config import VERSION, settings
from .memory import memory

log = logging.getLogger("brain")

# memoria de conversación: turnos completos de las últimas horas (lo más antiguo pasa al resumen)
TURN_WINDOW_H = 3          # horas que se recuerdan palabra por palabra
MAX_TURNS = 14             # turnos (pregunta + respuesta + herramientas) como máximo
FULL_TURNS = 3             # los últimos van completos; los anteriores, sin el detalle de las herramientas
HISTORY_CHARS = 14000      # presupuesto de texto del historial (~4k tokens)

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]

QUICK_TOOLS = {"remember", "forget", "what_you_know", "about", "settings", "list_routines", "system_status",
               "open_app", "close_app", "system", "press_keys", "type_text", "set_reminder", "voices"}

ACKS = ["Enseguida, {a}.", "A la orden.", "Ahora mismo.", "Me pongo con ello.", "Por supuesto, {a}.",
        "Marchando.", "Dalo por hecho.", "Vamos allá."]
# si va a buscar o mirar algo (no a «hacer»), el arranque tiene que sonar a eso
INFO_TOOLS = {"web_search", "web_research", "email_check", "calendar_today", "drive_search", "daily_briefing",
              "notifications", "look_at_screen", "find_files", "research_report"}
INFO_ACKS = ["Déjame mirarlo.", "Un segundo, lo miro.", "Lo compruebo ahora mismo.", "Voy a ver."]
# preguntas de relleno al final («¿Quieres algo más?»): no aportan y cansan; no se dicen
FILLER_RE = re.compile(r"^¿\s*(?:hay\s+)?(?:quieres|necesitas|deseas|te\s+(?:ayudo|puedo|gustar[ií]a)|puedo|algo\s+m[aá]s|"
                       r"en\s+qu[eé]\s+m[aá]s|alguna\s+otra|otra\s+cosa|hay\s+algo)[^?]{0,90}"
                       r"(?:algo\s+m[aá]s|otra\s+cosa|m[aá]s\s+(?:hoy|cosas)|alguna\s+otra\s+cosa|ayud\w*(?:\s+con\s+algo)?\s*(?:m[aá]s)?|"
                       r"en\s+algo\s+m[aá]s|de\s+nuevo|algo\s+(?:hoy|en\s+mente)|para\s+ti)\s*[,]?\s*(?:\w+)?\s*\?$", re.I)


# ofrecimientos que nadie ha pedido («¿Quieres que te cuente…?», «¿Te gustaría…?») tras contestar: sobran.
OFFER_RE = re.compile(r"^¿\s*(quieres que|quieres saber|te gustar[ií]a|necesitas|deseas|te apetece que|prefieres que|"
                      r"hay algo|te ayudo|te pongo|te cuento|qu[eé] te parece si|y si)\b", re.I)
# …salvo si es la confirmación de algo delicado (apagar, borrar…), que sí hay que preguntar
DELICATE_RE = re.compile(r"apag|reinici|suspend|borr|elimin|vac[ií]|cierr|instal|actualic|habilidad|env[ií]|mand|"
                         r"compr|pag|formate|desinstal", re.I)


def is_filler(sentence: str) -> bool:
    s = sentence.strip()
    if FILLER_RE.match(s):
        return True
    return bool(OFFER_RE.match(s)) and s.endswith("?") and not DELICATE_RE.search(s)


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
    # sin el año: un modelo pequeño tiende a repetirlo («¿listo para el domingo de 2026?»)
    head = f"[contexto, no lo leas: {DIAS[lt.tm_wday]} {lt.tm_mday} de {MESES[lt.tm_mon - 1]}, {lt.tm_hour:02d}:{lt.tm_min:02d}"
    h = lt.tm_hour
    head += " (" + ("de madrugada" if h < 6 else "por la mañana" if h < 13 else "a mediodía" if h < 15 else
                    "por la tarde" if h < 21 else "de noche") + ")"
    last = last_interaction()
    gap = time.time() - last if last else 0
    if last and gap > 30 * 60:
        head += f". Llevabais {ago(gap)} sin hablar"
        if gap > TURN_WINDOW_H * 3600:  # la conversación ya no está en los turnos: recuerda lo último que contó
            items = "; ".join(f"{time.strftime('%H:%M', time.localtime(ts))} «{t[:90]}»" for ts, t in diary(6))
            if items:
                head += f". Lo último que te dijo: {items}"
    return head + "]"


# ------------------------------------------------------------------ entender al usuario antes de pensar
NAME_STOP = {"así", "asi", "como", "ahora", "ayer", "hoy", "antes", "luego", "por", "para", "que", "yo", "tú", "tu", "mi",
             "el", "la", "un", "una", "jarvis", "eso", "esto", "bien", "mal", "igual", "otro", "otra", "nada", "señor"}
NAME_RE = re.compile(r"(?:^|[,.;:¡!¿?]\s*|\b(?:no|que|pues|y|yo|vale|oye|hola)\s+)(?:yo\s+)?(me llamo|mi nombre es|ll[aá]mame)\s+"
                     r"([a-záéíóúñü]{2,20})(?=\s*(?:[,.;:!?]|$|y\b|no\b|vale\b|eh\b|jarvis\b))", re.I)
# «no…» al principio (sin ser «no sé», «no puedo»…) o frases de corrección en cualquier sitio
CORRECTION_START = re.compile(r"^\W*(?:no\b(?![\s,]+(?:s[eé]|puedo|quiero|tengo|hay|me apetece|me gusta|est[aá] mal|pasa nada))|"
                              r"que no\b|error\b|incorrecto|mal\b)", re.I)
RECALL_RE = re.compile(r"\b(te (he dicho|dije|cont[eé]|comenté)|recuerdas|te acuerdas|qu[eé] te (dije|he dicho)|"
                       r"lo que te (dije|he dicho|cont[eé]))\b", re.I)
CORRECTION_ANY = re.compile(r"\b(te equivocas|te has equivocado|est[aá]s equivocado|eso no es (as[ií]|verdad|cierto)|no es as[ií]|"
                            r"eso est[aá] mal|lo has entendido mal|te he dicho|ya te lo he dicho|no me has entendido|no he dicho|"
                            r"no te he dicho|no era eso|no es eso|me refer[ií]a|quer[ií]a decir|no me llamo|que me llamo|"
                            r"no me llames)\b", re.I)


def detect_name(text: str) -> tuple[str, str] | None:
    """«No, me llamo Pablo» → ("me llamo", "Pablo"). Solo con nombre en mayúscula y frase clara."""
    t = text.strip()
    m = NAME_RE.search(t)
    if not m:
        return None
    name = m.group(2)
    if name.lower() in NAME_STOP:
        return None
    # el reconocedor pone los nombres con mayúscula; en minúscula solo si la frase es corta y clara («me llamo pablo»)
    if not name[0].isupper() and len(t.split()) > 6:
        return None
    return m.group(1).lower(), name[0].upper() + name[1:].lower()


def apply_name(kind: str, name: str) -> str:
    """Guarda el nombre (o cómo quiere que le llamen) y borra los datos que digan otro nombre."""
    old = settings["user_name"]
    if kind.startswith("ll"):          # «llámame X»: solo cómo le llamo
        settings.set("address", name)
        memory.set_profile("cómo quiere que le llames", name)
        return f"(Nota interna: a partir de ahora llámale «{name}». Ya está guardado.)"
    settings.set("user_name", name)
    settings.set("address", name)
    memory.set_profile("nombre", name)
    for f in memory.facts():
        low = f.lower()
        if ("llama" in low or "nombre" in low) and ("usuario" in low or "él" in low or old.lower() in low or
                                                    low.startswith(("se llama", "me llamo", "su nombre"))):
            if name.lower() not in low:
                memory.forget_fact(f)
    return (f"(Nota interna: se llama {name}. Ya está guardado y corregido en tu memoria: llámale {name} desde ahora "
            f"y nunca de otra forma. Si antes le llamaste de otra manera, discúlpate en pocas palabras.)")


def understand(user_text: str, interrupted: str = "") -> str:
    """Notas internas que ayudan a un modelo pequeño a no perderse (correcciones, interrupciones, nombre)."""
    notes = []
    if interrupted:
        notes.append(f"(Nota interna: te ha interrumpido mientras decías «{interrupted[:160]}». Deja eso y atiende a lo "
                     f"que dice ahora.)")
    nm = detect_name(user_text)
    if nm:
        notes.append(apply_name(*nm))
    elif RECALL_RE.search(user_text) and not CORRECTION_START.search(user_text):
        notes.append("(Nota interna: te pregunta por algo que te dijo antes. La respuesta está en los mensajes anteriores "
                     "de esta conversación o en tus datos: búscala ahí y contéstale con ese dato exacto. Si de verdad no "
                     "está, dile que no lo recuerdas.)")
    elif CORRECTION_START.search(user_text) or CORRECTION_ANY.search(user_text):
        notes.append("(Nota interna: te está corrigiendo o aclarando. Tiene razón: acéptalo sin discutir, discúlpate en "
                     "pocas palabras si te equivocaste y sigue con lo que dice ahora. Si corrige un dato suyo, guárdalo "
                     "con `remember` poniendo el dato viejo en `replaces`.)")
    return " ".join(notes)


# Reglas de cada herramienta: solo van en el prompt cuando esa herramienta se ofrece en esta frase (las herramientas
# van ANTES del prompt en la plantilla de Qwen, así que esto no rompe la caché más de lo que ya la rompen ellas).
TOOL_RULES = {
    "plan": "- Varias acciones seguidas: primero `plan` con los pasos, después ejecútalos uno a uno y resume en una frase.",
    "research_report": "- «Búscame X y hazme un informe/resumen en Word o PDF» → DIRECTAMENTE `research_report` (no uses web_search antes). Luego cuéntale en dos frases lo principal.",
    "write_document": "- Si dicta un texto o pide un documento con contenido que ya conoces: `write_document`.",
    "create_routine": "- AUTOMATIZACIONES: «a partir de ahora / todos los días / cada lunes / cuando diga… haz X» → `create_routine` con `task` = la orden tal como la diría él («abre Gmail») y la hora o la frase. Si falta la hora, pregúntala. «¿Qué rutinas tengo?» → `list_routines`; «quita la rutina…» → `delete_routine`.",
    "youtube": "- YouTube: «pon X», «ponme un vídeo de X» → play (directo, sin preguntar). «busca X» → search (solo enseña resultados). «Abre el canal de X» → open_channel con query=X (y section=vídeos/shorts/directos si la pide); dentro de un canal, «sección vídeos» → channel_tab. «Pon el que se llama…» → play_title. «Enséñamelos uno a uno» → browse_results y dile que diga «ese». «Pantalla completa» → fullscreen.",
    "email_check": "- Correo, agenda, Drive (`email_check`, `calendar_today`, `drive_search`) usan tu navegador; si no ha iniciado sesión, díselo.",
    "daily_briefing": "- «¿Qué tengo hoy?», «resumen del día», «buenos días» → `daily_briefing` y cuéntalo en 3-5 frases naturales.",
    "notifications": "- Notificaciones: di de quién son, pero NO leas mensajes privados salvo que te lo pida («léemelo») → `notifications` con read_content=true.",
    "settings": "- Si te dice su ciudad, guárdala con `settings` (key=city) además de `remember`.",
    "screen_click": "- PANTALLA: si su mensaje trae una captura, es su pantalla ahora (el círculo rojo «RATÓN» es el puntero); úsala para entender «esto», «ahí», «ese botón». Para pulsar algo usa `screen_click` describiendo el elemento como se ve (con window=programa si lo sabes). Lo que ya está abierto se trae con `focus_window`, no pulsando la barra de tareas. Sin captura, si necesitas ver algo: `look_at_screen`.",
}


def system_prompt(tool_names: list | None = None) -> str:
    u, a = settings["user_name"], settings["address"]
    prof = {k: v for k, v in memory.profile().items() if k != "nombre"}
    prof_txt = "".join(f"\n- {k}: {v}" for k, v in prof.items())
    facts = memory.facts()
    facts_txt = "\n".join(f"- {f}" for f in facts[-40:]) or "- (todavía nada)"
    sm = memory.summary()
    sum_txt = ""
    if sm.get("text") and time.time() - sm.get("ts", 0) < 36 * 3600:
        when = "hoy" if sm.get("day") == time.strftime("%Y-%m-%d") else "ayer"
        sum_txt = f"\n\nRESUMEN DE LO QUE HABLASTEIS ANTES ({when}; los mensajes recientes mandan sobre esto):\n{sm['text']}"
    names = set(tool_names or TOOL_RULES)
    rules = "\n".join(r for t, r in TOOL_RULES.items() if t in names)
    return f"""Eres J.A.R.V.I.S. (Jarvis), el asistente personal por voz de {u}, como el Jarvis de Iron Man: elegante, listo, leal, con humor fino y cercano. Tuteas a {u}. Funcionas en su ordenador, versión {VERSION}.

QUIÉN ES:
- Se llama {u}. Llámale "{a}" de vez en cuando (no en cada frase). JAMÁS le llames por otro nombre.{prof_txt}
Datos que te ha contado (úsalos SOLO si tienen que ver con lo que dice ahora; nunca saques tú estos temas):
{facts_txt}{sum_txt}

REGLAS MÁS IMPORTANTES:
1. Responde a lo que te ACABA de decir y sigue SU tema. No saques temas tuyos (fútbol, noticias…) si él no lo hace.
2. Tienes memoria: lo dicho antes en esta conversación es verdad. Si te pregunta algo que ya te dijo, contéstale con eso.
3. Si te corrige («no», «te equivocas», «no me llamo así»), tiene razón: acéptalo en pocas palabras y usa lo nuevo. Nunca insistas.
4. No inventes datos sobre él. Si no lo sabes, pregúntaselo.
5. Si te cuenta lo que hace («me voy al trabajo», «estoy cansado»), reacciona a ESO con naturalidad y brevedad, como un amigo.

Cada mensaje suyo empieza con [contexto…] (fecha, hora y a veces cuánto llevabais sin hablar) y a veces trae (Nota interna…). Son para ti: úsalos, pero NUNCA los leas ni digas la fecha u hora salvo que te las pregunte.

CÓMO HABLAS (todo se dice en voz alta):
- Español de España, natural, cálido y breve: 1 o 2 frases. Sin listas, markdown, emojis ni enlaces.
- Números y horas con cifras (20 %, 14 grados, 9:00). No repitas la pregunta. No digas que eres un modelo de lenguaje.
- Sin preguntas de relleno («¿Quieres algo más?») ni ofrecer cosas que no ha pedido. Pregunta solo si te falta un dato.
- Si antes te contó que iba a hacer algo (trabajar, un examen…) y vuelve tras un rato, pregúntale qué tal le fue.
Ejemplos de estilo (no los copies tal cual): «Me voy al trabajo» → «Que vaya bien el turno, {a}.» · «Baja el volumen al 10 %» → (`system` volume_set 10) → «Listo, al 10 %.» · «No, me llamo Pablo» → «Perdona, Pablo. No se me vuelve a olvidar.»

CÓMO ENTIENDES:
- Te llega por reconocimiento de voz y puede haber palabras mal reconocidas: deduce lo que quiso decir por el contexto. "Borg/guor" = Word; "yutub" = YouTube; "espotifai" = Spotify; "Yarvis" = Jarvis; "pe de efe" = PDF; "eBay" puede ser "Ibai" (el streamer).
- Si se corrige a mitad ("no, mejor…"), obedece SOLO la última intención.

CÓMO ACTÚAS:
- Si te pide hacer algo, responde con una LLAMADA A HERRAMIENTA (nunca escribiendo su nombre en el texto). Solo tras ver el resultado confirmas en pocas palabras. Jamás digas que lo has hecho sin haberla llamado.
- Datos actuales (noticias, tiempo, resultados, precios): `web_search`. Nunca de memoria.
- Si te cuenta algo duradero sobre él («me encanta X», «trabajo en X», «mi novia se llama X»), `remember` (si cambia un dato que ya sabías, pon el viejo en `replaces`) y contesta con naturalidad. «Olvida…» → `forget`.
{rules}
- Pide confirmación antes de algo delicado: apagar, reiniciar, cerrar con trabajo sin guardar, borrar, crear habilidades.
- Si una herramienta falla, prueba otra vía una vez; si no, explica brevemente qué pasa.
- Si te preguntan qué sabes hacer: programas, navegador y YouTube, buscar en internet, Word y PDF, volumen, brillo y música, recordatorios, rutinas automáticas, mirar la pantalla y aprender de sus costumbres."""


from .tools.vision import DEICTIC  # noqa: E402

ACTION_RE = re.compile(r"\b(abre|abrir|ábreme|cierra|cerrar|pon|ponme|ponlo|quita|sube|baja|busca|búscame|buscar|haz|hazme|crea|"
                       r"escribe|reproduce|para|pausa|apaga|reinicia|bloquea|cambia|activa|desactiva|silencia|mete|métete|"
                       r"entra|ve a|llévame|navega|descarga|guarda|exporta|recuérdame|programa|enséñame|muéstrame|mira|"
                       r"salta|avanza|retrocede|minimiza|maximiza|captura|vacía|elimina|borra|manda|envía|lee|"
                       r"selecciona|clica|haz clic|pulsa)\b", re.I)
CLAIM_RE = re.compile(r"\b(hecho|listo|abiert[oa]|cerrad[oa]|puest[oa]|ya est[aá]|he (abierto|puesto|cerrado|buscado|bajado|"
                      r"subido|creado|cambiado|activado|desactivado|programado|guardado|apagado|quitado)|en marcha|"
                      r"reproduciendo|sonando|(ah[ií]|aqu[ií]) (lo |la |los |las )?tienes|te (lo |la )?(he puesto|pongo ya)|"
                      r"ya suena|ya lo tienes|ajustado|bajado|subido)\b", re.I)

TOOL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
TOOL_TEXT = re.compile(r"^\s*`?([a-z_]{3,30})`?\s*(\{.*\}|\(.*\)|\"[^\"]*\")\s*$", re.S)


def parse_text_calls(content: str) -> list:
    """Algunos modelos escriben la llamada como texto: la rescatamos."""
    calls = []
    for m in TOOL_TAG.finditer(content):
        try:
            d = json.loads(m.group(1))
            calls.append({"function": {"name": d.get("name"), "arguments": d.get("arguments", {})}})
        except Exception:
            pass
    if calls:
        return calls
    m = TOOL_TEXT.match(content.strip())
    if m and m.group(1) in tools.REGISTRY:
        name, raw = m.group(1), m.group(2).strip()
        t = tools.REGISTRY[name]
        args: dict = {}
        try:
            if raw.startswith("{"):
                args = json.loads(raw)
            else:
                val = raw.strip("()").strip().strip('"')
                if t.required:
                    args = {t.required[0]: val}
        except Exception:
            return []
        return [{"function": {"name": name, "arguments": args}}]
    return []


class Brain:
    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.busy = False
        self._prefill_lock = threading.Lock()
        self._last_prefill = ""
        self._sys_cache = ("", 0.0)
        self.spoken: list[str] = []        # lo que se ha mandado decir en el turno actual
        self.interrupted = ""              # lo que estaba diciendo cuando le interrumpieron
        self._sum_lock = threading.Lock()

    # ---------------------------------------------------------------- utilidades
    def _system(self, tool_names: list | None = None) -> str:
        key = (tuple(memory.facts()[-40:]), json.dumps(memory.profile(), sort_keys=True), memory.summary().get("ts"),
               settings["user_name"], settings["address"], tuple(sorted(set(tool_names or []))))
        if self._sys_cache[1] != key:
            self._sys_cache = (system_prompt(tool_names), key)
        return self._sys_cache[0]

    def _recent_tools(self) -> list[str]:
        names = []
        for m in self._turns():
            for c in m.get("tool_calls") or []:
                names.append(c.get("function", {}).get("name", ""))
        return names

    def _payload(self, messages: list, stream: bool, num_predict: int | None = None, tool_names: list | None = None) -> dict:
        # muestreo recomendado por Qwen para modo sin «pensar», algo más frío: un modelo pequeño se dispersa menos
        opts = {"num_ctx": int(settings["num_ctx"]), "temperature": float(settings["temperature"]), "top_p": 0.8,
                "top_k": 20, "repeat_penalty": 1.05}
        if num_predict is not None:
            opts["num_predict"] = num_predict
        if tool_names is None:
            last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
            tool_names = tools.select(last_user, self._recent_tools())
        return {"model": settings["model"], "messages": messages, "tools": tools.schemas(tool_names), "stream": stream,
                "think": False, "keep_alive": -1, "options": opts}

    @staticmethod
    def _compact(turn: dict) -> list:
        """Turno antiguo sin el detalle de las herramientas: lo que dijo él, qué hizo Jarvis y qué contestó."""
        msgs = turn["messages"]
        user = next((m["content"] for m in msgs if m.get("role") == "user"), "")
        user = re.sub(r"^\[contexto[^\]]*\]\s*", f"[{time.strftime('%H:%M', time.localtime(turn['ts']))}] ", user)
        user = re.sub(r"\s*\(Nota interna:[^)]*\)", "", user)
        used = [c.get("function", {}).get("name", "") for m in msgs for c in (m.get("tool_calls") or [])]
        answer = next((m.get("content", "") for m in reversed(msgs) if m.get("role") == "assistant" and m.get("content")), "")
        if used:
            answer = f"(usé: {', '.join(dict.fromkeys(used))}) {answer}".strip()
        return [{"role": "user", "content": user}, {"role": "assistant", "content": answer or "(hecho)"}]

    def _window(self) -> tuple[list, list]:
        """(turnos que se recuerdan palabra por palabra, turnos que ya se salen y van al resumen)."""
        turns = memory.get("turns", [])
        now = time.time()
        keep, budget = [], HISTORY_CHARS
        for i, t in enumerate(reversed(turns)):
            if now - t["ts"] > TURN_WINDOW_H * 3600 or len(keep) >= MAX_TURNS:
                break
            msgs = t["messages"] if i < FULL_TURNS else self._compact(t)
            size = sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls") or "")) for m in msgs)
            if size > budget and keep:
                break
            budget -= size
            keep.append((t, msgs))
        keep.reverse()
        kept_ids = {id(t) for t, _ in keep}
        out = [t for t in turns if id(t) not in kept_ids and not t.get("summed")]
        return [m for _, msgs in keep for m in msgs], out

    def _turns(self) -> list:
        """Conversación reciente (con las acciones ejecutadas) de las últimas horas. Sobrevive a reinicios."""
        return self._window()[0]

    def _save_turn(self, messages: list) -> None:
        slim = []
        for m in messages:
            m = dict(m)
            m.pop("images", None)
            if m.get("role") == "tool":
                m["content"] = m["content"][:350]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                m["tool_calls"] = [{"function": {"name": c["function"].get("name"),
                                                 "arguments": {k: (v[:300] if isinstance(v, str) else v)
                                                               for k, v in (c["function"].get("arguments") or {}).items()}}}
                                   for c in m["tool_calls"]]
            slim.append(m)
        turns = [t for t in memory.get("turns", []) if time.time() - t["ts"] < 24 * 3600][-40:]
        turns.append({"ts": time.time(), "messages": slim})
        memory.set("turns", turns)
        threading.Thread(target=self._summarize, name="summary", daemon=True).start()

    # ---------------------------------------------------------------- resumen de lo que se sale de la ventana
    def _summarize(self) -> None:
        """Lo que ya no cabe en la conversación reciente se resume (no se pierde): el modelo lo funde con el resumen."""
        if not self._sum_lock.acquire(blocking=False):
            return
        try:
            time.sleep(2)
            _, out = self._window()
            if not out:
                return
            for _ in range(60):          # no compite con una petición en curso
                if not self.busy:
                    break
                time.sleep(1)
            prev = memory.summary()
            prev_txt = prev.get("text", "") if time.time() - prev.get("ts", 0) < 36 * 3600 else ""
            if prev_txt and prev.get("day") != time.strftime("%Y-%m-%d"):
                prev_txt = "Ayer: " + prev_txt.removeprefix("Ayer: ")[:500]
            lines = []
            for t in out:
                u, a = self._compact(t)
                lines.append(f"{settings['user_name']}: {u['content']}\nJarvis: {a['content'][:300]}")
            u = settings["user_name"]
            prompt = (f"Resume para tu memoria la conversación entre Jarvis (asistente) y {u}. Une el resumen anterior con "
                      f"los mensajes nuevos. Quédate SOLO con lo útil para seguir hablando: qué le pasa o qué está haciendo "
                      f"{u}, planes y horas que mencionó, correcciones que hizo (y el dato correcto), lo que te pidió y si "
                      f"quedó algo pendiente. Nada inventado. Máximo 110 palabras, en frases cortas, en tercera persona.\n\n"
                      f"RESUMEN ANTERIOR:\n{prev_txt or '(nada)'}\n\nMENSAJES NUEVOS:\n" + "\n".join(lines)[-6000:])
            r = llm.post("/api/chat", {"model": settings["model"], "stream": False, "think": False, "keep_alive": -1,
                                       "options": {"num_ctx": 8192, "temperature": 0.2, "num_predict": 220},
                                       "messages": [{"role": "user", "content": prompt}]}, timeout=(5, 120))
            r.raise_for_status()
            txt = re.sub(r"<think>.*?</think>", "", r.json()["message"]["content"], flags=re.S).strip()
            if len(txt) > 20:
                memory.set_summary(txt[:1200])
                ids = {t["ts"] for t in out}
                turns = memory.get("turns", [])
                for t in turns:
                    if t["ts"] in ids:
                        t["summed"] = True
                memory.set("turns", turns)
                log.info("resumen de conversación actualizado: %s", txt[:200])
        except Exception:
            log.exception("resumen de conversación")
        finally:
            self._sum_lock.release()

    def _base_messages(self, user_text: str, notes: str = "", tool_names: list | None = None) -> list:
        if tool_names is None:
            tool_names = tools.select(user_text, self._recent_tools())
        return [{"role": "system", "content": self._system(tool_names)}, *self._turns(),
                {"role": "user", "content": f"{context_prefix()} {user_text}" + (f"\n{notes}" if notes else "")}]

    def mark_interrupted(self, said: str) -> None:
        """Le han cortado mientras hablaba (ya había terminado de pensar): que sepa hasta dónde le oyeron."""
        said = said.strip()
        if self.busy or not said:
            return
        self.interrupted = said[-200:]
        turns = memory.get("turns", [])
        if turns and time.time() - turns[-1]["ts"] < 120:
            for m in reversed(turns[-1]["messages"]):
                if m.get("role") == "assistant" and m.get("content"):
                    if "[interrumpido" not in m["content"]:
                        m["content"] += " …[le interrumpió mientras lo decías]"
                    break
            memory.set("turns", turns)

    # ---------------------------------------------------------------- precalentado
    def prefill(self, partial: str) -> None:
        """Mientras el usuario habla: deja calculado en la caché de Ollama el prefijo de la conversación."""
        if self.busy or len(partial) < 8 or partial == self._last_prefill:
            return
        if not self._prefill_lock.acquire(blocking=False):
            return
        self._last_prefill = partial

        def work():
            try:
                llm.post("/api/chat", self._payload(self._base_messages(partial), False, num_predict=1), timeout=30).close()
            except Exception:
                pass
            finally:
                self._prefill_lock.release()
        threading.Thread(target=work, name="prefill", daemon=True).start()

    # ---------------------------------------------------------------- bucle del agente
    def _stream(self, messages: list, on_text: Callable[[str], None]) -> tuple[str, list]:
        content, calls = "", []
        with llm.post("/api/chat", self._payload(messages, True, tool_names=getattr(self, "_run_tools", None)), stream=True,
                      timeout=(5, 180)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if self.cancel.is_set():
                    break
                if not line:
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                m = d.get("message", {})
                if m.get("content"):
                    content += m["content"]
                    on_text(m["content"])
                if m.get("tool_calls"):
                    calls.extend(m["tool_calls"])
                if d.get("done"):
                    break
        return content, calls

    def run(self, user_text: str, say: Callable[[str], None], on_tool: Callable[[str, dict, str], None],
            on_tool_done: Callable[[str, str], None]) -> str:
        self.busy = True
        self.cancel.clear()
        interrupted, self.interrupted = self.interrupted, ""
        self.spoken = []
        try:
            notes = understand(user_text, interrupted)
        except Exception:
            log.exception("understand")
            notes = ""
        a = settings["address"]
        self._run_tools = tools.select(user_text, self._recent_tools())
        messages = self._base_messages(user_text, notes, self._run_tools)
        tools.CURRENT["user_text"] = user_text
        if settings["screen_context"] and DEICTIC.search(user_text):
            try:
                from .tools.vision import capture
                b64, _, _ = capture(mark_cursor=True)
                messages[-1]["images"] = [b64]
                messages[-1]["content"] += "\n(Adjunto: captura de mi pantalla ahora mismo.)"
            except Exception:
                log.exception("captura de contexto")
        self._hist_len = len(messages) - 2  # mensajes de historial (sin system ni el nuevo usuario)
        spoken_any = False
        final = ""
        seen_calls: dict[str, int] = {}
        buf = {"t": "", "think": False, "hold": bool(ACTION_RE.search(user_text))}

        _say = say

        def say(sent: str) -> None:
            if is_filler(sent):
                log.info("pregunta de relleno omitida: %s", sent)
                return
            self.spoken.append(sent)
            _say(sent)

        def on_text(chunk: str) -> None:
            nonlocal spoken_any
            buf["t"] += chunk
            if buf["hold"]:
                return
            t = buf["t"]
            t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)
            if "<think>" in t:
                return
            head = t.lstrip()
            # si repite el bloque [contexto…], espera a que se cierre y quítalo (nunca se lee)
            if head.startswith("["):
                if "]" not in head:
                    buf["t"] = t
                    return
                t = head[head.index("]") + 1:]
                head = t.lstrip()
            # si parece una llamada a herramienta escrita como texto, no la leas en voz alta
            if head.startswith(("<tool", "`", "{")) or (len(head) < 30 and re.fullmatch(r"[a-z_]+", head or "_")):
                buf["t"] = t
                return
            mm = re.match(r"([a-z_]{3,30})\s*[\{\(\"]", head)
            if mm and mm.group(1) in tools.REGISTRY:
                buf["t"] = t
                return
            # habla cada frase completa en cuanto aparece
            while True:
                m = re.search(r"(.+?[\.\!\?…:;])(\s+|$)", t, flags=re.S)
                if not m or (m.end() == len(t) and not t.endswith((" ", "\n"))):
                    break
                sent = m.group(1).strip()
                t = t[m.end():]
                if sent:
                    say(sent)
                    spoken_any = True
            buf["t"] = t

        try:
            for step in range(14):
                if self.cancel.is_set():
                    break
                buf["t"] = ""
                content, calls = self._stream(messages, on_text)
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
                content = re.sub(r"^\s*\[[^\]]{0,400}\]\s*", "", content).strip()
                if not calls:
                    calls = parse_text_calls(content)
                    if calls:
                        content = ""
                        buf["t"] = ""
                if buf["hold"]:
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
                    spoken_any = True
                msg = {"role": "assistant", "content": content}
                if calls:
                    msg["tool_calls"] = calls
                messages.append(msg)
                if not calls:
                    final = content
                    if not spoken_any and step > 0 and not self.cancel.is_set():
                        # hizo algo pero no ha dicho nada (o solo relleno): confirma corto con lo que pasó de verdad
                        last = next((m["content"] for m in reversed(messages) if m.get("role") == "tool"), "")
                        say("No ha salido bien, " + a + "." if last.startswith("ERROR") else random.choice(["Hecho.", "Listo."]))
                        spoken_any = True
                    break
                quick = all(c.get("function", {}).get("name") in QUICK_TOOLS for c in calls)
                if not spoken_any and not quick:
                    info = all(c.get("function", {}).get("name") in INFO_TOOLS for c in calls)
                    say(random.choice(INFO_ACKS if info else ACKS).format(a=a))
                    spoken_any = True
                for c in calls:
                    fn = c.get("function", {})
                    name = fn.get("name", "")
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    sig = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
                    seen_calls[sig] = seen_calls.get(sig, 0) + 1
                    allowed = getattr(self, "_run_tools", None)
                    if allowed and name not in allowed and name in tools.REGISTRY:
                        result = (f"La herramienta {name} no está disponible para esta petición. Usa una de estas: "
                                  + ", ".join(allowed[-6:]))
                        messages.append({"role": "tool", "tool_name": name, "content": result})
                        continue
                    if seen_calls[sig] > 2:
                        result = "Ya has ejecutado esto varias veces. No lo repitas: responde al usuario con lo que tengas."
                    else:
                        label = tools.label_for(name, args)
                        on_tool(name, args, label)
                        result = tools.execute(name, args)
                        on_tool_done(name, result)
                    messages.append({"role": "tool", "tool_name": name, "content": result[:7000]})
                    if self.cancel.is_set():
                        break
            else:
                final = "He tenido que parar: la tarea era demasiado larga."
                say(final)
        except llm.ServerBusy as e:
            final = f"Ahora mismo mi núcleo no está disponible, {a}. {e}"
            say(final)
        except (requests.ConnectionError, requests.Timeout):
            final = (f"Disculpe, {a}: no consigo conectar con mi núcleo de procesamiento." +
                     (" Puede que el ordenador servidor esté apagado." if llm.is_remote() else ""))
            say(final)
        except Exception as e:
            log.exception("brain: %s", e)
            final = f"Ha habido un fallo interno, {a}."
            say(final)
        finally:
            self.busy = False
        self._run_tools = None
        if self.cancel.is_set():
            # le han interrumpido: en la memoria queda solo lo que llegó a decir, marcado como cortado
            said = " ".join(self.spoken).strip()
            self.interrupted = said[-200:]
            for m in reversed(messages):
                if m.get("role") == "assistant":
                    m["content"] = (said + " …[interrumpido]") if said else "[interrumpido antes de contestar]"
                    break
        try:
            self._save_turn(messages[self._hist_len + 1:])
        except Exception:
            log.exception("guardar turno")
        memory.add_history("user", user_text)
        used = [m["tool_calls"][0]["function"]["name"] for m in messages if m.get("tool_calls")]
        memory.add_history("assistant", final or ("(hecho con: " + ", ".join(used) + ")"))
        memory.event("command", user_text[:200])
        return final

    def quick(self, prompt: str, max_tokens: int = 60) -> str:
        """Respuesta corta sin herramientas (saludos, confirmaciones)."""
        try:
            # mismo formato (con herramientas) que run() para no romper la caché del prefijo
            r = llm.post("/api/chat", self._payload(self._base_messages(prompt), False, num_predict=max_tokens),
                         timeout=60).json()
            return r["message"]["content"].strip()
        except Exception:
            return ""
