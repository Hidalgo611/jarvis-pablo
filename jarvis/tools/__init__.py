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


def schemas() -> list[dict]:
    return [t.schema() for t in REGISTRY.values()]


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
    from . import system, browser, docs, assistant, report  # noqa: F401  (registran sus herramientas)
