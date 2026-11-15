import json, time, requests, sys
tools=[{"type":"function","function":{"name":"open_app","description":"Abre un programa","parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}},
{"type":"function","function":{"name":"web_search","description":"Busca en internet y devuelve texto de resultados","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
{"type":"function","function":{"name":"create_word","description":"Crea un documento Word","parameters":{"type":"object","properties":{"title":{"type":"string"},"content":{"type":"string"}},"required":["title","content"]}}}]
for model in sys.argv[1:]:
    for q in ["abre el spotify porfa","busca qué está pasando con la economía y házme un resumen en un word","¿qué tal estás jarvis?"]:
        t=time.time()
        r=requests.post("http://localhost:11434/api/chat",json={"model":model,"think":False,"stream":False,"keep_alive":"10m","messages":[{"role":"system","content":"Eres Jarvis, asistente de Pablo. Responde en español, breve."},{"role":"user","content":q}],"tools":tools}).json()
        m=r["message"]; ev=r.get("eval_count",0); ed=r.get("eval_duration",1)/1e9
        print(model,"|",q[:30],"| %.2fs"%(time.time()-t),"| %.0f tok/s"%(ev/ed if ed else 0),"|",json.dumps(m.get("tool_calls"),ensure_ascii=False)[:150],"|",m.get("content","")[:80])
