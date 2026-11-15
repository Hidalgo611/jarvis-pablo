"""Herramientas del propio asistente: plan, memoria, recordatorios, rutinas, voces, habilidades, actualizaciones."""
from __future__ import annotations

import json
import threading
import time

from .. import voices
from ..bus import bus
from ..config import VERSION, settings
from ..memory import memory
from . import N, REGISTRY, S, tool

# Lo rellena app.py con referencias a servicios (speaker, updater, skills…)
CTX: dict = {}


@tool("plan", "Para tareas de VARIOS pasos: muestra en pantalla el plan antes de empezar. Llama a esta herramienta primero y luego ejecuta los pasos.",
      {"steps": {"type": "array", "items": {"type": "string"}, "description": "Pasos cortos, en orden"}}, ["steps"],
      label="Planificando")
def plan(steps: list) -> str:
    steps = [str(s) for s in steps][:8]
    bus.plan.emit(steps)
    CTX["plan"] = steps
    return "Plan mostrado. Ejecútalo paso a paso con las herramientas."


@tool("remember", "Guarda en memoria a largo plazo un dato o preferencia del usuario (gustos, nombres, cómo quiere que le hables…).",
      {"fact": S("Dato en una frase")}, ["fact"], label="Memorizando")
def remember(fact: str) -> str:
    memory.add_fact(fact)
    return "Guardado en memoria."


@tool("forget", "Olvida un dato guardado en memoria.", {"fact": S("Dato a olvidar")}, ["fact"], label="Olvidando")
def forget(fact: str) -> str:
    n = memory.forget_fact(fact)
    return f"Olvidados {n} datos." if n else "No tenía guardado nada así."


@tool("what_you_know", "Dice lo que Jarvis sabe y ha aprendido del usuario: datos guardados, hábitos detectados y rutinas.",
      {}, [], label="Consultando memoria")
def what_you_know() -> str:
    from ..learning import detector
    facts = memory.facts()
    pats = detector.find_patterns(min_days=2)
    rts = memory.q("SELECT name FROM routines WHERE enabled=1")
    return json.dumps({"datos": facts, "habitos_detectados": [p["text"] for p in pats],
                       "rutinas_activas": [r[0] for r in rts]}, ensure_ascii=False)


@tool("set_reminder", "Pone un temporizador o recordatorio: Jarvis avisará hablando cuando llegue el momento.",
      {"minutes": N("Dentro de cuántos minutos"), "at": S("O bien hora exacta HH:MM de hoy/mañana"), "text": S("Qué recordar")},
      ["text"], label="Programando recordatorio")
def set_reminder(text: str, minutes: float | None = None, at: str | None = None) -> str:
    now = time.time()
    if at:
        hh, mm = [int(x) for x in at.split(":")[:2]]
        lt = time.localtime()
        due = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
        if due <= now:
            due += 86400
    else:
        due = now + float(minutes or 5) * 60
    memory.q("INSERT INTO reminders(due, text) VALUES(?,?)", (due, text))
    return f"Recordatorio para las {time.strftime('%H:%M', time.localtime(due))}: {text}"


@tool("create_routine", "Crea una rutina automática: a una hora, al encender el PC o con una frase, Jarvis ejecuta acciones (herramientas existentes). Pide confirmación al usuario antes si no lo ha pedido él explícitamente.",
      {"name": S("Nombre corto"),
       "trigger_type": S("Cuándo", ["time", "startup", "phrase"]),
       "time": S("HH:MM si es por hora"),
       "days": S("Días: 'todos', 'laborables', 'finde' o lista 'lun,mar'"),
       "phrase": S("Frase que la dispara, si es por frase"),
       "actions": {"type": "array", "description": "Lista de acciones {tool, args}",
                   "items": {"type": "object", "properties": {"tool": {"type": "string"}, "args": {"type": "object"}}}},
       "announce": S("Lo que dirá Jarvis al ejecutarla")},
      ["name", "trigger_type", "actions"], label=lambda a: f"Creando rutina «{a.get('name', '')}»")
def create_routine(name: str, trigger_type: str, actions: list, time: str | None = None, days: str = "todos",
                   phrase: str | None = None, announce: str = "") -> str:
    bad = [a.get("tool") for a in actions if a.get("tool") not in REGISTRY]
    if bad:
        return f"ERROR: herramientas inexistentes en la rutina: {bad}"
    trig = {"type": trigger_type, "time": time, "days": days, "phrase": (phrase or "").lower()}
    memory.q("INSERT INTO routines(name, trigger, actions, ts) VALUES(?,?,?,?)",
             (name, json.dumps(trig, ensure_ascii=False), json.dumps({"actions": actions, "announce": announce}, ensure_ascii=False),
              __import__("time").time()))
    return f"Rutina «{name}» creada."


@tool("list_routines", "Lista las rutinas automáticas.", {}, [], label="Revisando rutinas")
def list_routines() -> str:
    rows = memory.q("SELECT id, name, trigger, enabled FROM routines")
    if not rows:
        return "No hay rutinas."
    return "\n".join(f"{r[0]}. {r[1]} — {r[2]} {'(activa)' if r[3] else '(desactivada)'}" for r in rows)


@tool("delete_routine", "Borra o desactiva una rutina por su nombre.", {"name": S("Nombre de la rutina")}, ["name"],
      label="Borrando rutina")
def delete_routine(name: str) -> str:
    rows = memory.q("SELECT id, name FROM routines")
    hit = [r for r in rows if name.lower() in r[1].lower()]
    for r in hit:
        memory.q("DELETE FROM routines WHERE id=?", (r[0],))
    return f"Borradas: {', '.join(r[1] for r in hit)}" if hit else "No hay ninguna rutina con ese nombre."


@tool("voices", "Gestiona la voz de Jarvis: listar voces, cambiar de voz, cambiar la velocidad o el efecto (sutil, robot, off).",
      {"action": S("Acción", ["list", "set", "speed", "effect"]),
       "voice": S("Id de la voz (para set), p.ej. jarvis-grave"), "value": S("Velocidad (0.8-1.3) o efecto: sutil/robot/off")},
      ["action"], label="Ajustando mi voz")
def voices_tool(action: str, voice: str | None = None, value: str | None = None) -> str:
    sp = CTX.get("speaker")
    if action == "list":
        return voices.describe()
    if action == "set" and voice:
        vid = next((v for v in voices.CATALOG if voice.lower().replace(" ", "-") in v), None)
        if not vid:
            return "No conozco esa voz. Voces: " + ", ".join(voices.CATALOG)
        settings.set("voice", vid)
        if sp:
            sp.load_voice(vid)
        return f"Voz cambiada a {vid}."
    if action == "speed" and value:
        try:
            settings.set("voice_speed", max(0.7, min(1.4, float(value))))
        except ValueError:
            return "Velocidad no válida."
        return "Velocidad ajustada."
    if action == "effect" and value in ("sutil", "robot", "off"):
        settings.set("voice_fx", value)
        return f"Efecto de voz: {value}."
    return "Acción desconocida."


@tool("settings", "Cambia ajustes de Jarvis: nombre del usuario, cómo llamarle (señor, Pablo…), tiempo de escucha, mostrar subtítulos, esquina del núcleo, modelo de IA.",
      {"key": S("Ajuste", ["user_name", "address", "follow_up_seconds", "end_silence_ms", "show_subtitles", "corner", "model",
                           "wake_threshold", "greet_on_start"]),
       "value": S("Nuevo valor")}, ["key", "value"], label="Cambiando ajustes")
def settings_tool(key: str, value: str) -> str:
    cur = settings[key]
    v: object = value
    if isinstance(cur, bool):
        v = str(value).lower() in ("1", "true", "sí", "si", "on", "verdadero")
    elif isinstance(cur, (int, float)):
        try:
            v = type(cur)(float(value))
        except ValueError:
            return "Valor no válido."
    settings.set(key, v)
    return f"Ajuste {key} = {v}"


@tool("check_updates", "Comprueba si hay una versión nueva de Jarvis y, si el usuario lo ha confirmado, la instala.",
      {"install": {"type": "boolean", "description": "Instalar ya (solo si el usuario ha dicho que sí)"}}, [],
      label="Buscando actualizaciones")
def check_updates(install: bool = False) -> str:
    up = CTX.get("updater")
    if not up:
        return "Las actualizaciones no están configuradas."
    info = up.check(force=True)
    if not info:
        return f"Estoy al día (versión {VERSION})."
    if install:
        threading.Thread(target=up.apply, args=(info,), daemon=True).start()
        return f"Instalando la versión {info['version']}. Me reiniciaré en unos segundos."
    return f"Hay una versión nueva {info['version']}. Novedades: {info['notes'][:600]}"


@tool("create_skill", "Crea una HABILIDAD nueva escribiendo código Python cuando ninguna herramienta existente sirve. Solo con permiso del usuario. El código debe definir SKILL = {'name','description','parameters'} y def run(**kwargs) -> str, y solo puede importar módulos seguros y 'jarvis_api'.",
      {"name": S("nombre_en_minúsculas"), "description": S("Qué hace"), "code": S("Código Python completo")},
      ["name", "description", "code"], label=lambda a: f"Programando habilidad «{a.get('name', '')}»")
def create_skill(name: str, description: str, code: str) -> str:
    sk = CTX.get("skills")
    if not sk:
        return "El sistema de habilidades no está disponible."
    return sk.install(name, description, code)


@tool("undo_skill", "Desactiva una habilidad creada por Jarvis (vuelve atrás).", {"name": S("Nombre")}, ["name"],
      label="Desactivando habilidad")
def undo_skill(name: str) -> str:
    sk = CTX.get("skills")
    return sk.disable(name) if sk else "No disponible."


@tool("about", "Información sobre Jarvis: versión, modelo, voz.", {}, [], label="Consultando sistemas")
def about() -> str:
    return f"J.A.R.V.I.S. versión {VERSION}. Cerebro: {settings['model']}. Voz: {settings['voice']}. Todo funciona en local."
