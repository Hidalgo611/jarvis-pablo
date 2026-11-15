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

from . import tools
from .config import VERSION, settings
from .memory import memory

log = logging.getLogger("brain")

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]

QUICK_TOOLS = {"remember", "forget", "what_you_know", "about", "settings", "list_routines", "system_status",
               "open_app", "close_app", "system", "press_keys", "type_text", "set_reminder", "voices"}

ACKS = ["Enseguida, {a}.", "A la orden.", "Ahora mismo.", "Me pongo con ello.", "Por supuesto, {a}.",
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
    head = f"[contexto, no lo leas: {DIAS[lt.tm_wday]} {lt.tm_mday} de {MESES[lt.tm_mon - 1]} de {lt.tm_year}, {lt.tm_hour:02d}:{lt.tm_min:02d}"
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

Cada mensaje del usuario empieza con un bloque [contexto…] con la fecha y hora reales (y a veces cuánto llevabais sin hablar y lo último que te contó). Es información interna: úsala, pero NUNCA la repitas ni digas la fecha u hora salvo que te las pregunte.

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
- "Búscame X y hazme un resumen/informe en Word o PDF": llama DIRECTAMENTE a `research_report` (busca, lee y redacta él solo; no uses web_search antes). Luego cuéntale en dos frases lo principal.
- Si dicta un texto o pide un documento con contenido que ya conoces: `write_document`.
- Datos actuales (noticias, tiempo, resultados, precios): `web_search` o `web_research`. Nunca respondas de memoria.
- YouTube (`youtube`): "pon X" → play. "busca X" → search (solo enseña resultados; NO los recorras). "Abre el canal de X" → open_channel con query=X (el NOMBRE del canal; sin query = canal del vídeo actual) y, si pide una sección, section=vídeos/shorts/directos. Ya dentro de un canal, "sección vídeos" → channel_tab con section. "Pon el que se llama…" → play_title. "Ve bajando / enséñamelos uno a uno" → browse_results y dile que diga "ese". No abras YouTube antes de buscar.
- Correo, agenda, Drive: `email_check`, `calendar_today`, `drive_search` (usan tu navegador; si no ha iniciado sesión, díselo). «¿Qué tengo hoy?», «resumen del día», «buenos días» → `daily_briefing` y cuéntalo en 3-5 frases naturales.
- Notificaciones: anuncia de quién son, pero NO leas el contenido de mensajes privados salvo que él te lo pida («léemelo», «¿qué dice?») → `notifications` con read_content=true.
- Si te dice su ciudad, guárdala con `settings` (key=city) además de `remember`.
- Si te cuenta algo duradero sobre él («me encanta X», «soy de X», «mi novia se llama X», «trabajo en X», cómo quiere que le hables), llama a `remember` y después contesta con naturalidad.
- PANTALLA: si su mensaje trae una captura, es su pantalla en este momento (el círculo rojo «RATÓN» es el puntero). Úsala para entender «esto», «ahí», «ese botón». Para pulsar cualquier cosa de cualquier programa usa `screen_click` describiendo el elemento tal como se ve (con window=programa si sabes en cuál está). No pulses en la barra de tareas para «abrir» algo que ya está abierto: usa `focus_window`. Nunca afirmes un resultado que no hayas visto. Sin captura y si necesitas ver algo, usa `look_at_screen`.
- Las herramientas se usan con llamadas de herramienta, NUNCA escribiendo su nombre en el texto.
- Si te pide hacer algo, tu respuesta tiene que ser una LLAMADA A HERRAMIENTA. Solo después de ver el resultado confirmas en pocas palabras, sin preguntas de relleno. Jamás digas "hecho" sin haber llamado a la herramienta.
- Pide confirmación antes de acciones delicadas: apagar, reiniciar, cerrar programas con trabajo sin guardar, borrar, vaciar papelera, crear habilidades nuevas.
- Si una herramienta falla, intenta otra vía razonable una vez; si no, explica brevemente qué pasa.
- Si te preguntan qué puedes hacer: abrir y cerrar programas, controlar el navegador y YouTube, buscar e investigar en internet, crear Word y PDF, controlar volumen, brillo y música, recordatorios, rutinas automáticas, mirar la pantalla y aprender de sus costumbres."""


from .tools.vision import DEICTIC  # noqa: E402

ACTION_RE = re.compile(r"\b(abre|abrir|ábreme|cierra|cerrar|pon|ponme|ponlo|quita|sube|baja|busca|búscame|buscar|haz|hazme|crea|"
                       r"escribe|reproduce|para|pausa|apaga|reinicia|bloquea|cambia|activa|desactiva|silencia|mete|métete|"
                       r"entra|ve a|llévame|navega|descarga|guarda|exporta|recuérdame|programa|enséñame|muéstrame|mira|"
                       r"salta|avanza|retrocede|minimiza|maximiza|captura|vacía|elimina|borra|manda|envía|lee|"
                       r"selecciona|clica|haz clic|pulsa)\b", re.I)
CLAIM_RE = re.compile(r"\b(hecho|listo|abiert[oa]|cerrad[oa]|puesto|ya está|he abierto|he puesto|he cerrado|"
                      r"he buscado|en marcha|reproduciendo|ahí lo tienes|ahí tienes)\b", re.I)

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
        self.session = requests.Session()
        self.cancel = threading.Event()
        self.busy = False
        self._prefill_lock = threading.Lock()
        self._last_prefill = ""
        self._sys_cache = ("", 0.0)

    # ---------------------------------------------------------------- utilidades
    def _system(self) -> str:
        # el prompt cambia con la hora: se recalcula como mucho cada minuto para no romper la caché
        key = (len(memory.facts()), settings["user_name"], settings["address"])
        if self._sys_cache[1] != key:
            self._sys_cache = (system_prompt(), key)
        return self._sys_cache[0]

    def _recent_tools(self) -> list[str]:
        names = []
        for m in self._turns():
            for c in m.get("tool_calls") or []:
                names.append(c.get("function", {}).get("name", ""))
        return names

    def _payload(self, messages: list, stream: bool, num_predict: int | None = None, tool_names: list | None = None) -> dict:
        opts = {"num_ctx": int(settings["num_ctx"]), "temperature": 0.6, "top_p": 0.9}
        if num_predict is not None:
            opts["num_predict"] = num_predict
        if tool_names is None:
            last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
            tool_names = tools.select(last_user, self._recent_tools())
        return {"model": settings["model"], "messages": messages, "tools": tools.schemas(tool_names), "stream": stream,
                "think": False, "keep_alive": -1, "options": opts}

    def _turns(self) -> list:
        """Últimos turnos de la conversación (con las acciones ejecutadas), máx. 15 min. Sobrevive a reinicios."""
        turns = memory.get("turns", [])
        fresh = [t for t in turns if time.time() - t["ts"] < 15 * 60][-3:]
        return [m for t in fresh for m in t["messages"]]

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
        turns = [t for t in memory.get("turns", []) if time.time() - t["ts"] < 15 * 60][-2:]
        turns.append({"ts": time.time(), "messages": slim})
        memory.set("turns", turns)

    def _base_messages(self, user_text: str) -> list:
        return [{"role": "system", "content": self._system()}, *self._turns(),
                {"role": "user", "content": f"{context_prefix()} {user_text}"}]

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
                self.session.post(settings["ollama_url"] + "/api/chat",
                                  json=self._payload(self._base_messages(partial), False, num_predict=1), timeout=30)
            except Exception:
                pass
            finally:
                self._prefill_lock.release()
        threading.Thread(target=work, name="prefill", daemon=True).start()

    # ---------------------------------------------------------------- bucle del agente
    def _stream(self, messages: list, on_text: Callable[[str], None]) -> tuple[str, list]:
        content, calls = "", []
        with self.session.post(settings["ollama_url"] + "/api/chat",
                               json=self._payload(messages, True, tool_names=getattr(self, "_run_tools", None)), stream=True,
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
        a = settings["address"]
        messages = self._base_messages(user_text)
        self._run_tools = tools.select(user_text, self._recent_tools())
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
                    break
                quick = all(c.get("function", {}).get("name") in QUICK_TOOLS for c in calls)
                if not spoken_any and not quick:
                    say(random.choice(ACKS).format(a=a))
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
        except requests.ConnectionError:
            final = f"Disculpe, {a}: no consigo conectar con mi núcleo de procesamiento."
            say(final)
        except Exception as e:
            log.exception("brain: %s", e)
            final = f"Ha habido un fallo interno, {a}."
            say(final)
        finally:
            self.busy = False
        self._run_tools = None
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
            r = self.session.post(settings["ollama_url"] + "/api/chat",
                                  json=self._payload(self._base_messages(prompt), False, num_predict=max_tokens),
                                  timeout=60).json()
            return r["message"]["content"].strip()
        except Exception:
            return ""
