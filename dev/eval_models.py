import sys, time, json; sys.path.insert(0,".")
from jarvis import tools; tools.load_all()
from jarvis.config import settings
from jarvis.memory import memory
import jarvis.brain as B
cases=[("abre spotify",{"open_app"}),("busca a Ibai en YouTube",{"youtube:search"}),("pon el tráiler de Dune 3",{"youtube:play"}),
("abre el canal de AuronPlay y ve a la sección de shorts",{"youtube:open_channel"}),("sube el volumen al 60",{"system"}),
("búscame las noticias de hoy del Barça y hazme un PDF con el resumen",{"research_report"}),("recuérdame en 10 minutos sacar la pizza",{"set_reminder"}),
("¿qué tal estás?",set()),("me encanta el rock de los ochenta",{"remember"}),("cierra el bloc de notas",{"close_app"}),
("haz una captura de pantalla",{"system"}),("¿quién ganó el último Mundial de fútbol?",{"web_search","web_research"}),("revisa si tengo correos nuevos en gmail",{"email_check"}),("¿qué tengo hoy?",{"daily_briefing","calendar_today"}),("clica en el botón de aceptar",{"screen_click"}),("¿qué hay en mi pantalla?",{"look_at_screen"}),("léeme el último whatsapp",{"notifications"})]
calls=[]
def fake(name,args):
    calls.append(name+(":"+args.get("action","") if name=="youtube" else "")); return "Hecho."
tools.execute=fake
for model in sys.argv[1:]:
    settings.data["model"]=model; ok=0; tt=0
    br=B.Brain()
    for q,exp in cases:
        memory.q("DELETE FROM kv WHERE key='turns'"); calls.clear(); t=time.time()
        br.run(q, lambda s:None, lambda n,a,l:None, lambda n,r:None); dt=time.time()-t; tt+=dt
        got=set(calls[:1]) if calls else set()
        good=(not exp and not calls) or (exp and any(c in exp or c.split(":")[0] in exp for c in calls[:2]))
        ok+=good; print(f"{model:11s} {'OK ' if good else 'BAD'} {dt:4.1f}s {q[:45]:45s} -> {calls[:3]}")
    print(f"== {model}: {ok}/{len(cases)} correctas, {tt:.0f}s total\n")
