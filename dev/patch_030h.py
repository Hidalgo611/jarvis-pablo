def patch(p, pairs):
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert a in s, (p, a[:70]); s = s.replace(a, b, 1)
    open(p, "w", encoding="utf-8").write(s)
patch("jarvis/tools/__init__.py", [("REGISTRY: dict[str, Tool] = {}", "REGISTRY: dict[str, Tool] = {}\nCURRENT = {\"user_text\": \"\"}   # lo que pidió el usuario en este turno")])
patch("jarvis/brain.py", [("        self._run_tools = tools.select(user_text, self._recent_tools())",
                           "        self._run_tools = tools.select(user_text, self._recent_tools())\n        tools.CURRENT[\"user_text\"] = user_text")])
patch("jarvis/tools/browser.py", [('''    b = browser
    if action in ("search", "play"):''', '''    b = browser
    from . import CURRENT
    if action == "search" and re.match(r"^\W*(pon|ponme|ponlo|reproduce|quiero ver)\b", CURRENT.get("user_text", ""), re.I):
        action = "play"   # «pon X» siempre reproduce
    if action in ("search", "play"):''')])
print("ok")
