"""Navegador controlado por Jarvis (Playwright + Chrome/Edge instalado), búsqueda web y YouTube."""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
import urllib.parse
from pathlib import Path

from ..bus import bus
from ..config import BROWSER_PROFILE, settings
from ..memory import memory
from . import N, S, tool

log = logging.getLogger("browser")

CHROME = [Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / r"Google\Chrome\Application\chrome.exe",
          Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / r"Google\Chrome\Application\chrome.exe",
          Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe"]

SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
    "twitter": "https://x.com", "x": "https://x.com", "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com", "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.es",
    "wikipedia": "https://es.wikipedia.org", "twitch": "https://www.twitch.tv", "reddit": "https://www.reddit.com",
    "tiktok": "https://www.tiktok.com", "chatgpt": "https://chatgpt.com", "whatsapp web": "https://web.whatsapp.com",
    "marca": "https://www.marca.com", "as": "https://as.com", "el país": "https://elpais.com",
    "google maps": "https://maps.google.com", "maps": "https://maps.google.com", "drive": "https://drive.google.com",
    "spotify web": "https://open.spotify.com", "disney": "https://www.disneyplus.com", "prime video": "https://www.primevideo.com",
}

HIGHLIGHT_CSS = """
.jarvis-hl{outline:3px solid #39d0ff !important;outline-offset:4px;border-radius:14px;
 box-shadow:0 0 0 6px rgba(57,208,255,.18),0 0 38px rgba(57,208,255,.55) !important;transition:all .25s}
.jarvis-tag{position:absolute;z-index:99999;background:#061826;color:#7fe3ff;font:600 12px Segoe UI;
 padding:3px 8px;border:1px solid #39d0ff;border-radius:8px;letter-spacing:1px}
"""

CONSENT_BUTTONS = re.compile(r"^(Aceptar todo|Rechazar todo|Accept all|Reject all|Acepto|Aceptar|I agree)$", re.I)


class Browser:
    def __init__(self) -> None:
        self.jobs: "queue.Queue" = queue.Queue()
        self.ctx = None
        self.page = None
        self.browse = None          # dict con estado del "scroll lento" que resalta elementos
        self.scroll = None          # scroll lento genérico
        threading.Thread(target=self._loop, name="browser", daemon=True).start()

    # ---- hilo propio (Playwright sync no es multihilo) ----
    def call(self, fn, *args, timeout: float = 90):
        res: "queue.Queue" = queue.Queue()
        self.jobs.put((fn, args, res))
        ok, val = res.get(timeout=timeout)
        if not ok:
            raise val
        return val

    def _loop(self) -> None:
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        while True:
            try:
                fn, args, res = self.jobs.get(timeout=0.15)
            except queue.Empty:
                self._tick()
                continue
            try:
                res.put((True, fn(*args)))
            except Exception as e:  # noqa: BLE001
                log.exception("browser job")
                res.put((False, e))

    def _ensure(self):
        if self.ctx is not None:
            try:
                pages = [p for p in self.ctx.pages if not p.is_closed()]
                if pages:
                    if self.page is None or self.page.is_closed():
                        self.page = pages[-1]
                    return self.page
                self.page = self.ctx.new_page()
                return self.page
            except Exception:
                self.ctx = None
        kind = settings["browser"]
        channel = "chrome" if (kind == "chrome" or (kind == "auto" and any(p.exists() for p in CHROME))) else "msedge"
        BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
        self.ctx = self.pw.chromium.launch_persistent_context(
            str(BROWSER_PROFILE), channel=channel, headless=False, no_viewport=True,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled", "--no-first-run",
                  "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required"],
            ignore_default_args=["--enable-automation"], chromium_sandbox=True, locale="es-ES")
        self.ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        self.ctx.on("page", lambda p: setattr(self, "page", p))
        self.ctx.on("close", lambda *_: setattr(self, "ctx", None))
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        log.info("navegador lanzado (%s)", channel)
        return self.page

    def _front(self, page) -> None:
        try:
            page.bring_to_front()
        except Exception:
            pass

    def _consent(self, page, wait: float = 0.0) -> bool:
        """Cierra avisos de cookies de Google/YouTube (prefiere «Rechazar todo»). Devuelve True si cerró alguno."""
        name = re.compile(r"^\s*(Rechazar todo|Reject all)\s*$", re.I)
        name2 = re.compile(r"^\s*(Aceptar todo|Accept all)\s*$", re.I)
        end = time.time() + wait
        while True:
            try:
                for rx in (name, name2):
                    btn = page.locator("button, [role=button]").filter(has_text=rx)
                    for i in range(min(btn.count(), 4)):
                        b = btn.nth(i)
                        if b.is_visible():
                            b.click(timeout=3000)
                            time.sleep(0.8)
                            return True
            except Exception:
                pass
            if time.time() >= end:
                return False
            time.sleep(0.3)

    def _goto(self, url: str):
        page = self._ensure()
        self._front(page)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        google = any(d in page.url for d in ("youtube.com", "google.", "consent."))
        self._consent(page, wait=2.5 if google and not self.__dict__.get("_consent_done") else 0)
        if google:
            self._consent_done = True
        return page

    # ---- ratón visible sobre elementos de la página ----
    def _screen_point(self, page, locator) -> tuple[int, int] | None:
        try:
            box = locator.bounding_box(timeout=3000)
            if not box:
                return None
            m = page.evaluate("""() => ({sx: window.screenX, sy: window.screenY, ow: window.outerWidth,
                oh: window.outerHeight, iw: window.innerWidth, ih: window.innerHeight, dpr: window.devicePixelRatio})""")
            border = max(0, (m["ow"] - m["iw"]) / 2)
            top = m["oh"] - m["ih"] - border
            x = (m["sx"] + border + box["x"] + box["width"] / 2) * m["dpr"]
            y = (m["sy"] + top + box["y"] + min(box["height"] / 2, 30)) * m["dpr"]
            return int(x), int(y)
        except Exception:
            return None

    def _visible_click(self, page, locator) -> None:
        from .system import move_mouse_visibly
        locator.scroll_into_view_if_needed(timeout=4000)
        pt = self._screen_point(page, locator)
        if pt:
            move_mouse_visibly(*pt, duration=0.6)
        try:
            locator.click(timeout=4000)
        except Exception:
            if self._consent(page, wait=1.0):  # algo tapaba el clic (aviso de cookies)
                locator.click(timeout=6000)
            else:
                locator.click(timeout=4000, force=True)

    def _smooth_scroll(self, page, total: int = 1400, steps: int = 7) -> None:
        for _ in range(steps):
            page.evaluate(f"window.scrollBy({{top:{total // steps},behavior:'smooth'}})")
            time.sleep(0.28)

    # ---- tick periódico (scroll lento con resaltado) ----
    def _tick(self) -> None:
        now = time.time()
        if self.browse and now >= self.browse["next"]:
            try:
                self.browse["next"] = now + self.browse["every"]
                page = self.page
                title = page.evaluate("""([sel, i]) => {
                    const els=[...document.querySelectorAll(sel)].filter(e=>e.offsetHeight>0);
                    document.querySelectorAll('.jarvis-hl').forEach(e=>e.classList.remove('jarvis-hl'));
                    if(i>=els.length) return null;
                    const el=els[i]; el.classList.add('jarvis-hl');
                    el.scrollIntoView({behavior:'smooth',block:'center'});
                    const t=el.querySelector('#video-title,h3,a'); return t?(t.textContent||'').trim():'';
                }""", [self.browse["sel"], self.browse["i"]])
                if title is None:
                    self.browse["misses"] += 1
                    page.evaluate("window.scrollBy({top:900,behavior:'smooth'})")
                    if self.browse["misses"] > 4:
                        self.browse = None
                        bus.status.emit("No hay más resultados")
                    return
                self.browse["misses"] = 0
                self.browse["title"] = title
                bus.status.emit(f"Resaltado {self.browse['i'] + 1}: {title[:60]}")
                self.browse["i"] += 1
            except Exception:
                log.exception("browse tick")
                self.browse = None
        if self.scroll and now >= self.scroll["next"]:
            try:
                self.scroll["next"] = now + 0.35
                self.page.evaluate(f"window.scrollBy({{top:{self.scroll['dy']},behavior:'smooth'}})")
                if now > self.scroll["until"]:
                    self.scroll = None
            except Exception:
                self.scroll = None

    # ============ operaciones (se ejecutan en el hilo del navegador) ============
    def open_url(self, url: str) -> str:
        return self.call(self._open_url, url)

    def _open_url(self, url: str) -> str:
        page = self._goto(url)
        return f"Abierta {page.url} — título: {page.title()}"

    def _new_tab(self, url: str | None) -> str:
        self._ensure()
        page = self.ctx.new_page()
        self.page = page
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            self._consent(page)
        self._front(page)
        return f"Nueva pestaña: {page.url}"

    def _tabs(self) -> list:
        self._ensure()
        return [p for p in self.ctx.pages if not p.is_closed()]

    def _switch(self, delta: int | None = None, index: int | None = None) -> str:
        tabs = self._tabs()
        cur = tabs.index(self.page) if self.page in tabs else 0
        i = (cur + delta) % len(tabs) if delta is not None else max(0, min(len(tabs) - 1, (index or 1) - 1))
        self.page = tabs[i]
        self._front(self.page)
        return f"Pestaña {i + 1} de {len(tabs)}: {self.page.title()}"

    def _list_tabs(self) -> str:
        return "\n".join(f"{i + 1}. {p.title()} — {p.url}" for i, p in enumerate(self._tabs()))

    def _click_text(self, text: str) -> str:
        page = self._ensure()
        cands = [page.get_by_role("link", name=re.compile(re.escape(text), re.I)),
                 page.get_by_role("button", name=re.compile(re.escape(text), re.I)),
                 page.get_by_text(re.compile(re.escape(text), re.I))]
        for loc in cands:
            try:
                n = loc.count()
            except Exception:
                n = 0
            for i in range(min(n, 5)):
                el = loc.nth(i)
                if el.is_visible():
                    self._visible_click(page, el)
                    time.sleep(0.8)
                    return f"Clic en «{text}». Ahora: {page.title()}"
        return f"No veo nada que ponga «{text}» en la página."

    def _type(self, text: str, enter: bool) -> str:
        page = self._ensure()
        box = page.locator("input[type=search]:visible, input[name=q]:visible, input[type=text]:visible, textarea:visible").first
        try:
            if box.count():
                self._visible_click(page, box)
                box.fill(text)
            else:
                page.keyboard.type(text, delay=20)
        except Exception:
            page.keyboard.type(text, delay=20)
        if enter:
            page.keyboard.press("Enter")
            time.sleep(1.2)
        return "Escrito."

    def _read(self, max_chars: int = 6000) -> str:
        import trafilatura
        page = self._ensure()
        txt = trafilatura.extract(page.content(), include_comments=False, include_tables=True) or ""
        if len(txt) < 200:
            txt = page.inner_text("body")
        return f"{page.title()} ({page.url})\n{txt[:max_chars]}"

    def _start_browse(self, sel: str, every: float = 2.6) -> None:
        page = self._ensure()
        page.add_style_tag(content=HIGHLIGHT_CSS)
        self.browse = {"sel": sel, "i": 0, "every": every, "next": time.time() + 0.5, "title": "", "misses": 0}

    # ---- música de fondo (presentación): pestaña aparte con la ventana minimizada ----
    def _minimize(self, page) -> None:
        try:
            cdp = self.ctx.new_cdp_session(page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
        except Exception:
            log.exception("minimizar")

    def _music_start(self, url: str, search: str) -> bool:
        prev = self._ensure()
        page = self.ctx.new_page()
        self.music = page
        self.page = prev  # las órdenes normales siguen en la pestaña de siempre
        self._minimize(page)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        self._consent(page, wait=2.5)
        try:
            page.wait_for_selector("video", timeout=10000)
        except Exception:
            page.goto("https://www.youtube.com/results?search_query=" + urllib.parse.quote(search),
                      wait_until="domcontentloaded", timeout=25000)
            page.locator("a#video-title:visible, yt-lockup-view-model h3 a:visible").first.click(timeout=8000)
            page.wait_for_selector("video", timeout=10000)
        page.evaluate("() => { const v = document.querySelector('video'); if (v) { v.muted = false; v.volume = 1; v.play(); } }")
        self._minimize(page)
        return True

    def _music_state(self) -> dict:
        page = getattr(self, "music", None)
        if page is None or page.is_closed():
            return {}
        st = page.evaluate("""() => { const v = document.querySelector('video'); const ad = !!document.querySelector('.ad-showing');
            const mp = document.getElementById('movie_player');
            const vol = (mp && mp.getVolume) ? mp.getVolume() / 100 : (v ? v.volume : 1);
            return v ? {t: v.currentTime, playing: !v.paused, vol: vol, ad: ad} : {}; }""")
        if st.get("ad"):  # anuncio: silencio y saltar en cuanto se pueda
            page.evaluate("""() => { const v = document.querySelector('video'); if (v) v.muted = true;
                const b = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern'); if (b) b.click(); }""")
        else:
            page.evaluate("() => { const v = document.querySelector('video'); if (v && v.muted) v.muted = false; }")
        return st

    def _music_volume(self, v: float) -> None:
        page = getattr(self, "music", None)
        if page is not None and not page.is_closed():
            x = max(0.0, min(1.0, v))
            page.evaluate(f"""() => {{ const mp = document.getElementById('movie_player');
                if (mp && mp.setVolume) {{ mp.unMute && mp.unMute(); mp.setVolume({x * 100:.1f}); }}
                const v = document.querySelector('video'); if (v) v.volume = {x:.3f}; }}""")

    def _music_stop(self) -> None:
        page = getattr(self, "music", None)
        self.music = None
        if page is not None and not page.is_closed():
            page.close()

    def _select_highlighted(self) -> str:
        if not self.browse:
            return "No estoy recorriendo ninguna lista ahora mismo."
        page = self.page
        self.browse = None
        el = page.locator(".jarvis-hl").first
        if not el.count():
            return "No hay ningún elemento resaltado."
        link = el.locator("a#video-title, a#video-title-link, a.ytLockupMetadataViewModelTitle, h3 a, a[href*=watch]").first
        self._visible_click(page, link)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(1.5)
        return f"Abierto: {page.title()}"


browser = Browser()


# ============================ herramientas ============================
def _norm_url(target: str) -> str:
    t = target.strip()
    low = t.lower()
    if low in SITES:
        return SITES[low]
    if re.match(r"^https?://", t):
        return t
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", low):
        return "https://" + t
    return "https://duckduckgo.com/?q=" + urllib.parse.quote("!ducky " + t)


@tool("open_website", "Abre una página web en el navegador de Jarvis (por URL o por nombre: youtube, gmail, netflix, marca…).",
      {"target": S("URL o nombre de la web"), "new_tab": {"type": "boolean", "description": "Abrir en pestaña nueva"}},
      ["target"], label=lambda a: f"Abriendo {a.get('target', '')}")
def open_website(target: str, new_tab: bool = False) -> str:
    url = _norm_url(target)
    memory.event("site_open", target.lower())
    if new_tab:
        return browser.call(browser._new_tab, url)
    return browser.open_url(url)


def _ddg(query: str, news: bool, n: int = 8) -> list[dict]:
    try:
        from ddgs import DDGS
        with DDGS() as d:
            if news:
                return [{"title": r.get("title", ""), "url": r.get("url", ""), "body": r.get("body", ""),
                         "date": r.get("date", ""), "source": r.get("source", "")}
                        for r in d.news(query, region="es-es", timelimit="w", max_results=n)]
            return [{"title": r.get("title", ""), "url": r.get("href", ""), "body": r.get("body", "")}
                    for r in d.text(query, region="es-es", max_results=n)]
    except Exception:
        log.exception("ddgs")
        return []


NEWS_RE = re.compile(r"noticia|qué está pasando|que esta pasando|últim|hoy|actualidad|ayer|esta semana", re.I)


@tool("web_search", "Busca en internet (se ve en el navegador) y devuelve los resultados (título, enlace, resumen). Para datos actuales: noticias, tiempo, resultados, precios…",
      {"query": S("Qué buscar"), "news": {"type": "boolean", "description": "Buscar noticias recientes"}},
      ["query"], label=lambda a: f"Buscando «{a.get('query', '')}»")
def web_search(query: str, news: bool | None = None) -> str:
    news = bool(news) if news is not None else bool(NEWS_RE.search(query))
    url = "https://duckduckgo.com/?" + urllib.parse.urlencode({"q": query, "kl": "es-es", "ia": "news" if news else "web",
                                                                 **({"iar": "news"} if news else {})})
    try:
        browser.open_url(url)
    except Exception:
        log.exception("búsqueda visible")
    res = _ddg(query, news) or (_ddg(query, False) if news else [])
    if not res:
        return "La búsqueda no ha devuelto resultados."
    lines = []
    for i, r in enumerate(res, 1):
        extra = f" ({r.get('source', '')} {r.get('date', '')[:10]})" if r.get("date") else ""
        lines.append(f"{i}. {r['title']}{extra}\n   {r['url']}\n   {r['body'][:300]}")
    return "\n".join(lines)


@tool("web_research", "Investiga un tema a fondo: busca en internet, entra en las mejores páginas (se ve en pantalla) y devuelve su contenido con las fuentes. Úsalo antes de hacer un resumen o un informe.",
      {"query": S("Tema a investigar"), "pages": N("Cuántas páginas leer (1-4, por defecto 3)")},
      ["query"], label=lambda a: f"Investigando «{a.get('query', '')}»")
def web_research(query: str, pages: float = 3) -> str:
    news = bool(NEWS_RE.search(query))
    results = _ddg(query, news) or _ddg(query, False)
    try:
        browser.open_url("https://duckduckgo.com/?" + urllib.parse.urlencode({"q": query, "kl": "es-es"}))
    except Exception:
        pass
    if not results:
        return "No he encontrado resultados."
    n = int(max(1, min(4, pages or 3)))
    out = [f"Resultados para «{query}»:"]
    for r in results[:8]:
        out.append(f"- {r['title']} ({r.get('source', '')} {r.get('date', '')[:10]}): {r['body'][:220]}")
    read = 0
    for r in results:
        if read >= n:
            break
        u = r["url"]
        if not u or any(b in u for b in ("youtube.com", ".pdf", "facebook.com", "instagram.com", "x.com", "tiktok.com")):
            continue
        bus.status.emit(f"Leyendo {urllib.parse.urlparse(u).netloc}")
        try:
            txt = browser.call(_visit_and_read, u, timeout=40)
        except Exception:
            continue
        if txt and len(txt) > 300:
            read += 1
            out.append(f"\n### Fuente {read}: {r['title']} — {u}\n{txt[:2200]}")
    return "\n".join(out)


def _visit_and_read(url: str) -> str:
    import trafilatura
    page = browser._goto(url)
    time.sleep(0.8)
    browser._smooth_scroll(page, 1600, 6)
    html = page.content()
    txt = trafilatura.extract(html, include_comments=False) or ""
    return txt


@tool("browser", "Controla el navegador de Jarvis: pestañas, atrás/adelante, recargar, desplazarse (también lento), hacer clic en un texto, escribir, leer la página, zoom.",
      {"action": S("Acción", ["new_tab", "close_tab", "next_tab", "previous_tab", "go_to_tab", "list_tabs", "back", "forward",
                              "reload", "scroll_down", "scroll_up", "scroll_slow", "stop_scroll", "scroll_top", "click_text",
                              "type", "type_and_enter", "press_key", "read_page", "zoom_in", "zoom_out", "fullscreen"]),
       "value": S("Texto, URL, tecla o número de pestaña según la acción")}, ["action"],
      label=lambda a: {"click_text": f"Clic en «{a.get('value', '')}»", "read_page": "Leyendo la página",
                       "scroll_slow": "Desplazando despacio"}.get(a.get("action"), "Navegando"))
def browser_tool(action: str, value: str | None = None) -> str:
    b = browser
    v = value or ""
    if action == "new_tab":
        return b.call(b._new_tab, _norm_url(v) if v else None)
    if action == "close_tab":
        return b.call(lambda: (b._ensure().close(), b._switch(0))[1] if len(b._tabs()) > 1 else "Solo queda una pestaña.")
    if action == "next_tab":
        return b.call(b._switch, 1)
    if action == "previous_tab":
        return b.call(b._switch, -1)
    if action == "go_to_tab":
        return b.call(b._switch, None, int(re.sub(r"\D", "", v) or 1))
    if action == "list_tabs":
        return b.call(b._list_tabs)
    if action in ("back", "forward", "reload"):
        def nav():
            p = b._ensure()
            getattr(p, {"back": "go_back", "forward": "go_forward", "reload": "reload"}[action])(timeout=15000)
            return p.title()
        return b.call(nav)
    if action in ("scroll_down", "scroll_up", "scroll_top"):
        dy = {"scroll_down": 700, "scroll_up": -700}.get(action)
        js = "window.scrollTo({top:0,behavior:'smooth'})" if dy is None else f"window.scrollBy({{top:{dy},behavior:'smooth'}})"
        return b.call(lambda: (b._ensure().evaluate(js), "Hecho.")[1])
    if action == "scroll_slow":
        def start():
            b._ensure()
            b.scroll = {"dy": 90, "next": time.time(), "until": time.time() + 90}
            return "Desplazando despacio. Dime 'para' cuando quieras."
        return b.call(start)
    if action == "stop_scroll":
        b.scroll = None
        b.browse = None
        return "Parado."
    if action == "click_text":
        return b.call(b._click_text, v)
    if action in ("type", "type_and_enter"):
        return b.call(b._type, v, action == "type_and_enter")
    if action == "press_key":
        return b.call(lambda: (b._ensure().keyboard.press(v or "Enter"), "Hecho.")[1])
    if action == "read_page":
        return b.call(b._read)
    if action in ("zoom_in", "zoom_out"):
        key = "Control+Equal" if action == "zoom_in" else "Control+Minus"
        return b.call(lambda: (b._ensure().keyboard.press(key), "Hecho.")[1])
    if action == "fullscreen":
        return b.call(lambda: (b._ensure().keyboard.press("F11"), "Hecho.")[1])
    return "Acción desconocida."


# ------------------------------- YouTube -------------------------------
YT_CARDS = "ytd-video-renderer, ytd-rich-item-renderer"


def _yt_page():
    p = browser._ensure()
    if "youtube.com" not in p.url:
        p = browser._goto("https://www.youtube.com")
        time.sleep(1.5)
    return p


def _yt_js(js: str):
    return browser.call(lambda: _yt_page().evaluate(js))


def _yt_focus_player(p) -> None:
    try:
        p.locator("#movie_player").first.focus(timeout=2000)
    except Exception:
        pass


def _yt_key(key: str) -> str:
    def f():
        p = _yt_page()
        _yt_focus_player(p)
        p.keyboard.press(key)
        return "Hecho."
    return browser.call(f)


YT_TITLE_LINKS = ("a#video-title:visible, a#video-title-link:visible, "
                  "a.ytLockupMetadataViewModelTitle:visible, yt-lockup-view-model h3 a:visible, "
                  "a.yt-lockup-metadata-view-model__title:visible")
YT_CARDS_ALL = ("ytd-video-renderer, ytd-rich-item-renderer, ytd-grid-video-renderer, ytd-compact-video-renderer, "
                "yt-lockup-view-model")
CHANNEL_TABS = {"inicio": "featured", "principal": "featured", "videos": "videos", "vídeos": "videos", "shorts": "shorts",
                "directos": "streams", "en vivo": "streams", "en directo": "streams", "streams": "streams",
                "listas": "playlists", "playlists": "playlists", "listas de reproducción": "playlists",
                "publicaciones": "posts", "comunidad": "posts", "podcasts": "podcasts", "acerca de": "about"}


def _yt_titles(p, n: int = 8) -> list[str]:
    try:
        return [t.strip() for t in p.locator(YT_TITLE_LINKS).all_inner_texts()[:n] if t.strip()]
    except Exception:
        return []


def _channel_base(p) -> str | None:
    m = re.match(r"(https://www\.youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+))", p.url)
    if m:
        return m.group(1)
    try:  # en un vídeo: el enlace del canal del autor
        href = p.locator("ytd-video-owner-renderer a[href]").first.get_attribute("href", timeout=3000)
        if href:
            return "https://www.youtube.com" + href.split("?")[0].rstrip("/").removesuffix("/videos")
    except Exception:
        pass
    return None


def _open_channel(name: str | None) -> str:
    p = _yt_page()
    browser.browse = None
    if not name:
        base = _channel_base(p)
        if not base:
            return "No estoy en ningún vídeo ni canal; dime de qué canal se trata."
        p = browser._goto(base)
        return f"Canal abierto: {p.title()}"
    browser._goto("https://www.youtube.com/results?search_query=" + urllib.parse.quote(name) + "&sp=EgIQAg%253D%253D")
    p = browser.page
    try:
        p.wait_for_selector("ytd-channel-renderer", timeout=10000)
        link = p.locator("ytd-channel-renderer a#main-link, ytd-channel-renderer a.channel-link").first
        browser._visible_click(p, link)
        p.wait_for_url(re.compile(r"/(@|channel/|c/|user/)"), timeout=10000)
    except Exception:
        p = browser._goto("https://www.youtube.com/@" + re.sub(r"\s+", "", name))
    time.sleep(1.2)
    return f"Canal abierto: {p.title()} ({p.url}). Pestañas: inicio, vídeos, shorts, directos, listas."


def _channel_tab(tab: str) -> str:
    p = _yt_page()
    browser.browse = None
    key = (tab or "videos").lower().strip()
    slug = CHANNEL_TABS.get(key) or next((v for k, v in CHANNEL_TABS.items() if k in key), "videos")
    base = _channel_base(p)
    if not base:
        return "No estoy en ningún canal. Primero abre el canal (action=open_channel)."
    p = browser._goto(base + "/" + slug)
    time.sleep(1.5)
    tl = _yt_titles(p, 6)
    return f"Sección «{tab}» abierta en {p.title()}." + ("\nPrimeros: " + " | ".join(tl) if tl else "")


def _play_title(text: str) -> str:
    import difflib
    p = _yt_page()
    browser.browse = None
    links = p.locator(YT_TITLE_LINKS)
    best, score = None, 0.0
    q = text.lower()
    words = re.findall(r"\w{4,}", q)
    for i in range(min(links.count(), 40)):
        t = (links.nth(i).inner_text() or "").strip().lower()
        if not t:
            continue
        sc = difflib.SequenceMatcher(None, q, t).ratio() + (0.5 if q in t else 0) + 0.15 * sum(w in t for w in words)
        if sc > score:
            best, score = i, sc
    if best is None or score < 0.45:
        return f"No veo ningún vídeo que se parezca a «{text}» en esta página."
    browser._visible_click(p, links.nth(best))
    p.wait_for_load_state("domcontentloaded", timeout=15000)
    time.sleep(1.5)
    return f"Reproduciendo: {p.title()}"


@tool("youtube", "Controla YouTube en el navegador de Jarvis. "
      "play = buscar y reproducir el mejor resultado ('pon X'). search = buscar y enseñar resultados (NO los recorre). "
      "browse_results = recorrer despacio los vídeos de la página resaltándolos para que el usuario diga 'ese' (solo si lo pide: 've bajando', 'enséñamelos uno a uno'). "
      "open_channel = abrir un CANAL (query = nombre; sin query, el canal del vídeo actual). "
      "channel_tab = abrir una sección del canal: inicio, vídeos, shorts, directos, listas (query = sección). "
      "play_title = reproducir el vídeo de esta página cuyo título se parezca a query. play_result = el n-ésimo. "
      "Además: pausa, pantalla completa, avanzar/retroceder segundos, calidad máxima, silenciar, velocidad, subtítulos, siguiente, saltar anuncio.",
      {"action": S("Acción", ["play", "search", "open_channel", "channel_tab", "play_title", "play_result", "browse_results",
                              "select_highlighted", "play_pause", "fullscreen", "exit_fullscreen", "seek", "quality_max",
                              "mute", "unmute", "speed", "captions", "next_video", "skip_ad", "what_is_playing"]),
       "query": S("Búsqueda, NOMBRE del canal (open_channel) o parte del título (play_title)"),
       "section": S("Sección del canal: inicio, vídeos, shorts, directos, listas (para open_channel o channel_tab)"),
       "value": N("Segundos para seek (negativo = atrás), número para play_result, o velocidad (1.5)")},
      ["action"], label=lambda a: {"search": f"Buscando en YouTube «{a.get('query', '')}»", "play": f"Poniendo «{a.get('query', '')}»",
                                   "browse_results": "Recorriendo vídeos", "open_channel": f"Abriendo el canal {a.get('query') or ''}",
                                   "channel_tab": f"Sección {a.get('query', '')}", "play_title": f"Poniendo «{a.get('query', '')}»",
                                   "select_highlighted": "Abriendo el vídeo elegido", "quality_max": "Calidad máxima",
                                   "fullscreen": "Pantalla completa", "seek": "Moviendo el vídeo"}.get(a.get("action"), "Controlando YouTube"))
def youtube(action: str, query: str | None = None, value: float | None = None, section: str | None = None) -> str:
    b = browser
    if action in ("search", "play"):
        q = query or ""
        memory.event("site_open", "youtube")
        r = b.open_url("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))

        def titles():
            p = b.page
            p.wait_for_selector("ytd-video-renderer, yt-lockup-view-model", timeout=12000)
            return "\n".join(f"{i + 1}. {t}" for i, t in enumerate(_yt_titles(p, 6)))
        try:
            lst = b.call(titles)
        except Exception:
            return r
        if action == "play":
            return youtube("play_result", value=1) + "\nOtros resultados:\n" + lst
        return ("Resultados en pantalla (no los recorras salvo que lo pida; puede decir el título, un número, "
                "o pedir el canal):\n" + lst)
    if action == "open_channel":
        if query and query.lower().strip() in CHANNEL_TABS and not section:
            return b.call(_channel_tab, query, timeout=60)  # «abre la sección vídeos» mal etiquetado
        r = b.call(_open_channel, query, timeout=60)
        if section:
            r += "\n" + b.call(_channel_tab, section, timeout=60)
        return r
    if action == "channel_tab":
        return b.call(_channel_tab, section or query or "videos", timeout=60)
    if action == "play_title":
        return b.call(_play_title, query or "", timeout=60)
    if action == "browse_results":
        def start():
            _yt_page()
            b._start_browse(YT_CARDS_ALL, every=2.8)
            return "Recorriendo los vídeos uno a uno y resaltándolos. Cuando el usuario diga 'ese', usa select_highlighted."
        return b.call(start)
    if action == "select_highlighted":
        return b.call(b._select_highlighted)
    if action == "play_result":
        n = int(value or 1)

        def play():
            p = _yt_page()
            b.browse = None
            b._visible_click(p, p.locator(YT_TITLE_LINKS).nth(n - 1))
            p.wait_for_load_state("domcontentloaded", timeout=15000)
            time.sleep(1.5)
            return f"Reproduciendo: {p.title()}"
        return b.call(play)
    if action == "play_pause":
        return _yt_key("k")
    if action == "fullscreen":
        return _yt_key("f")
    if action == "exit_fullscreen":
        return _yt_key("Escape")
    if action == "mute" or action == "unmute":
        return _yt_key("m")
    if action == "captions":
        return _yt_key("c")
    if action == "next_video":
        return _yt_key("Shift+N")
    if action == "seek":
        s = float(value or -5)
        t = _yt_js(f"(()=>{{const v=document.querySelector('video'); if(!v) return null; v.currentTime=Math.max(0,v.currentTime+({s})); return Math.round(v.currentTime)}})()")
        return f"Vídeo en el segundo {t}." if t is not None else "No hay ningún vídeo reproduciéndose."
    if action == "speed":
        sp = float(value or 1.0)
        _yt_js(f"(()=>{{const v=document.querySelector('video'); if(v) v.playbackRate={sp};}})()")
        return f"Velocidad x{sp}."
    if action == "skip_ad":
        def skip():
            p = _yt_page()
            btn = p.locator(".ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern").first
            if btn.count() and btn.is_visible():
                b._visible_click(p, btn)
                return "Anuncio saltado."
            return "No hay anuncio que se pueda saltar ahora."
        return b.call(skip)
    if action == "quality_max":
        def q():
            p = _yt_page()
            res = p.evaluate("""() => { const mp=document.getElementById('movie_player'); if(!mp||!mp.getAvailableQualityLevels) return null;
                const lv=mp.getAvailableQualityLevels(); if(!lv.length) return null;
                try{ mp.setPlaybackQualityRange(lv[0], lv[0]); }catch(e){}
                try{ mp.setPlaybackQuality(lv[0]); }catch(e){}
                return lv[0]; }""")
            # vía menú como refuerzo (lo que haría una persona)
            try:
                _yt_focus_player(p)
                b._visible_click(p, p.locator(".ytp-settings-button").first)
                time.sleep(0.5)
                p.locator(".ytp-menuitem").filter(has_text=re.compile("Calidad|Quality")).first.click(timeout=3000)
                time.sleep(0.5)
                items = p.locator(".ytp-quality-menu .ytp-menuitem")
                if items.count():
                    items.first.click(timeout=3000)
            except Exception:
                try:
                    p.keyboard.press("Escape")
                except Exception:
                    pass
            return f"Calidad máxima ({res})." if res else "He puesto la calidad más alta disponible."
        return b.call(q)
    if action == "what_is_playing":
        return b.call(lambda: _yt_page().title())
    return "Acción desconocida."


def browsing_active() -> bool:
    return browser.browse is not None or browser.scroll is not None
