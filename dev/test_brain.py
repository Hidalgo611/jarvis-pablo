import sys, time, json, logging; sys.path.insert(0,".")
logging.basicConfig(level=logging.WARNING)
from PySide6.QtCore import QCoreApplication
app=QCoreApplication([])
from jarvis import tools; tools.load_all()
import jarvis.tools.system as _s; _s.move_mouse_visibly = lambda *a, **k: None
from jarvis.tools import system; system.build_index()
from jarvis.brain import Brain
b=Brain()
for q in sys.argv[1:]:
    print("\n=== USER:", q); t0=time.time(); first=[None]
    def say(s):
        if first[0] is None: first[0]=time.time()-t0
        print(f"  [{time.time()-t0:5.1f}s] SAY:", s)
    def on_tool(n,a,l): print(f"  [{time.time()-t0:5.1f}s] TOOL {n} {json.dumps(a,ensure_ascii=False)[:300]}")
    def done(n,r): print(f"  [{time.time()-t0:5.1f}s]   -> {r[:200]!r}")
    final=b.run(q,say,on_tool,done)
    print(f"  total {time.time()-t0:.1f}s, first speech {first[0]}")
