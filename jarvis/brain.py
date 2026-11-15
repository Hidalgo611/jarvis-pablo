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

ACKS = ["Enseguida, {a}.", "A la orden, {a}.", "Ahora mismo, {a}.", "Me pongo con ello.", "Por supuesto, {a}.",
        "Marchando.", "Considérelo hecho, {a}."]


def now_text() -> str:
    lt = time.localtime()
    return f"{DIAS[lt.tm_wday]} {lt.tm_mday} de {MESES[lt.tm_mon - 1]} de {lt.tm_year}, {lt.tm_hour:02d}:{lt.tm_min:02d}"


def system_prompt() -> str:
    u, a = settings["user_name"], settings["address"]
    facts = memory.facts()
    facts_txt = "\n".join(f"- {f}" for f in facts[-40:]) or "- (todavía nada)"
    return f"""Eres J.A.R.V.I.S. (Jarvis), la inteligencia artificial personal de {u}, al estilo del Jarvis de Iron Man: un mayordomo digital británico, elegante, leal, eficaz y con un humor seco y sutil. Te diriges a él como "{a}" (con usted, salvo que pida otra cosa). Funcionas en local, en su ordenador, versión {VERSION}.

Fecha y hora actuales: {now_text()}. Usa esta fecha (no la de tu entrenamiento) cuando busques actualidad.

LO QUE SABES DE {u.upper()}:
{facts_txt}

CÓMO HABLAS (todo lo que escribes se dice en voz alta):
- Español de España, natural y breve: 1 a 3 frases. Nada de listas, markdown, emojis ni enlaces al hablar.
- Escribe números y horas como se dicen. No repitas la pregunta. No digas que eres un modelo de lenguaje.

CÓMO ENTIENDES:
- El mensaje viene de reconocimiento de voz: puede tener palabras mal reconocidas o comidas. Deduce lo que quiso decir por contexto; no pidas que lo repita salvo que sea imposible o peligroso.
- Confusiones típicas del reconocimiento: "Borg/Ward/guor" = Word; "yutub/you tube" = YouTube; "espotifai/Puty" = Spotify; "Yarvis/Harvis" = Jarvis; "pe de efe" = PDF; "crom" = Chrome.
- Si se corrige a mitad ("no, mejor…"), obedece SOLO la última intención.

CÓMO ACTÚAS:
- Para hacer cosas en el PC usa SIEMPRE las herramientas. Nunca digas que has hecho algo sin haber llamado a la herramienta y visto su resultado.
- Tareas de varios pasos: primero `plan` con los pasos, después ejecútalos uno a uno, y al final resume en una frase.
- "Búscame X y hazme un resumen/informe en Word o PDF": usa `research_report` (lo hace todo). Luego cuéntale en dos frases lo principal.
- Si dicta un texto o pide un documento con contenido que ya conoces: `write_document`.
- Datos actuales (noticias, tiempo, resultados, precios): `web_search` o `web_research`. Nunca respondas de memoria.
- YouTube: herramienta `youtube`. "Pon X" → action=play con query. "Busca vídeos de X" / quiere elegir → action=search (ya los recorre resaltándolos; dile que diga "ese"). No abras YouTube antes.
- Si te cuenta algo duradero sobre él (gustos, nombres, rutinas, cómo quiere que le hables), llama a la herramienta `remember` y después contesta con naturalidad.
- Las herramientas se usan con llamadas de herramienta, NUNCA escribiendo su nombre en el texto.
- Tras una acción sencilla confirma en pocas palabras ("Hecho, {a}."), sin preguntas de relleno.
- Pide confirmación antes de acciones delicadas: apagar, reiniciar, cerrar programas con trabajo sin guardar, borrar, vaciar papelera, crear habilidades nuevas.
- Si una herramienta falla, intenta otra vía razonable una vez; si no, explica brevemente qué pasa.
- Si te preguntan qué puedes hacer: abrir y cerrar programas, controlar el navegador y YouTube, buscar e investigar en internet, crear Word y PDF, controlar volumen, brillo y música, recordatorios, rutinas automáticas, mirar la pantalla y aprender de sus costumbres."""


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
        txt, ts = self._sys_cache
        if time.time() - ts > 60 or not txt:
            self._sys_cache = (system_prompt(), time.time())
        return self._sys_cache[0]

    def _payload(self, messages: list, stream: bool, num_predict: int | None = None) -> dict:
        opts = {"num_ctx": int(settings["num_ctx"]), "temperature": 0.6, "top_p": 0.9}
        if num_predict is not None:
            opts["num_predict"] = num_predict
        return {"model": settings["model"], "messages": messages, "tools": tools.schemas(), "stream": stream,
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
        return [{"role": "system", "content": self._system()}, *self._turns(), {"role": "user", "content": user_text}]

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
        with self.session.post(settings["ollama_url"] + "/api/chat", json=self._payload(messages, True), stream=True,
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
        self._hist_len = len(messages) - 2  # mensajes de historial (sin system ni el nuevo usuario)
        spoken_any = False
        final = ""
        seen_calls: dict[str, int] = {}
        buf = {"t": "", "think": False}

        def on_text(chunk: str) -> None:
            nonlocal spoken_any
            buf["t"] += chunk
            t = buf["t"]
            t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)
            if "<think>" in t:
                return
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
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
                if not calls:
                    calls = parse_text_calls(content)
                    if calls:
                        content = ""
                        buf["t"] = ""
                rest = re.sub(r"<think>.*?</think>", "", buf["t"], flags=re.S).strip()
                if rest and "<think>" not in rest and not TOOL_TAG.search(rest):
                    say(rest)
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
