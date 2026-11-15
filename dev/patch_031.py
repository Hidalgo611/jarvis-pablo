def patch(p, pairs):
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert a in s, (p, a[:70])
        s = s.replace(a, b, 1)
    open(p, "w", encoding="utf-8").write(s)


patch("jarvis/config.py", [
    ('''    "model": "qwen3.5:9b",         # cerebro principal: herramientas + visión (ve la pantalla)''',
     '''    "model": "qwen3.5:4b",         # cerebro principal: herramientas + visión; cabe entero en 8 GB de VRAM'''),
    ('''    "num_ctx": 8192,''', '''    "num_ctx": 12288,'''),
    ('''        self.data["settings_version"] = 3''',
     '''        if v < 4:
            # 0.3.1: el 9B no cabe entero en 8 GB (se iba en parte a la CPU) → 4B, más rápido y también con visión
            if self.data.get("model") in ("qwen3.5:9b", "qwen3:8b"):
                if self.data.get("model") == "qwen3.5:9b":
                    self.data["cleanup_models"] = ["qwen3.5:9b"]
                self.data["model"] = DEFAULTS["model"]
            if self.data.get("num_ctx") == 8192:
                self.data["num_ctx"] = DEFAULTS["num_ctx"]
        self.data["settings_version"] = 4'''),
])

patch("jarvis/installer.py", [
    ('''def pick_model() -> str:
    v = _vram_mb()
    if v >= 7500:
        return "qwen3.5:9b"''',
     '''def pick_model() -> str:
    v = _vram_mb()
    if v >= 12000:
        return "qwen3.5:9b"
    if v >= 7500:
        return "qwen3.5:4b"'''),
    ('''    if m not in have and f"{m}:latest" not in have:
        pull(m, "cerebro (modelo de lenguaje)")''',
     '''    if m not in have and f"{m}:latest" not in have:
        pull(m, "cerebro (modelo de lenguaje)")
    # borra modelos que Jarvis ya no usa (solo los que descargó él) para liberar disco
    for old in settings.get("cleanup_models") or []:
        if old != m:
            try:
                requests.delete(settings["ollama_url"] + "/api/delete", json={"model": old}, timeout=30)
                log.info("modelo antiguo borrado: %s", old)
            except Exception:
                log.exception("borrar %s", old)
    if settings.get("cleanup_models"):
        settings.set("cleanup_models", [])'''),
])

patch("jarvis/brain.py", [
    ('''- "Búscame X y hazme un resumen/informe en Word o PDF": usa `research_report` (lo hace todo).''',
     '''- "Búscame X y hazme un resumen/informe en Word o PDF": llama DIRECTAMENTE a `research_report` (busca, lee y redacta él solo; no uses web_search antes).'''),
])
print("ok")
