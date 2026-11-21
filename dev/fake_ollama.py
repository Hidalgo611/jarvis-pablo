# Ollama falso: guarda cada petición y responde en streaming
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
LOG = sys.argv[2]
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self,*a): pass
    def do_GET(self):
        d = json.dumps({"models":[{"name":"qwen3.5:4b"}], "version":"x"}).encode()
        self.send_response(200); self.send_header("Content-Length",str(len(d))); self.end_headers(); self.wfile.write(d)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        open(LOG,"a").write(json.dumps({"path":self.path,"body":body},ensure_ascii=False)+"\n")
        msgs = body.get("messages") or []
        last = msgs[-1]["content"] if msgs else ""
        if "Resume para tu memoria" in last: reply = "Pablo se ha ido al trabajo. Corrigió su nombre: se llama Pablo."
        else: reply = "Perdona, tienes razón. ¿Qué tal en el trabajo?"
        if not body.get("stream", True):
            d = json.dumps({"message":{"role":"assistant","content":reply},"done":True}).encode()
            self.send_response(200); self.send_header("Content-Length",str(len(d))); self.end_headers(); self.wfile.write(d); return
        self.send_response(200); self.send_header("Content-Type","application/x-ndjson"); self.send_header("Transfer-Encoding","chunked"); self.end_headers()
        for w in reply.split(" "):
            line = (json.dumps({"message":{"role":"assistant","content":w+" "},"done":False})+"\n").encode()
            self.wfile.write(b"%x\r\n%s\r\n"%(len(line),line))
        line=(json.dumps({"message":{"role":"assistant","content":""},"done":True})+"\n").encode()
        self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n"%(len(line),line))
ThreadingHTTPServer(("127.0.0.1",int(sys.argv[1])),H).serve_forever()
