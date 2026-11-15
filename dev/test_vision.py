import sys, time, json, subprocess; sys.path.insert(0,".")
from PySide6.QtCore import QCoreApplication
app=QCoreApplication([])
from jarvis import tools; tools.load_all()
from jarvis.memory import memory
from jarvis.brain import Brain
subprocess.Popen(["calc.exe"]); time.sleep(3)
memory.q("DELETE FROM kv WHERE key='turns'")
b=Brain()
for q in sys.argv[1:]:
    t0=time.time(); print("\n=== USER:", q)
    b.run(q, lambda s: print(f"  [{time.time()-t0:4.1f}s] SAY:", s),
          lambda n,a,l: print(f"  [{time.time()-t0:4.1f}s] TOOL {n} {json.dumps(a,ensure_ascii=False)[:150]}"),
          lambda n,r: print(f"  [{time.time()-t0:4.1f}s]   -> {r[:150]!r}"))
