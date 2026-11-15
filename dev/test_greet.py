import sys, time; sys.path.insert(0,".")
from jarvis import tools; tools.load_all()
from jarvis.memory import memory
from jarvis.brain import Brain, context_prefix
now=time.time()
memory.q("DELETE FROM history WHERE ts > ?", (now-86400,))
memory.q("INSERT INTO history(role,text,ts) VALUES('user',?,?)", ("Jarvis, me voy a trabajar, luego hablamos", now-3*3600-600))
memory.q("INSERT INTO history(role,text,ts) VALUES('assistant',?,?)", ("¡Que te vaya genial, Pablo! Aquí estaré.", now-3*3600-590))
memory.q("DELETE FROM kv WHERE key='turns'")
print(context_prefix())
from jarvis.experience import Greeter
g=Greeter(Brain(), lambda: False)
for i in range(3):
    t=time.time(); print("%.1fs"%(time.time()-t), g._generate())
