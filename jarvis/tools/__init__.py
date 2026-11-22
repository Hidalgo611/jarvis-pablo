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


# Las que van SIEMPRE. El resto solo si la frase habla de ello (cada herramienta son ~100-300 tokens que el modelo
# lee en cada frase: menos herramientas = responde antes y se equivoca menos al elegir).
CORE = ["open_app", "close_app", "open_website", "web_search", "system", "remember", "forget"]
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
    # recordatorios solo si los pide (si no, el modelo pequeño los pone sin venir a cuento)
    "recordatorios": (r"recu[eé]rd(a|ame|amelo)|av[ií]s(a|ame)|recordatorio|temporizador|alarma|despi[eé]rtame|"
                      r"en \w+ minutos|dentro de|cuenta atr[aá]s", ["set_reminder"]),
    "rutinas": (r"rutina|automatiza|autom[aá]tic|a partir de ahora|de ahora en adelante|desde ahora|cada (d[ií]a|ma[ñn]ana|"
                r"noche|tarde|semana|hora|vez|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)|todos los (d[ií]as|lunes|"
                r"martes|mi[eé]rcoles|jueves|viernes|s[aá]bados|domingos)|todas las (ma[ñn]anas|noches|tardes)|siempre que|"
                r"cuando (encienda|arranque|diga)|al encender|programa(r|me)? (que|para)|diariamente|de lunes a viernes|entre semana|"
                r"los fines de semana|todas las semanas",
                ["create_routine", "list_routines", "delete_routine"]),
    "pantalla": (r"\b(ah[ií]|aqu[ií]|esto|esta|este|eso|esa|ese|pantalla(?! completa)|ventana|bot[oó]n|icono|clic|cl[ií]ca|pulsa|"
                 r"pincha|marca|selecciona|mira|ves|minimiza|maximiza|cambia a|ve a|tr[aá]e|pon delante|men[uú]|pesta[ñn]a)\b",
                 ["screen_click", "look_at_screen", "focus_window"]),
    "navegador": (r"navegador|pesta[ñn]a|p[aá]gina|web\b|enlace|link|scroll|baja la p|sube la p|desliza|l[eé]e(me)? |"
                  r"escribe en|rellena|formulario|google|zoom|atr[aá]s|adelante|recarga|inicia sesi[oó]n|chrome|edge",
                  ["browser"]),
    "plan": (r"\by (luego|despu[eé]s|tambi[eé]n|adem[aá]s)\b|\bprimero\b|\bdespu[eé]s\b|\by (abre|pon|cierra|busca|haz|m[aá]ndame|"
             r"escribe|baja|sube)\b", ["plan"]),
    "jarvis": (r"voz|habla m[aá]s|ajuste|configura|actualiza|versi[oó]n|habilidad|"
               r"aprend|sabes de m[ií]|qui[eé]n eres|c[oó]mo te llamas|estado del (pc|ordenador|sistema)|bater[ií]a|memoria ram",
               ["create_routine", "list_routines", "delete_routine", "voices", "settings", "check_updates", "create_skill",
                "undo_skill", "about", "what_you_know", "forget", "system_status"]),
}


def select(text: str, recent: list[str] | None = None) -> list[str]:
    """Herramientas para esta petición: las básicas + los grupos que encajan con lo dicho o se usaron hace poco."""
    import re as _re
    names = list(CORE)
    low = (text or "").lower()
    recent = set(recent or [])
    # crear/gestionar una rutina: solo sus herramientas (lo que haga la rutina se ejecutará después, a su hora)
    routine = bool(_re.search(GROUPS["rutinas"][0], low))
    for gname, (kw, group) in GROUPS.items():
        if routine and gname not in ("rutinas", "recordatorios"):
            continue
        if _re.search(kw, low) or recent.intersection(group):
            names += group
    names += [n for n, t in REGISTRY.items() if not t.core]       # habilidades propias
    # «búscame X y hazme un Word/PDF/informe»: solo la herramienta que lo hace todo (los modelos pequeños se lían)
    if _re.search(r"(word|pdf|documento|informe)", low) and _re.search(r"(busca|b[uú]scame|investiga|noticias|inf[oó]rmate|"
                                                                       r"qu[eé] (est[aá]|ha) pasa|sobre)", low):
        names = [n for n in names if n not in ("web_search", "web_research", "write_document")]
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
