"""Jarvis Server: el cerebro de Jarvis en el PC de casa, para que el portátil no tenga que cargar con la IA.

Va delante de Ollama (que se queda escuchando solo en local) y añade:

- Clave compartida (cabecera X-Jarvis-Key) y bloqueo de operaciones peligrosas desde fuera (borrar modelos…).
- MODO JUEGO: si detecta que estás jugando, saca el modelo de la VRAM al momento y, mientras juegas,
  responde en la CPU (`gaming_mode: "cpu"`), con un modelo en la nube (`"cloud"`) o avisa de que está en pausa
  (`"pause"`). Al cerrar el juego vuelve a cargarlo en la gráfica.
- Mantiene el PC despierto (sin suspender) aunque las pantallas se apaguen: modo «standby» con la IA activa.
- Opcional: de noche limita el consumo máximo de la gráfica (nvidia-smi, requiere administrador).
- /jarvis/status: estado en JSON (jugando, modelos cargados…).

Solo usa la biblioteca estándar de Python (psutil opcional, mejora la detección de juegos).
Uso:  pythonw jarvis_server.py            (normal, sin ventana)
      python jarvis_server.py --pantallas  (apaga las pantallas ya)
"""
from __future__ import annotations

import ctypes
import http.client
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "jarvis_server.json"
LOG_PATH = HERE / "jarvis_server.log"
WINDOWS = os.name == "nt"

DEFAULTS = {
    "port": 11500,
    "listen": "0.0.0.0",              # el firewall (instalar_servidor.ps1) solo deja entrar desde Tailscale
    "ollama": "http://127.0.0.1:11434",
    "key": "",                        # se genera sola la primera vez
    "model": "qwen3.5:4b",            # modelo principal (para precargarlo al acabar de jugar)
    "gaming_mode": "cpu",             # cpu | cloud | pause
    "cpu_threads": 4,                 # hilos de CPU para la IA mientras juegas (deja el resto al juego)
    "cloud_model": "",                # modelo «-cloud» de Ollama para gaming_mode=cloud (requiere `ollama signin`)
    "detect_fullscreen": True,        # una app a pantalla completa (que no sea navegador) cuenta como juego
    "extra_games": [],                # nombres de .exe extra que cuentan como juego, p. ej. ["valorant.exe"]
    "not_games": [],                  # .exe que nunca cuentan como juego
    "keep_awake": True,               # no suspender (las pantallas sí se pueden apagar)
    "idle_unload_minutes": 0,         # 0 = el modelo se queda en la gráfica siempre (respuesta inmediata)
    "night": {"start": "01:00", "end": "08:00", "gpu_power_limit_w": 0},   # 0 = no tocar la gráfica de noche
}

GAME_DIRS = ("\\steamapps\\common\\", "\\epic games\\", "\\riot games\\", "\\xboxgames\\", "\\ea games\\",
             "\\ubisoft game launcher\\games\\", "\\battle.net\\", "\\gog galaxy\\games\\", "\\games\\",
             "\\rockstar games\\", "\\electronic arts\\")
LAUNCHERS = {"steam.exe", "steamwebhelper.exe", "steamservice.exe", "epicgameslauncher.exe", "epicwebhelper.exe",
             "riotclientservices.exe", "riotclientux.exe", "riotclientuxrender.exe", "riotclientcrashhandler.exe",
             "vgc.exe", "vgtray.exe", "battle.net.exe", "agent.exe", "eadesktop.exe", "eabackgroundservice.exe",
             "upc.exe", "uplaywebcore.exe", "galaxyclient.exe", "gamebar.exe", "unitycrashhandler64.exe",
             "crashreportclient.exe", "easyanticheat.exe", "beservice.exe", "socialclubhelper.exe",
             "rockstarservice.exe", "launcher.exe", "gameinputsvc.exe"}
NOT_FULLSCREEN_GAMES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vlc.exe", "explorer.exe",
                        "jarvis.exe", "mpc-hc64.exe", "potplayermini64.exe", "obs64.exe", "discord.exe",
                        "applicationframehost.exe", "searchhost.exe", "lockapp.exe", "shellexperiencehost.exe",
                        "textinputhost.exe", "startmenuexperiencehost.exe", "powerpnt.exe", "spotify.exe"}
REMOTE_BLOCKED = ("/api/delete", "/api/create", "/api/copy", "/api/push", "/api/blobs")

log = logging.getLogger("jarvis-server")


# ------------------------------------------------------------------ configuración
def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            night = dict(cfg["night"], **(user.get("night") or {}))
            cfg.update(user)
            cfg["night"] = night
        except Exception:
            log.exception("jarvis_server.json corrupto: uso valores por defecto")
    if not cfg.get("key"):
        cfg["key"] = secrets.token_urlsafe(18)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


CFG: dict = {}
STATE = {"gaming": False, "game": "", "since": 0.0, "night": False, "last_model": "", "last_request": 0.0,
         "requests": 0, "default_power": 0}


# ------------------------------------------------------------------ Ollama
def ollama(method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    u = urllib.parse.urlparse(CFG["ollama"])
    c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        return json.loads(data or b"{}") if r.status < 400 else {"error": data.decode(errors="replace")}
    finally:
        c.close()


def loaded_models() -> list[str]:
    try:
        return [m.get("name") or m.get("model") for m in ollama("GET", "/api/ps").get("models", [])]
    except Exception:
        return []


def unload_all() -> None:
    for m in loaded_models():
        try:
            ollama("POST", "/api/generate", {"model": m, "keep_alive": 0}, timeout=60)
            log.info("descargado de memoria: %s", m)
        except Exception:
            log.exception("descargar %s", m)


def preload_gpu() -> None:
    m = STATE["last_model"] or CFG["model"]
    try:
        ollama("POST", "/api/generate", {"model": m, "keep_alive": -1, "prompt": ""}, timeout=180)
        log.info("precargado en la gráfica: %s", m)
    except Exception:
        log.exception("precargar %s", m)


# ------------------------------------------------------------------ detección de juegos (Windows)
def _foreground_fullscreen_exe() -> str:
    """Nombre del .exe de la ventana delantera si ocupa todo su monitor; si no, ''."""
    if not WINDOWS:
        return ""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd or hwnd in (user32.GetDesktopWindow(), user32.GetShellWindow()):
            return ""

        class RECT(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        wr = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(wr))
        mon = user32.MonitorFromWindow(hwnd, 2)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(mon, ctypes.byref(mi))
        m = mi.rcMonitor
        if not (wr.l <= m.l and wr.t <= m.t and wr.r >= m.r and wr.b >= m.b):
            return ""
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return _exe_name(pid.value)
    except Exception:
        return ""


def _exe_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name().lower()
    except Exception:
        pass
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(520)
        n = ctypes.c_ulong(520)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        k32.CloseHandle(h)
        return os.path.basename(buf.value).lower() if ok else ""
    except Exception:
        return ""


def _processes() -> list[tuple[str, str]]:
    """[(nombre.exe, ruta)] de los procesos en marcha."""
    try:
        import psutil
        out = []
        for p in psutil.process_iter(["name", "exe"]):
            out.append(((p.info.get("name") or "").lower(), (p.info.get("exe") or "").lower()))
        return out
    except ImportError:
        pass
    try:
        r = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=10,
                           creationflags=0x08000000)
        return [(ln.split('","')[0].strip('"').lower(), "") for ln in r.stdout.splitlines() if ln]
    except Exception:
        return []


def detect_game() -> str:
    extra = {x.lower() for x in CFG.get("extra_games") or []}
    never = LAUNCHERS | {x.lower() for x in CFG.get("not_games") or []}
    for name, path in _processes():
        if not name or name in never:
            continue
        if name in extra:
            return name
        if (path and any(d in path for d in GAME_DIRS) and "redist" not in path and "\\launcher\\" not in path
                and "installer" not in name and "setup" not in name and "crash" not in name):
            return name
    if CFG.get("detect_fullscreen"):
        fs = _foreground_fullscreen_exe()
        if fs and fs not in NOT_FULLSCREEN_GAMES and fs not in never:
            return fs
    return ""


def game_watcher() -> None:
    confirm = 0
    while True:
        try:
            g = detect_game()
            if g and not STATE["gaming"]:
                confirm += 1
                if confirm >= 2:                   # 2 lecturas seguidas (~6 s) para no saltar por nada
                    STATE.update(gaming=True, game=g, since=time.time())
                    log.info("MODO JUEGO: %s → libero la VRAM (modo %s)", g, CFG["gaming_mode"])
                    unload_all()
            elif not g and STATE["gaming"]:
                confirm += 1
                if confirm >= 4:                   # ~12 s sin juego: se acabó la partida
                    log.info("fin del modo juego (%s, %d min)", STATE["game"], (time.time() - STATE["since"]) / 60)
                    STATE.update(gaming=False, game="")
                    unload_all()                   # quita la copia de CPU
                    preload_gpu()
            else:
                confirm = 0
            idle = float(CFG.get("idle_unload_minutes") or 0)
            if idle and not STATE["gaming"] and STATE["last_request"] and time.time() - STATE["last_request"] > idle * 60:
                if loaded_models():
                    log.info("sin uso %d min: libero la VRAM", idle)
                    unload_all()
                STATE["last_request"] = 0
        except Exception:
            log.exception("vigilante de juegos")
        time.sleep(3)


# ------------------------------------------------------------------ energía
def keep_awake() -> None:
    """El PC no se suspende, pero las pantallas sí se apagan (ES_SYSTEM_REQUIRED sin ES_DISPLAY_REQUIRED)."""
    if WINDOWS and CFG.get("keep_awake"):
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_AWAYMODE_REQUIRED = 0x80000000, 0x00000001, 0x00000040
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)
        log.info("el PC no se suspenderá mientras el servidor esté activo (las pantallas sí se apagan)")


def monitors_off() -> None:
    if WINDOWS:
        HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER = 0xFFFF, 0x0112, 0xF170
        ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def _in_window(start: str, end: str) -> bool:
    now = time.strftime("%H:%M")
    return (start <= now < end) if start <= end else (now >= start or now < end)


def _nvsmi(*args: str) -> str:
    r = subprocess.run(["nvidia-smi", *args], capture_output=True, text=True, timeout=15,
                       creationflags=0x08000000 if WINDOWS else 0)
    return r.stdout.strip()


def night_watcher() -> None:
    """De noche (opcional) baja el consumo máximo de la gráfica; de día lo devuelve a su valor normal."""
    n = CFG.get("night") or {}
    limit = int(n.get("gpu_power_limit_w") or 0)
    if not limit:
        return
    try:
        STATE["default_power"] = int(float(_nvsmi("--query-gpu=power.default_limit", "--format=csv,noheader,nounits")
                                           .splitlines()[0]))
    except Exception:
        log.warning("no puedo leer el consumo de la gráfica (¿nvidia-smi?); sin modo noche de gráfica")
        return
    while True:
        night = _in_window(n.get("start", "01:00"), n.get("end", "08:00"))
        if night != STATE["night"]:
            STATE["night"] = night
            w = limit if night else STATE["default_power"]
            try:
                _nvsmi("-pl", str(w))
                log.info("gráfica limitada a %d W (%s)", w, "noche" if night else "día")
            except Exception:
                log.exception("nvidia-smi -pl (hace falta ejecutar el servidor como administrador)")
        time.sleep(60)


# ------------------------------------------------------------------ proxy
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "JarvisServer/1.0"

    def log_message(self, fmt, *args):  # silencio: ya logueamos lo importante
        pass

    def _local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self) -> bool:
        if self._local():
            return True
        if secrets.compare_digest(self.headers.get("X-Jarvis-Key", ""), CFG["key"]):
            return True
        self._json(401, {"error": "Falta la clave del Jarvis Server (ajustes de Jarvis → Clave del servidor)."})
        return False

    def do_GET(self):
        self._proxy("GET")

    def do_HEAD(self):
        self._proxy("HEAD")

    def do_POST(self):
        self._proxy("POST")

    def do_DELETE(self):
        self._proxy("DELETE")

    def _rewrite(self, path: str, body: bytes) -> tuple[bytes, dict | None]:
        """Ajusta la petición según el modo (juego, inactividad). Devuelve (cuerpo, error_503 o None)."""
        if path not in ("/api/chat", "/api/generate", "/api/embed", "/api/embeddings") or not body:
            return body, None
        try:
            d = json.loads(body)
        except Exception:
            return body, None
        if d.get("keep_alive") == 0:
            return body, None
        STATE["last_request"] = time.time()
        STATE["requests"] += 1
        if d.get("model") and not STATE["gaming"]:
            STATE["last_model"] = d["model"]
        if STATE["gaming"]:
            mode = CFG.get("gaming_mode", "cpu")
            if mode == "pause":
                return body, {"error": "Ahora mismo el servidor está en modo juego; vuelvo en cuanto termine la partida."}
            if mode == "cloud" and CFG.get("cloud_model"):
                d["model"] = CFG["cloud_model"]
            else:   # cpu: la VRAM queda entera para el juego
                opts = d.setdefault("options", {})
                opts["num_gpu"] = 0
                opts["num_thread"] = int(CFG.get("cpu_threads") or 4)
                d["keep_alive"] = "10m"
        elif CFG.get("idle_unload_minutes"):
            d["keep_alive"] = f"{int(CFG['idle_unload_minutes'])}m"
        return json.dumps(d).encode(), None

    def _proxy(self, method: str) -> None:
        path = self.path
        if path.startswith("/jarvis/status"):
            if not self._auth():
                return
            self._json(200, {"gaming": STATE["gaming"], "game": STATE["game"], "gaming_mode": CFG["gaming_mode"],
                             "night": STATE["night"], "loaded": loaded_models(), "requests": STATE["requests"]})
            return
        if not self._auth():
            return
        if not self._local() and path.split("?")[0].startswith(REMOTE_BLOCKED):
            self._json(403, {"error": "Operación no permitida desde fuera del servidor."})
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        body, busy = self._rewrite(path.split("?")[0], body)
        if busy:
            self._json(503, busy)
            return
        u = urllib.parse.urlparse(CFG["ollama"])
        up = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=600)
        try:
            up.request(method, path, body=body if method in ("POST", "DELETE") else None,
                       headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
            r = up.getresponse()
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() not in ("transfer-encoding", "content-length", "connection"):
                    self.send_header(k, v)
            if method == "HEAD":
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                            # el portátil cortó (p. ej. le interrumpieron): normal
        except (ConnectionRefusedError, OSError) as e:
            try:
                self._json(502, {"error": f"Ollama no responde en el servidor: {e}"})
            except Exception:
                pass
        finally:
            up.close()


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stdout and not sys.executable.lower().endswith("pythonw.exe"):
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)


def main() -> int:
    setup_logging()
    if "--pantallas" in sys.argv:
        monitors_off()
        return 0
    CFG.update(load_config())
    if "--clave" in sys.argv:
        print(CFG["key"])
        return 0
    keep_awake()
    threading.Thread(target=game_watcher, name="juegos", daemon=True).start()
    threading.Thread(target=night_watcher, name="noche", daemon=True).start()
    srv = ThreadingHTTPServer((CFG["listen"], int(CFG["port"])), Handler)
    srv.daemon_threads = True
    log.info("Jarvis Server escuchando en %s:%s → %s (modo juego: %s)", CFG["listen"], CFG["port"], CFG["ollama"],
             CFG["gaming_mode"])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
