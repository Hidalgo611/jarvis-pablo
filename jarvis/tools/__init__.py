"""Registro de herramientas que el cerebro puede usar."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("tools")


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    required: list
    fn: Callable[..., str]
    label: Callable[[dict], str] | None = None   # texto para la interfaz
    core: bool = True

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


REGISTRY: dict[str, Tool] = {}
CURRENT = {"user_text": ""}   # lo que pidió el usuario en este turno


def tool(name: str, description: str, params: dict | None = None, required: list | None = None,
         label: Callable[[dict], str] | str | None = None, core: bool = True):
    def deco(fn):
        lab = (lambda a, s=label: s) if isinstance(label, str) else label
        REGISTRY[name] = Tool(name, description, params or {}, required or [], fn, lab, core)
        return fn
    return deco


def S(desc: str, enum: list | None = None) -> dict:
    d = {"type": "string", "description": desc}
    if enum:
        d["enum"] = enum
    return d


def N(desc: str) -> dict:
    return {"type": "number", "description": desc}


CORE = ["open_app", "close_app", "open_website", "web_search", "system", "screen_click", "look_at_screen",
        "remember", "browser", "focus_window", "set_reminder", "plan"]
# grupo -> (palabras clave, herramientas)
GROUPS = {
    "youtube": (r"youtube|yutub|v[ií]deo|canal|shorts|directo|reproduc|tr[aá]iler|canci[oó]n|anuncio|calidad|pantalla completa|"
                r"subt[ií]tulo|retrocede|avanza|adelanta|pausa|ese\b|pon\b|ponme|m[uú]sica", ["youtube"]),
    "docs": (r"word|pdf|documento|informe|resumen|redacta|escribe un|exporta|investiga", ["research_report", "write_document",
                                                                                        "convert_to_pdf", "web_research"]),
    "cuentas": (r"correo|mail|gmail|hotmail|outlook|agenda|calendario|evento|drive|archivo de google|qu[eé] tengo|"
                r"resumen del d[ií]a|buenos d[ií]as|notificaci|whatsapp|mensaje|telegram|l[eé]emelo|qu[eé] dice",
                ["email_check", "calendar_today", "drive_search", "daily_briefing", "notifications"]),
    "archivos": (r"carpeta|archivo|fichero|descargas|documentos|escritorio|im[aá]genes|fotos|busca el archivo",
                 ["open_folder", "find_files", "open_file"]),
    "teclado": (r"escribe|teclea|pulsa|tecla|atajo|rat[oó]n|copia|pega|control|alt\b", ["type_text", "press_keys", "mouse"]),
    "jarvis": (r"rutina|cada d[ií]a|todos los d[ií]as|voz|habla m[aá]s|ajuste|configura|actualiza|versi[oó]n|habilidad|"
               r"aprend|sabes de m[ií]|olvida|qui[eé]n eres|c[oó]mo te llamas|estado del (pc|ordenador|sistema)|bater[ií]a|memoria ram",
               ["create_routine", "list_routines", "delete_routine", "voices", "settings", "check_updates", "create_skill",
                "undo_skill", "about", "what_you_know", "forget", "system_status"]),
}


def select(text: str, recent: list[str] | None = None) -> list[str]:
    """Herramientas para esta petición: las básicas + los grupos que encajan con lo dicho o se usaron hace poco."""
    import re as _re
    names = list(CORE)
    low = (text or "").lower()
    recent = set(recent or [])
    for kw, group in GROUPS.values():
        if _re.search(kw, low) or recent.intersection(group):
            names += group
    names += [n for n, t in REGISTRY.items() if not t.core]       # habilidades propias
    seen = set()
    return [n for n in names if n in REGISTRY and not (n in seen or seen.add(n))]


def schemas(names: list[str] | None = None) -> list[dict]:
    if names is None:
        return [t.schema() for t in REGISTRY.values()]
    return [REGISTRY[n].schema() for n in names if n in REGISTRY]


def label_for(name: str, args: dict) -> str:
    t = REGISTRY.get(name)
    if t and t.label:
        try:
            return t.label(args)
        except Exception:
            pass
    return name.replace("_", " ").capitalize()


def execute(name: str, args: dict) -> str:
    t = REGISTRY.get(name)
    if not t:
        return f"ERROR: la herramienta '{name}' no existe."
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    args = {k: v for k, v in (args or {}).items() if k in t.params}
    t0 = time.time()
    try:
        out = t.fn(**args)
        log.info("tool %s(%s) -> %.1fs %s", name, json.dumps(args, ensure_ascii=False)[:200], time.time() - t0,
                 str(out)[:200].replace("\n", " "))
        return str(out) if out is not None else "Hecho."
    except TypeError as e:
        return f"ERROR de argumentos: {e}"
    except Exception as e:
        log.exception("tool %s falló", name)
        return f"ERROR: {type(e).__name__}: {e}"


def load_all() -> None:
    from . import system, browser, docs, assistant, report, vision, accounts  # noqa: F401  (registran sus herramientas)
