def patch(p, pairs):
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert a in s, (p, a[:70])
        s = s.replace(a, b, 1)
    open(p, "w", encoding="utf-8").write(s)


patch("jarvis/tools/__init__.py", [
    ('''def schemas() -> list[dict]:
    return [t.schema() for t in REGISTRY.values()]''',
     r'''CORE = ["open_app", "close_app", "open_website", "web_search", "system", "screen_click", "look_at_screen",
        "remember", "browser", "focus_window", "set_reminder", "plan"]
# grupo -> (palabras clave, herramientas)
GROUPS = {
    "youtube": (r"youtube|yutub|v[ií]deo|canal|shorts|directo|reproduc|tr[aá]iler|canci[oó]n|anuncio|calidad|pantalla completa|"
                r"subt[ií]tulo|retrocede|avanza|adelanta|pausa|ese\b|pon\b|ponme|m[uú]sica", ["youtube"]),
    "docs": (r"word|pdf|documento|informe|resumen|redacta|escribe un|exporta|investiga", ["research_report", "write_document",
                                                                                        "convert_to_pdf", "web_research"]),
    "cuentas": (r"correo|mail|gmail|hotmail|outlook|agenda|calendario|evento|drive|archivo de google|qu[eé] tengo|"
                r"resumen del d[ií]a|buenos d[ií]as|notificaci|whatsapp|mensaje|telegram|l[eé]emelo|qu[eé] dice",
                ["email_check", "calendar_today", "drive_search", "daily_briefing", "notifications"]),
    "archivos": (r"carpeta|archivo|fichero|descargas|documentos|escritorio|im[aá]genes|fotos|busca el archivo",
                 ["open_folder", "find_files", "open_file"]),
    "teclado": (r"escribe|teclea|pulsa|tecla|atajo|rat[oó]n|copia|pega|control|alt\b", ["type_text", "press_keys", "mouse"]),
    "jarvis": (r"rutina|cada d[ií]a|todos los d[ií]as|voz|habla m[aá]s|ajuste|configura|actualiza|versi[oó]n|habilidad|"
               r"aprend|sabes de m[ií]|olvida|qui[eé]n eres|c[oó]mo te llamas|estado del (pc|ordenador|sistema)|bater[ií]a|memoria ram",
               ["create_routine", "list_routines", "delete_routine", "voices", "settings", "check_updates", "create_skill",
                "undo_skill", "about", "what_you_know", "forget", "system_status"]),
}


def select(text: str, recent: list[str] | None = None) -> list[str]:
    """Herramientas para esta petición: las básicas + los grupos que encajan con lo dicho o se usaron hace poco."""
    import re as _re
    names = list(CORE)
    low = (text or "").lower()
    recent = set(recent or [])
    for kw, group in GROUPS.values():
        if _re.search(kw, low) or recent.intersection(group):
            names += group
    names += [n for n, t in REGISTRY.items() if not t.core]       # habilidades propias
    seen = set()
    return [n for n in names if n in REGISTRY and not (n in seen or seen.add(n))]


def schemas(names: list[str] | None = None) -> list[dict]:
    if names is None:
        return [t.schema() for t in REGISTRY.values()]
    return [REGISTRY[n].schema() for n in names if n in REGISTRY]'''),
])

patch("jarvis/brain.py", [
    ('''    def _payload(self, messages: list, stream: bool, num_predict: int | None = None) -> dict:
        opts = {"num_ctx": int(settings["num_ctx"]), "temperature": 0.6, "top_p": 0.9}
        if num_predict is not None:
            opts["num_predict"] = num_predict
        return {"model": settings["model"], "messages": messages, "tools": tools.schemas(), "stream": stream,
                "think": False, "keep_alive": -1, "options": opts}''',
     '''    def _recent_tools(self) -> list[str]:
        names = []
        for m in self._turns():
            for c in m.get("tool_calls") or []:
                names.append(c.get("function", {}).get("name", ""))
        return names

    def _payload(self, messages: list, stream: bool, num_predict: int | None = None, tool_names: list | None = None) -> dict:
        opts = {"num_ctx": int(settings["num_ctx"]), "temperature": 0.6, "top_p": 0.9}
        if num_predict is not None:
            opts["num_predict"] = num_predict
        if tool_names is None:
            last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
            tool_names = tools.select(last_user, self._recent_tools())
        return {"model": settings["model"], "messages": messages, "tools": tools.schemas(tool_names), "stream": stream,
                "think": False, "keep_alive": -1, "options": opts}'''),
    # dentro de una misma petición, las herramientas no cambian entre pasos (caché) y se añaden las usadas
    ('''    def _stream(self, messages: list, on_text: Callable[[str], None]) -> tuple[str, list]:
        content, calls = "", []
        with self.session.post(settings["ollama_url"] + "/api/chat", json=self._payload(messages, True), stream=True,''',
     '''    def _stream(self, messages: list, on_text: Callable[[str], None]) -> tuple[str, list]:
        content, calls = "", []
        with self.session.post(settings["ollama_url"] + "/api/chat",
                               json=self._payload(messages, True, tool_names=getattr(self, "_run_tools", None)), stream=True,'''),
    ('''        messages = self._base_messages(user_text)
        if settings["screen_context"]''',
     '''        messages = self._base_messages(user_text)
        self._run_tools = tools.select(user_text, self._recent_tools())
        if settings["screen_context"]'''),
    ('''        try:
            self._save_turn(messages[self._hist_len + 1:])''',
     '''        self._run_tools = None
        try:
            self._save_turn(messages[self._hist_len + 1:])'''),
])

# descripciones más cortas en las herramientas grandes
patch("jarvis/tools/browser.py", [
    ('''@tool("youtube", "Controla YouTube en el navegador de Jarvis. "
      "play = buscar y reproducir el mejor resultado (SIEMPRE que diga 'pon X' o 'ponme X'). search = buscar y enseñar resultados (NO los recorre). "
      "browse_results = recorrer despacio los vídeos de la página resaltándolos para que el usuario diga 'ese' (solo si lo pide: 've bajando', 'enséñamelos uno a uno'). "
      "open_channel = abrir un CANAL (query = nombre; sin query, el canal del vídeo actual). "
      "channel_tab = abrir una sección del canal: inicio, vídeos, shorts, directos, listas (query = sección). "
      "play_title = reproducir el vídeo de esta página cuyo título se parezca a query. play_result = el n-ésimo. "
      "Además: pausa, pantalla completa, avanzar/retroceder segundos, calidad máxima, silenciar, velocidad, subtítulos, siguiente, saltar anuncio.",''',
     '''@tool("youtube", "YouTube. play: buscar y reproducir ('pon X'). search: enseñar resultados. open_channel: canal (query=nombre, section opcional). "
      "channel_tab: sección del canal. play_title: vídeo de la página por título. browse_results: recorrer resaltando (solo si lo pide). "
      "seek: value=segundos (negativo atrás).",'''),
])
patch("jarvis/tools/assistant.py", [
    ('''@tool("create_routine", "Crea una rutina automática: a una hora, al encender el PC o con una frase, Jarvis ejecuta acciones (herramientas existentes). Pide confirmación al usuario antes si no lo ha pedido él explícitamente.",''',
     '''@tool("create_routine", "Crea una rutina: a una hora, al encender o con una frase, ejecuta acciones (herramientas existentes).",'''),
    ('''@tool("create_skill", "Crea una HABILIDAD nueva escribiendo código Python cuando ninguna herramienta existente sirve. Solo con permiso del usuario. El código debe definir SKILL = {'name','description','parameters'} y def run(**kwargs) -> str, y solo puede importar módulos seguros y 'jarvis_api'.",''',
     '''@tool("create_skill", "Crea una habilidad nueva en Python (solo con permiso). Debe definir SKILL={'name','description','parameters'} y def run(**kwargs)->str; solo puede importar módulos seguros y jarvis_api.",'''),
])
print("ok")
