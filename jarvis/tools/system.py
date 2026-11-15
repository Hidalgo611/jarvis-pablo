"""Herramientas del sistema: programas, archivos, volumen, ratón, teclado, pantalla."""
from __future__ import annotations

import base64
import ctypes
import difflib
import io
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import psutil

from ..bus import bus
from ..config import SHOTS_OUT, settings
from ..memory import memory
from . import N, S, tool

log = logging.getLogger("system")
CREATE_NO_WINDOW = 0x08000000
user32 = ctypes.windll.user32

# ------------------------------------------------------------------ índice de programas
ALIASES = {
    "navegador": "__browser__", "internet": "__browser__",
    "bloc de notas": "notepad.exe", "notepad": "notepad.exe", "notas": "notepad.exe",
    "calculadora": "calc.exe", "explorador": "explorer.exe", "explorador de archivos": "explorer.exe",
    "archivos": "explorer.exe", "mis archivos": "explorer.exe",
    "configuración": "ms-settings:", "ajustes": "ms-settings:", "configuracion": "ms-settings:",
    "administrador de tareas": "taskmgr.exe", "paint": "mspaint.exe", "terminal": "wt.exe",
    "cmd": "cmd.exe", "símbolo del sistema": "cmd.exe", "powershell": "powershell.exe",
    "panel de control": "control.exe", "recortes": "ms-screenclip:", "cámara": "microsoft.windows.camera:",
    "tienda": "ms-windows-store:", "microsoft store": "ms-windows-store:", "spotify": "spotify:",
    "whatsapp": "whatsapp:", "steam": "steam://open/main", "correo": "outlookmail:",
    "xbox": "xbox:", "mapas": "bingmaps:", "reloj": "ms-clock:", "alarmas": "ms-clock:",
    "bluetooth": "ms-settings:bluetooth", "wifi": "ms-settings:network-wifi", "sonido": "ms-settings:sound",
    "pantalla": "ms-settings:display", "fondo de pantalla": "ms-settings:personalization-background",
    "actualizaciones de windows": "ms-settings:windowsupdate", "windows update": "ms-settings:windowsupdate",
}

_index: dict[str, str] = {}
_index_ready = threading.Event()


def _scan_start_menu() -> dict[str, str]:
    out: dict[str, str] = {}
    roots = [Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
             Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"]
    for root in roots:
        if root.exists():
            for p in root.rglob("*.lnk"):
                n = p.stem.lower()
                if any(w in n for w in ("uninstall", "desinstalar", "readme", "léame", "help", "ayuda")):
                    continue
                out.setdefault(n, str(p))
    return out


def _scan_start_apps() -> dict[str, str]:
    cached = memory.get("start_apps")
    if cached and time.time() - cached.get("ts", 0) < 86400:
        return cached["apps"]
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
                           capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW,
                           encoding="utf-8", errors="ignore")
        data = json.loads(r.stdout or "[]")
        apps = {d["Name"].lower(): "shell:AppsFolder\\" + d["AppID"] for d in data if d.get("Name")}
        memory.set("start_apps", {"ts": time.time(), "apps": apps})
        return apps
    except Exception:
        log.exception("Get-StartApps falló")
        return {}


def build_index() -> None:
    def work():
        idx = _scan_start_apps()
        idx.update(_scan_start_menu())
        _index.clear()
        _index.update(idx)
        _index_ready.set()
        log.info("índice de programas: %d entradas", len(_index))
    threading.Thread(target=work, name="app-index", daemon=True).start()


def find_app(name: str) -> tuple[str, str] | None:
    n = name.lower().strip().removeprefix("el ").removeprefix("la ")
    if n in ALIASES:
        return n, ALIASES[n]
    _index_ready.wait(8)
    if n in _index:
        return n, _index[n]
    subs = [k for k in _index if n in k]
    if subs:
        k = min(subs, key=len)
        return k, _index[k]
    close = difflib.get_close_matches(n, list(_index) + list(ALIASES), n=1, cutoff=0.6)
    if close:
        k = close[0]
        return k, _index.get(k) or ALIASES[k]
    return None


def launch(target: str) -> None:
    if target == "__browser__":
        from .browser import browser
        browser.open_url("https://duckduckgo.com")
        return
    if target.startswith("shell:AppsFolder"):
        subprocess.Popen(["explorer.exe", target], creationflags=CREATE_NO_WINDOW)
    else:
        os.startfile(target)


@tool("open_app", "Abre un programa o aplicación de Windows por su nombre (Spotify, Discord, Steam, Word, calculadora, ajustes…).",
      {"name": S("Nombre del programa")}, ["name"], label=lambda a: f"Abriendo {a.get('name', '')}")
def open_app(name: str) -> str:
    found = find_app(name)
    if not found:
        return f"No encuentro ningún programa llamado '{name}' instalado."
    key, target = found
    launch(target)
    memory.event("app_open", key)
    return f"Abierto: {key}."


PROTECTED = {"explorer.exe", "csrss.exe", "winlogon.exe", "svchost.exe", "lsass.exe", "services.exe",
             "dwm.exe", "system", "smss.exe", "wininit.exe", "jarvis.exe", "python.exe", "ollama.exe"}


@tool("close_app", "Cierra un programa abierto (de forma normal, como pulsar la X).",
      {"name": S("Nombre del programa o proceso")}, ["name"], label=lambda a: f"Cerrando {a.get('name', '')}")
def close_app(name: str) -> str:
    n = name.lower().replace(" ", "")
    hits = set()
    for p in psutil.process_iter(["name"]):
        pn = (p.info["name"] or "").lower()
        if pn in PROTECTED:
            continue
        if n in pn.replace(" ", "") or difflib.SequenceMatcher(None, n, pn.removesuffix(".exe")).ratio() > 0.8:
            hits.add(pn)
    if not hits:
        return f"No hay ningún programa abierto que coincida con '{name}'."
    for pn in hits:
        subprocess.run(["taskkill", "/IM", pn], capture_output=True, creationflags=CREATE_NO_WINDOW)
    return "Cerrado: " + ", ".join(sorted(hits))


FOLDERS = {"descargas": "Downloads", "documentos": "Documents", "escritorio": "Desktop", "imágenes": "Pictures",
           "imagenes": "Pictures", "fotos": "Pictures", "música": "Music", "musica": "Music", "vídeos": "Videos",
           "videos": "Videos", "jarvis": "Documents/Jarvis"}


def _user_folder(name: str) -> Path | None:
    n = name.lower().strip()
    for k, v in FOLDERS.items():
        if k in n:
            return Path.home() / v
    p = Path(name)
    return p if p.exists() else None


@tool("open_folder", "Abre una carpeta en el explorador (descargas, documentos, escritorio, imágenes, música, vídeos o una ruta).",
      {"name": S("Carpeta o ruta")}, ["name"], label=lambda a: f"Abriendo carpeta {a.get('name', '')}")
def open_folder(name: str) -> str:
    p = _user_folder(name)
    if not p:
        return f"No conozco la carpeta '{name}'."
    os.startfile(p)
    return f"Abierta {p}"


@tool("find_files", "Busca archivos por nombre en las carpetas del usuario (escritorio, documentos, descargas, imágenes, vídeos, música).",
      {"query": S("Parte del nombre del archivo"), "open_first": {"type": "boolean", "description": "Abrir el primero encontrado"}},
      ["query"], label=lambda a: f"Buscando archivos «{a.get('query', '')}»")
def find_files(query: str, open_first: bool = False) -> str:
    q = query.lower()
    res = []
    t0 = time.time()
    for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "OneDrive"):
        root = Path.home() / sub
        if not root.exists():
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ("node_modules", "appdata")]
            for f in files:
                if q in f.lower():
                    res.append(Path(dirpath) / f)
            if len(res) > 30 or time.time() - t0 > 6:
                break
    if not res:
        return f"No he encontrado archivos con '{query}'."
    res.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    if open_first:
        os.startfile(res[0])
    return "Encontrados (más recientes primero):\n" + "\n".join(str(p) for p in res[:10])


@tool("open_file", "Abre un archivo con su programa predeterminado.", {"path": S("Ruta completa")}, ["path"],
      label="Abriendo archivo")
def open_file(path: str) -> str:
    if not Path(path).exists():
        return "Ese archivo no existe."
    os.startfile(path)
    return "Abierto."


# ------------------------------------------------------------------ teclado
VK = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD, "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1,
      "stop": 0xB2}


def _key(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)


def _set_clipboard(text: str) -> None:
    import win32clipboard
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            return
        except Exception:
            time.sleep(0.1)


@tool("type_text", "Escribe texto en la ventana que esté activa (donde esté el cursor).",
      {"text": S("Texto a escribir"), "enter": {"type": "boolean", "description": "Pulsar Intro al final"}},
      ["text"], label="Escribiendo")
def type_text(text: str, enter: bool = False) -> str:
    import pyautogui
    _set_clipboard(text)
    pyautogui.hotkey("ctrl", "v")
    if enter:
        time.sleep(0.1)
        pyautogui.press("enter")
    return "Escrito."


@tool("press_keys", "Pulsa una tecla o combinación de teclas en la ventana activa. Ej: 'ctrl+c', 'alt+tab', 'win+d', 'enter', 'f11'.",
      {"keys": S("Teclas separadas por +")}, ["keys"], label=lambda a: f"Pulsando {a.get('keys', '')}")
def press_keys(keys: str) -> str:
    import pyautogui
    parts = [k.strip().lower().replace("windows", "win").replace("control", "ctrl").replace("intro", "enter")
             .replace("escape", "esc").replace("suprimir", "delete").replace("espacio", "space") for k in keys.split("+")]
    pyautogui.hotkey(*parts)
    return "Pulsado."


# ------------------------------------------------------------------ ratón (visible)
def move_mouse_visibly(x: int, y: int, duration: float = 0.7) -> None:
    import pyautogui
    pyautogui.FAILSAFE = True
    bus.mouse.emit(True)
    bus.mouse_target.emit(int(x), int(y))
    try:
        pyautogui.moveTo(x, y, duration=duration, tween=pyautogui.easeInOutQuad)
    finally:
        threading.Timer(1.2, lambda: bus.mouse.emit(False)).start()


@tool("mouse", "Mueve el ratón y/o hace clic en coordenadas de pantalla (píxeles). Úsalo solo si sabes las coordenadas.",
      {"action": S("Acción", ["move", "click", "double_click", "right_click", "scroll_up", "scroll_down"]),
       "x": N("Coordenada X"), "y": N("Coordenada Y")}, ["action"], label="Controlando el ratón")
def mouse(action: str, x: float | None = None, y: float | None = None) -> str:
    import pyautogui
    if x is not None and y is not None:
        move_mouse_visibly(int(x), int(y))
    if action == "click":
        pyautogui.click()
    elif action == "double_click":
        pyautogui.doubleClick()
    elif action == "right_click":
        pyautogui.rightClick()
    elif action == "scroll_up":
        pyautogui.scroll(500)
    elif action == "scroll_down":
        pyautogui.scroll(-500)
    return "Hecho."


# ------------------------------------------------------------------ sistema
def _volume_iface():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


@tool("system", "Controla el PC: volumen, música (reproducir/pausar/siguiente), brillo, bloquear, suspender, apagar, reiniciar, captura de pantalla, modo oscuro/claro, minimizar todo.",
      {"action": S("Acción", ["volume_set", "volume_up", "volume_down", "mute", "unmute", "media_play_pause", "media_next",
                              "media_previous", "brightness_set", "lock", "sleep", "shutdown", "restart", "cancel_shutdown",
                              "screenshot", "dark_mode", "light_mode", "show_desktop", "empty_recycle_bin"]),
       "value": N("Valor 0-100 para volumen o brillo")}, ["action"],
      label=lambda a: {"screenshot": "Capturando pantalla", "lock": "Bloqueando el equipo"}.get(a.get("action"), "Ajustando el sistema"))
def system(action: str, value: float | None = None) -> str:
    if action.startswith("volume") or action in ("mute", "unmute"):
        try:
            v = _volume_iface()
            cur = v.GetMasterVolumeLevelScalar()
            if action == "volume_set" and value is not None:
                v.SetMasterVolumeLevelScalar(max(0.0, min(1.0, value / 100)), None)
            elif action == "volume_up":
                v.SetMasterVolumeLevelScalar(min(1.0, cur + (value or 10) / 100), None)
            elif action == "volume_down":
                v.SetMasterVolumeLevelScalar(max(0.0, cur - (value or 10) / 100), None)
            elif action == "mute":
                v.SetMute(1, None)
            elif action == "unmute":
                v.SetMute(0, None)
            return f"Volumen al {round(v.GetMasterVolumeLevelScalar() * 100)} %."
        except Exception:
            _key(VK.get(action, VK["volume_up"]))
            return "Hecho."
    if action.startswith("media_"):
        _key(VK[action.removeprefix("media_")])
        return "Hecho."
    if action == "brightness_set":
        lv = int(value if value is not None else 70)
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{lv})"],
                           capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        return f"Brillo al {lv} %." if r.returncode == 0 else "Esta pantalla no permite cambiar el brillo desde Windows."
    if action == "lock":
        user32.LockWorkStation()
        return "Equipo bloqueado."
    if action == "sleep":
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        return "Suspendiendo."
    if action == "shutdown":
        subprocess.run(["shutdown", "/s", "/t", "60"], creationflags=CREATE_NO_WINDOW)
        return "El equipo se apagará en 60 segundos. Se puede cancelar."
    if action == "restart":
        subprocess.run(["shutdown", "/r", "/t", "60"], creationflags=CREATE_NO_WINDOW)
        return "El equipo se reiniciará en 60 segundos. Se puede cancelar."
    if action == "cancel_shutdown":
        subprocess.run(["shutdown", "/a"], creationflags=CREATE_NO_WINDOW)
        return "Apagado cancelado."
    if action == "screenshot":
        from PIL import ImageGrab
        p = SHOTS_OUT / time.strftime("captura_%Y-%m-%d_%H-%M-%S.png")
        ImageGrab.grab(all_screens=True).save(p)
        return f"Captura guardada en {p}"
    if action in ("dark_mode", "light_mode"):
        import winreg
        val = 0 if action == "dark_mode" else 1
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                           0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, "AppsUseLightTheme", 0, winreg.REG_DWORD, val)
        winreg.SetValueEx(k, "SystemUsesLightTheme", 0, winreg.REG_DWORD, val)
        winreg.CloseKey(k)
        return "Modo oscuro activado." if val == 0 else "Modo claro activado."
    if action == "show_desktop":
        import pyautogui
        pyautogui.hotkey("win", "d")
        return "Hecho."
    if action == "empty_recycle_bin":
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
        return "Papelera vaciada."
    return "Acción desconocida."


@tool("system_status", "Da información del equipo: batería, uso de CPU y memoria, programas que más consumen.",
      {}, [], label="Revisando el sistema")
def system_status() -> str:
    b = psutil.sensors_battery()
    parts = []
    if b:
        parts.append(f"Batería {b.percent:.0f} % {'cargando' if b.power_plugged else 'sin cargador'}")
    parts.append(f"CPU {psutil.cpu_percent(interval=0.5):.0f} %")
    vm = psutil.virtual_memory()
    parts.append(f"RAM {vm.percent:.0f} % de {vm.total / 2**30:.0f} GB")
    procs = sorted(psutil.process_iter(["name", "memory_info"]), key=lambda p: p.info["memory_info"].rss if p.info["memory_info"] else 0, reverse=True)[:5]
    parts.append("Más memoria: " + ", ".join(f"{p.info['name']} ({p.info['memory_info'].rss / 2**20:.0f} MB)" for p in procs))
    du = psutil.disk_usage(str(Path.home().anchor))
    parts.append(f"Disco {du.percent:.0f} % usado, {du.free / 2**30:.0f} GB libres")
    return ". ".join(parts)


# ------------------------------------------------------------------ visión
@tool("look_at_screen", "Mira la pantalla actual (captura) y responde a una pregunta sobre lo que se ve.",
      {"question": S("Qué quieres saber de la pantalla")}, ["question"], label="Analizando la pantalla")
def look_at_screen(question: str) -> str:
    import requests
    from PIL import ImageGrab
    time.sleep(0.4)
    img = ImageGrab.grab()
    img.thumbnail((1344, 1344))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    b64 = base64.b64encode(buf.getvalue()).decode()
    r = requests.post(settings["ollama_url"] + "/api/chat", json={
        "model": settings["vision_model"], "stream": False, "think": False, "keep_alive": "2m",
        "messages": [{"role": "user", "content": f"Responde en español, conciso. {question}", "images": [b64]}]},
        timeout=180)
    r.raise_for_status()
    return r.json()["message"]["content"]
