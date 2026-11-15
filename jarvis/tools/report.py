"""Informe completo: investigar en internet → redactar (pasada dedicada del modelo) → Word/PDF."""
from __future__ import annotations

import json
import re

import requests

from ..brain import now_text
from ..bus import bus
from ..config import settings
from . import N, S, tool
from .browser import web_research
from .docs import write_document

WRITER = """Eres un redactor experto. Escribe en español de España un informe claro y bien estructurado a partir de las FUENTES.
Reglas:
- No copies párrafos: sintetiza con tus palabras. Usa solo datos que aparezcan en las fuentes; si algo no está claro, dilo.
- Formato exacto (markdown sencillo):
## Resumen
(3-5 frases con lo esencial)
## Puntos clave
- (5-8 viñetas concretas con datos, cifras y fechas)
## En detalle
(2-4 párrafos explicativos; puedes usar ### subtítulos)
## Fuentes
- Título — URL
Fecha de hoy: {fecha}."""


@tool("research_report", "Hace TODO el trabajo de un informe sobre un tema de internet: investiga en varias páginas (se ve en pantalla), redacta un resumen bien escrito y lo guarda en Word y/o PDF y lo abre. Úsala para 'búscame X y hazme un resumen/informe/documento'.",
      {"topic": S("Tema a investigar, con detalle"), "title": S("Título del documento"),
       "format": S("Formato", ["docx", "pdf", "both"]), "focus": S("Qué quiere el usuario destacar (opcional)"),
       "pages": N("Páginas a leer (2-4)")},
      ["topic"], label=lambda a: f"Informe: {a.get('topic', '')}")
def research_report(topic: str, title: str | None = None, format: str = "docx", focus: str = "", pages: float = 3) -> str:
    sources = web_research(topic, pages)
    if sources.startswith("No he encontrado"):
        return sources
    bus.status.emit("Redactando el informe…")
    user = f"TEMA: {topic}\n" + (f"ENFOQUE PEDIDO: {focus}\n" if focus else "") + f"\nFUENTES:\n{sources[:9000]}"
    body = ""
    with requests.post(settings["ollama_url"] + "/api/chat", stream=True, timeout=(5, 300), json={
        "model": settings["model"], "stream": True, "think": False, "keep_alive": -1,
        "options": {"num_ctx": int(settings["num_ctx"]), "temperature": 0.4, "num_predict": 1400},
        "messages": [{"role": "system", "content": WRITER.format(fecha=now_text())}, {"role": "user", "content": user}]}) as r:
        r.raise_for_status()
        n = 0
        for line in r.iter_lines():
            if not line:
                continue
            d = json.loads(line)
            body += d.get("message", {}).get("content", "")
            n += 1
            if n % 40 == 0:
                bus.status.emit(f"Redactando el informe… {len(body.split())} palabras")
            if d.get("done"):
                break
    body = re.sub(r"<think>.*?</think>", "", body, flags=re.S).strip()
    title = title or topic[:1].upper() + topic[1:80]
    res = write_document(title, body, format or "docx", True)
    resumen = ""
    m = re.search(r"## Resumen\s*\n(.+?)(\n## |\Z)", body, re.S)
    if m:
        resumen = " ".join(m.group(1).split())[:700]
    return (f"{res}. El documento ya está abierto en pantalla.\nAHORA dile al usuario en voz alta, en dos frases, "
            f"lo más importante de este resumen: {resumen}")
