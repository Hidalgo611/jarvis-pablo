p = "jarvis/tools/vision.py"
s = open(p, encoding="utf-8").read()

a = s.index("def capture(")
b = s.index('@tool("screen_click"')
new_mid = r'''def _grab():
    from PIL import ImageGrab
    bus.capture.emit(True)
    time.sleep(0.12)
    try:
        return ImageGrab.grab()
    finally:
        bus.capture.emit(False)


def _to_b64(img, mark_cursor: bool = False) -> str:
    from PIL import ImageDraw
    img = img.copy()
    if mark_cursor:
        try:
            import pyautogui
            x, y = pyautogui.position()
            d = ImageDraw.Draw(img)
            r = max(10, img.size[0] // 120)
            d.ellipse((x - r, y - r, x + r, y + r), outline=(255, 40, 40), width=4)
            d.text((x + r + 4, y - r), "RATÓN", fill=(255, 40, 40))
        except Exception:
            pass
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def capture(mark_cursor: bool = True) -> tuple[str, tuple[int, int], tuple[int, int]]:
    """Captura la pantalla principal sin la interfaz de Jarvis. Devuelve (jpeg_b64, tamaño_real, tamaño_enviado)."""
    img = _grab()
    return _to_b64(img, mark_cursor), img.size, img.size


def _ask(prompt: str, b64: str, max_tokens: int = 200) -> str:
    r = requests.post(settings["ollama_url"] + "/api/chat", timeout=120, json={
        "model": vision_model(), "stream": False, "think": False, "keep_alive": -1,
        "options": {"num_ctx": int(settings["num_ctx"]), "temperature": 0, "num_predict": max_tokens},
        "messages": [{"role": "user", "content": prompt, "images": [b64]}]})
    r.raise_for_status()
    return r.json()["message"]["content"]


def _parse_point(out: str) -> tuple[float, float] | None:
    m = re.search(r'"point_2d"\s*:\s*\[\s*([\d.]+)\s*,\s*([\d.]+)', out)
    if m:
        return float(m.group(1)), float(m.group(2))
    m2 = re.search(r'"bbox_2d"\s*:\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)', out)
    if m2:
        x1, y1, x2, y2 = (float(v) for v in m2.groups())
        return (x1 + x2) / 2, (y1 + y2) / 2
    return None


def _verify(img, pos: tuple[int, int], target: str) -> bool:
    """Recorta alrededor del punto y pregunta si de verdad es el elemento (evita clics inventados)."""
    from PIL import ImageDraw
    W, H = img.size
    cw, ch = max(220, W // 7), max(150, H // 7)
    x0, y0 = max(0, pos[0] - cw // 2), max(0, pos[1] - ch // 2)
    crop = img.crop((x0, y0, min(W, x0 + cw), min(H, y0 + ch))).convert("RGB")
    d = ImageDraw.Draw(crop)
    cx, cy = pos[0] - x0, pos[1] - y0
    d.line((cx - 14, cy, cx + 14, cy), fill=(255, 0, 0), width=3)
    d.line((cx, cy - 14, cx, cy + 14), fill=(255, 0, 0), width=3)
    crop = crop.resize((crop.size[0] * 2, crop.size[1] * 2))
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=88)
    out = _ask(f'Esta imagen es un recorte de la pantalla. ¿La cruz roja está encima de «{target}»? '
               f'Responde solo "sí" o "no".', base64.b64encode(buf.getvalue()).decode(), 5).strip().lower()
    return out.startswith(("sí", "si", "yes"))


def locate(target: str, verify: bool = True) -> tuple[int, int] | None:
    """Devuelve la posición física (px) del elemento descrito, o None si no está (verificado)."""
    img = _grab()
    b64 = _to_b64(img, mark_cursor=True)
    out = _ask(f'Localiza en la captura de pantalla: "{target}". (El círculo rojo «RATÓN» es el puntero; '
               f'si el usuario dice «ahí» puede referirse a él.) Responde SOLO con JSON: '
               f'{{"point_2d": [x, y], "label": "..."}} con coordenadas normalizadas de 0 a 1000. '
               f'Si NO aparece en la pantalla, responde {{"point_2d": null}}.', b64, 120)
    pt = _parse_point(out)
    if not pt:
        log.info("no localizado %r: %s", target, out[:120])
        return None
    pos = int(pt[0] / 1000 * img.size[0]), int(pt[1] / 1000 * img.size[1])
    if verify and not _verify(img, pos, target):
        log.info("localización descartada en la verificación: %r %s", target, pos)
        return None
    return pos


def focus(name: str) -> str | None:
    """Trae al frente la ventana cuyo título o programa se parezca a `name`."""
    import difflib
    import ctypes
    import psutil
    import win32con
    import win32gui
    import win32process
    n = name.lower().strip()
    best, score = None, 0.0
    def cb(h, _):
        nonlocal best, score
        if not win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h):
            return
        title = win32gui.GetWindowText(h)
        if not title:
            return
        try:
            proc = psutil.Process(win32process.GetWindowThreadProcessId(h)[1]).name().lower()
        except Exception:
            proc = ""
        t = title.lower()
        sc = max(difflib.SequenceMatcher(None, n, t).ratio(), difflib.SequenceMatcher(None, n, proc.removesuffix(".exe")).ratio())
        if n in t or n in proc:
            sc += 1
        if sc > score:
            best, score = (h, title), sc
    win32gui.EnumWindows(cb, None)
    if not best or score < 0.6:
        return None
    h, title = best
    if win32gui.IsIconic(h):
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt: permite cambiar el primer plano
    try:
        win32gui.SetForegroundWindow(h)
    finally:
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.6)
    return title


@tool("focus_window", "Trae al frente una ventana abierta (por su título o programa: 'calculadora', 'Spotify', 'Word').",
      {"name": S("Programa o título de la ventana")}, ["name"], label=lambda a: f"Trayendo {a.get('name', '')} al frente")
def focus_window(name: str) -> str:
    t = focus(name)
    return f"Ventana al frente: {t}" if t else f"No hay ninguna ventana abierta que se parezca a «{name}»."


'''
s = s[:a] + new_mid + s[b:]

s = s.replace('''       "type_text": S("Texto a escribir después del clic (opcional)")},''',
              '''       "type_text": S("Texto a escribir después del clic (opcional)"),
       "window": S("Programa en el que está (opcional): se trae al frente antes, p.ej. 'calculadora'")},''')
s = s.replace('''def screen_click(target: str, action: str = "click", type_text: str | None = None) -> str:
    import pyautogui
    from .system import move_mouse_visibly, type_text as _type
    pos = locate(target)
    if not pos:
        return f"No veo «{target}» en la pantalla."''', '''def screen_click(target: str, action: str = "click", type_text: str | None = None, window: str | None = None) -> str:
    import pyautogui
    from .system import move_mouse_visibly, type_text as _type
    if window and not focus(window):
        return f"No encuentro abierta ninguna ventana de «{window}». Ábrela primero."
    pos = locate(target)
    if not pos:
        return (f"No veo «{target}» en la pantalla (comprobado). No lo inventes: dile al usuario que no lo ves "
                f"o prueba a traer la ventana al frente con focus_window.")'''
)
open(p, "w", encoding="utf-8").write(s)

p = "jarvis/brain.py"
s = open(p, encoding="utf-8").read()
old = "Para pulsar cualquier cosa de cualquier programa usa `screen_click` describiendo el elemento tal como se ve."
assert old in s
s = s.replace(old, "Para pulsar cualquier cosa de cualquier programa usa `screen_click` describiendo el elemento tal como se ve (con window=programa si sabes en cuál está). No pulses en la barra de tareas para «abrir» algo que ya está abierto: usa `focus_window`. Nunca afirmes un resultado que no hayas visto.")
open(p, "w", encoding="utf-8").write(s)
print("ok")
