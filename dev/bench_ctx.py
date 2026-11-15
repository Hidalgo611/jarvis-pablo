import sys, time, requests; sys.path.insert(0,".")
from jarvis import tools; tools.load_all()
from jarvis.brain import Brain
from jarvis.config import settings
b=Brain()
for ctx in [8192, 10240]:
    settings.data["num_ctx"]=ctx
    requests.post("http://localhost:11434/api/generate",json={"model":"qwen3:8b","keep_alive":0}); time.sleep(2)
    for i in range(2):
        p=b._payload(b._base_messages("Cuéntame en tres frases quién es Tony Stark."),False)
        t=time.time(); r=requests.post("http://localhost:11434/api/chat",json=p).json()
        print(ctx, i, "prompt_tokens", r["prompt_eval_count"], "prompt %.2fs"%(r["prompt_eval_duration"]/1e9), "gen %.0f t/s"%(r["eval_count"]/(r["eval_duration"]/1e9)), "total %.2fs"%(time.time()-t))
