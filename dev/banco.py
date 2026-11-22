"""Banco de pruebas del cerebro de Jarvis con el MODELO REAL (Ollama) y las acciones de Windows simuladas.

Comprueba en cada frase: qué herramienta usa y con qué datos, qué contesta, si inventa o se va del tema, y cuánto tarda.

    python dev/banco.py                 # todos los escenarios
    python dev/banco.py volumen nombre  # solo los que contienen esas palabras
Informe: dev/banco_informe.md (y .json)
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time

os.environ.setdefault("LOCALAPPDATA", tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stubs_windows  # noqa: E402

stubs_windows.install()

from jarvis import tools  # noqa: E402
from jarvis.config import settings  # noqa: E402

if os.environ.get("JARVIS_MODEL"):           # comparar modelos: JARVIS_MODEL=jarvis-pablo python dev/banco.py
    settings.data["model"] = os.environ["JARVIS_MODEL"]
from jarvis.memory import memory  # noqa: E402

tools.load_all()
from jarvis.brain import Brain  # noqa: E402

REAL = {"remember", "forget", "what_you_know", "create_routine", "list_routines", "delete_routine", "set_reminder",
        "settings", "about"}
STATE = {"volume": 50}


def fake(name: str, args: dict) -> str:
    a = args or {}
    if name == "system":
        act, v = a.get("action", ""), a.get("value")
        try:
            v = float(str(v).strip(" %")) if v is not None else None
        except ValueError:
            v = None
        if act == "volume_set" and v is not None:
            STATE["volume"] = int(v)
        elif act == "volume_up":
            STATE["volume"] = min(100, STATE["volume"] + int(v or 10))
        elif act == "volume_down":
            STATE["volume"] = max(0, STATE["volume"] - int(v or 10))
        elif act.startswith("volume"):
            return "ERROR: falta el valor."
        if act.startswith("volume"):
            return f"Volumen al {STATE['volume']} %."
        return "Hecho."
    if name == "youtube":
        act = a.get("action", "")
        if act in ("play", "play_title"):
            return f"Reproduciendo: {a.get('query') or a.get('title') or 'vídeo'} (YouTube)"
        if act == "search":
            return "Resultados en pantalla: 1. Ibai reacciona… 2. Lo mejor de Ibai 2026… 3. Ibai y Kun en directo…"
        if act == "fullscreen":
            return "Vídeo en pantalla completa."
        return "Hecho."
    if name == "open_app":
        return f"Abierto: {a.get('name', '')}."
    if name == "close_app":
        return f"Cerrado: {a.get('name', '')}."
    if name == "open_website":
        return f"Abierta: {a.get('target', '')}"
    if name in ("web_search", "web_research"):
        q = str(a.get("query", "")).lower()
        if "tiempo" in q or "clima" in q:
            return ("1. El tiempo en Madrid hoy: 14 °C, nubes y claros, máxima 17 °C. (aemet.es)\n"
                    "2. Previsión Madrid: lluvia débil por la tarde. (eltiempo.es)")
        return f"1. Resultados sobre «{q}»: en Venus un día dura más que un año (243 días terrestres). (nasa.gov)"
    if name == "research_report":
        return "Documento creado: Informe.pdf. El documento ya está abierto en pantalla. Lo más importante: X sube un 12 %."
    if name == "plan":
        return "Plan mostrado. Ejecútalo paso a paso con las herramientas."
    if name == "daily_briefing":
        return "Hoy: 2 correos nuevos (Amazon, banco). Agenda: dentista a las 17:00. Tiempo: 14 °C."
    return "Hecho."


CALLS: list = []
_orig = tools.execute


def execute(name, args):
    if name in REAL:
        out = _orig(name, args)
    else:
        out = fake(name, args)
    CALLS.append((name, args, out))
    return out


tools.execute = execute
import jarvis.brain as _b  # noqa: E402

_b.tools.execute = execute

# ---------------------------------------------------------------- escenarios
# expect: lista de (herramienta, {arg: valor | "re:regex"}) que DEBEN aparecer; none=True → no debe usar acciones
S = [
    {"name": "youtube_pantalla_completa", "turns": [
        {"say": "Ponme un vídeo de Ibai en YouTube", "expect": [("youtube", {"action": "re:play|search"})]},
        {"say": "Ponlo en pantalla completa", "expect": [("youtube", {"action": "fullscreen"})]},
    ]},
    {"name": "volumen_10", "turns": [
        {"say": "Baja el volumen al 10%", "expect": [("system", {"action": "volume_set", "value": "re:^10(\\.0)?$"})],
         "require": [r"10|diez"]},
        {"say": "Súbelo un poco", "expect": [("system", {"action": "re:volume_up|volume_set"})]},
    ]},
    {"name": "trabajo_sin_cambiar_de_tema", "facts": ["Le gusta el Barça", "Juega al Valorant"], "turns": [
        {"say": "Bueno Jarvis, me voy al trabajo", "none": True, "forbid": [r"bar[cç]a|f[uú]tbol|partido|valorant"],
         "max_words": 35},
        {"say": "Sí, hoy tengo turno de tarde", "none": True, "forbid": [r"bar[cç]a|f[uú]tbol|partido|valorant"]},
    ]},
    {"name": "nombre_corregido", "facts": ["El usuario se llama Antonio"], "user_name": "Antonio", "turns": [
        {"say": "Hola Jarvis", "none": True},
        {"say": "No, que me llamo Pablo, no Antonio", "forbid": [r"\bAntonio\b(?!.*Pablo)"], "require": [r"Pablo|perd[oó]n|razón"]},
        {"say": "¿Cómo me llamo?", "none": True, "require": [r"Pablo"], "forbid": [r"Antonio"]},
    ]},
    {"name": "memoria_reciente", "turns": [
        {"say": "Esta tarde tengo examen de matemáticas a las seis", "forbid": [r"bar[cç]a"]},
        {"say": "Abre Spotify", "expect": [("open_app", {"name": "re:(?i)spotify"})]},
        {"say": "Oye, ¿a qué hora te dije que tenía el examen?", "none": True, "require": [r"seis|6|18"]},
    ]},
    {"name": "rutina_gmail_9", "turns": [
        {"say": "A partir de ahora, todos los días a las 9 de la mañana ábreme Gmail",
         "expect": [("create_routine", {"trigger_type": "time", "time": "re:^0?9(:00)?$"})], "require": [r"9|nueve"]},
        {"say": "¿Qué rutinas tengo?", "expect": [("list_routines", {})], "require": [r"(?i)gmail"]},
    ]},
    {"name": "recordatorio", "turns": [
        {"say": "Recuérdame en diez minutos que saque la ropa de la lavadora",
         "expect": [("set_reminder", {"minutes": "re:^10(\\.0)?$"})]},
    ]},
    {"name": "olvidar_dato", "facts": ["Le gusta el Barça"], "turns": [
        {"say": "Olvida lo del Barça, que ya no me gusta el fútbol", "expect": [("forget", {})]},
    ]},
    {"name": "tiempo_actual", "turns": [
        {"say": "¿Qué tiempo hace hoy en Madrid?", "expect": [("re:web_search|web_research", {})], "require": [r"14|catorce"]},
    ]},
    {"name": "abrir_cerrar", "turns": [
        {"say": "Abre Discord", "expect": [("open_app", {"name": "re:(?i)discord"})]},
        {"say": "Ciérralo", "expect": [("close_app", {"name": "re:(?i)discord"})]},
    ]},
    {"name": "charla_estado_animo", "turns": [
        {"say": "Estoy un poco cansado hoy", "none": True, "max_words": 40, "forbid": [r"bar[cç]a|f[uú]tbol"]},
    ]},
    {"name": "interrupcion_no", "turns": [
        {"say": "Cuéntame un dato curioso sobre el espacio"},
        {"say": "No, para, mejor pon música relajante", "interrupt": "Sabías que en Venus un día dura más que",
         "expect": [("re:youtube|open_app", {})]},
    ]},
]


def _match(val, want) -> bool:
    if isinstance(want, str) and want.startswith("re:"):
        return re.search(want[3:], str(val if val is not None else "")) is not None
    return str(val).lower() == str(want).lower()


def check_turn(t: dict, calls: list, reply: str) -> list[str]:
    errs = []
    names = [c[0] for c in calls]
    for tool, args in t.get("expect", []):
        ok = False
        for n, a, _ in calls:
            if _match(n, tool) and all(_match((a or {}).get(k), v) for k, v in args.items()):
                ok = True
        if not ok:
            errs.append(f"esperaba {tool} {args}; usó {[(n, a) for n, a, _ in calls] or 'nada'}")
    if t.get("none"):
        acts = [n for n in names if n not in ("remember", "forget", "what_you_know", "settings")]
        if acts:
            errs.append(f"no debía hacer acciones; usó {acts}")
    for rx in t.get("forbid", []):
        if re.search(rx, reply, re.I | re.S):
            errs.append(f"dice algo prohibido /{rx}/")
    for rx in t.get("require", []):
        if not re.search(rx, reply, re.I):
            errs.append(f"falta /{rx}/ en la respuesta")
    if t.get("max_words") and len(reply.split()) > t["max_words"]:
        errs.append(f"demasiado largo ({len(reply.split())} palabras)")
    if not reply.strip():
        errs.append("no ha dicho nada")
    # reglas generales para todas las frases
    if re.search(r"\b20\d\d\b|dos mil veinti", reply) and not re.search(r"20\d\d", t["say"]):
        errs.append("dice el año sin venir a cuento")
    if re.search(r"(?i)\b(veint[ií]|veintena) por ciento|\bveint[ií]\b", reply):
        errs.append("número mal escrito")
    return errs


def reset(sc: dict) -> None:
    for tb in ("facts", "history", "events", "routines", "proposals", "reminders", "kv"):
        memory.q(f"DELETE FROM {tb}")
    settings.set("user_name", sc.get("user_name", "Pablo"))
    settings.set("address", sc.get("user_name", "Pablo"))
    for f in sc.get("facts", []):
        memory.add_fact(f)
    STATE["volume"] = 50


def main() -> int:
    flt = [a.lower() for a in sys.argv[1:]]
    scs = [s for s in S if not flt or any(f in s["name"] for f in flt)]
    brain = Brain()
    results, t_all = [], time.time()
    for sc in scs:
        reset(sc)
        print(f"\n### {sc['name']}", flush=True)
        for t in sc["turns"]:
            if t.get("interrupt"):
                brain.mark_interrupted(t["interrupt"])
            CALLS.clear()
            said, t0, first = [], time.time(), [None]

            def say(s):
                if first[0] is None:
                    first[0] = time.time() - t0
                said.append(s)
            brain.run(t["say"], say, lambda *a: None, lambda *a: None)
            dt = time.time() - t0
            from jarvis.tts import clean_for_speech
            reply = clean_for_speech(" ".join(said))   # lo que sonaría por el altavoz
            errs = check_turn(t, list(CALLS), reply)
            r = {"scenario": sc["name"], "say": t["say"], "reply": reply, "tools": [(n, a) for n, a, _ in CALLS],
                 "seconds": round(dt, 1), "first_speech": round(first[0], 1) if first[0] else None, "errors": errs}
            results.append(r)
            print(f"  {'OK ' if not errs else 'MAL'} [{dt:5.1f}s] «{t['say']}»\n      → {reply[:200]}\n"
                  f"      herramientas: {r['tools']}" + "".join(f"\n      ✗ {e}" for e in errs), flush=True)
            time.sleep(2.5)   # deja que termine el resumen en segundo plano
    ok = sum(not r["errors"] for r in results)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "banco_informe.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    with open(os.path.join(here, "banco_informe.md"), "w", encoding="utf-8") as f:
        f.write(f"# Banco de pruebas: {ok}/{len(results)} frases bien · modelo {settings['model']} · "
                f"{time.strftime('%Y-%m-%d %H:%M')} · {time.time() - t_all:.0f} s\n\n")
        for r in results:
            f.write(f"- {'✅' if not r['errors'] else '❌'} **{r['scenario']}** «{r['say']}» ({r['seconds']} s)\n"
                    f"  - Jarvis: {r['reply']}\n  - Herramientas: `{r['tools']}`\n"
                    + "".join(f"  - ✗ {e}\n" for e in r["errors"]))
    print(f"\nRESULTADO: {ok}/{len(results)} frases bien")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
