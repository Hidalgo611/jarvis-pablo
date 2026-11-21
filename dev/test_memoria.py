"""Prueba sin GPU de la memoria de conversación, correcciones de nombre e interrupciones (con dev/fake_ollama.py).

    python dev/fake_ollama.py 11434 dev/req.log &
    python dev/test_memoria.py
"""
import json, os, sys, tempfile, time
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, ".")
from jarvis import brain as B, tools  # noqa: E402
import jarvis.tools.assistant  # noqa: E402,F401
from jarvis.config import settings  # noqa: E402
from jarvis.memory import memory  # noqa: E402

LOG = "dev/req.log"


def last_request() -> dict:
    return [json.loads(l) for l in open(LOG, encoding="utf-8") if '"stream": true' in l][-1]["body"]


def run(b, text):
    return b.run(text, lambda s: None, lambda *a: None, lambda *a: None)


ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("OK   " if cond else "FALLA"), name)


memory.add_fact("El usuario se llama Antonio")
memory.add_fact("Le gusta el Barça")
b = B.Brain()
run(b, "Hola, me voy al trabajo")
run(b, "No, me llamo Pablo, no Antonio")
req = last_request()
check("nombre corregido en ajustes", settings["user_name"] == "Pablo")
check("dato erróneo borrado", not any("Antonio" in f for f in memory.facts()))
check("sin «Antonio» en el prompt", "Antonio" not in req["messages"][0]["content"])
check("recuerda el turno anterior", any("me voy al trabajo" in m["content"] for m in req["messages"][1:-1]))
check("nota de corrección", "Nota interna" in req["messages"][-1]["content"])
# muchos turnos: ventana + resumen
now = time.time()
memory.set("turns", [{"ts": now - (30 - i) * 240, "messages": [
    {"role": "user", "content": f"[contexto, no lo leas: x] mensaje {i}"},
    {"role": "assistant", "content": f"respuesta {i}"}]} for i in range(30)])
keep, out = b._window()
check("ventana limitada", sum(m["role"] == "user" for m in keep) == B.MAX_TURNS)
time.sleep(4)  # el resumen se hace en segundo plano tras cada turno
b._summarize()
check("resumen generado", memory.summary().get("text"))
check("resumen en el prompt", "RESUMEN DE LO QUE HABLASTEIS" in B.system_prompt())
# interrupción
run(b, "cuéntame algo")
b.mark_interrupted("¿Has visto el último resultado del Barça?")
run(b, "No, para")
check("sabe que le interrumpieron", "te ha interrumpido" in last_request()["messages"][-1]["content"])
# rutinas
from jarvis.tools.assistant import create_routine  # noqa: E402
check("rutina con orden natural", "09:00" in create_routine("Gmail", "time", "abre Gmail", "9"))
check("herramientas de rutina ofrecidas", "create_routine" in tools.select("a partir de ahora cada mañana ábreme el correo"))
sys.exit(0 if ok else 1)
