import requests, time, sys
big = "Contexto de herramientas y documentos. " * 350  # ~2.5k tokens
for model in sys.argv[1:]:
    for i in range(2):
        t=time.time()
        r=requests.post("http://localhost:11434/api/chat",json={"model":model,"think":False,"stream":False,"keep_alive":"5m","options":{"num_ctx":8192},
          "messages":[{"role":"system","content":big},{"role":"user","content":"Escribe un párrafo de 120 palabras sobre Iron Man."}]}).json()
        pe=r["prompt_eval_count"]/(r["prompt_eval_duration"]/1e9); ge=r["eval_count"]/(r["eval_duration"]/1e9)
        print(model,i,"total %.1fs load %.1fs prompt %d tok @ %.0f t/s, gen %d tok @ %.0f t/s"%(time.time()-t,r["load_duration"]/1e9,r["prompt_eval_count"],pe,r["eval_count"],ge))
    import subprocess; print(subprocess.run(["ollama","ps"],capture_output=True,text=True).stdout)
    requests.post("http://localhost:11434/api/generate",json={"model":model,"keep_alive":0})
