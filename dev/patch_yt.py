p = "jarvis/tools/browser.py"
s = open(p, encoding="utf-8").read()
a = s.index('@tool("youtube"')
b = s.index('    if action == "play_pause":')
new = r'''YT_TITLE_LINKS = ("a#video-title:visible, a#video-title-link:visible, "
                  "a.yt-lockup-metadata-view-model__title:visible, a.yt-lockup-metadata-view-model-wiz__title:visible")
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
       "query": S("Búsqueda, nombre del canal, sección del canal o parte del título"),
       "value": N("Segundos para seek (negativo = atrás), número para play_result, o velocidad (1.5)")},
      ["action"], label=lambda a: {"search": f"Buscando en YouTube «{a.get('query', '')}»", "play": f"Poniendo «{a.get('query', '')}»",
                                   "browse_results": "Recorriendo vídeos", "open_channel": f"Abriendo el canal {a.get('query') or ''}",
                                   "channel_tab": f"Sección {a.get('query', '')}", "play_title": f"Poniendo «{a.get('query', '')}»",
                                   "select_highlighted": "Abriendo el vídeo elegido", "quality_max": "Calidad máxima",
                                   "fullscreen": "Pantalla completa", "seek": "Moviendo el vídeo"}.get(a.get("action"), "Controlando YouTube"))
def youtube(action: str, query: str | None = None, value: float | None = None) -> str:
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
        return b.call(_open_channel, query, timeout=60)
    if action == "channel_tab":
        return b.call(_channel_tab, query or "videos", timeout=60)
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
'''
s = s[:a] + new + s[b:]
# el "ese" del recorrido debe encontrar el enlace del título en cualquier tipo de tarjeta
s = s.replace('link = el.locator("a#video-title, a#video-title-link, h3 a, a").first',
              'link = el.locator("a#video-title, a#video-title-link, a.yt-lockup-metadata-view-model__title, h3 a, a[href*=watch]").first')
open(p, "w", encoding="utf-8").write(s)
print("ok")
