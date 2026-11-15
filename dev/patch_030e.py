p = "jarvis/app.py"
s = open(p, encoding="utf-8").read()
a = s.index("    def _is_user_speech(self, heard: str) -> bool:")
b = s.index("    def _on_speaking(self, active: bool) -> None:")
s = s[:a] + '''    def _is_user_speech(self, heard: str) -> bool:
        """Mientras Jarvis habla: ¿lo que entra por el micro es el usuario (interrumpe) o el eco de su propia voz?"""
        return is_user_speech(heard, [t for ts, t in self.speaker.recent if time.time() - ts < 25])

''' + s[b:]
s = s.replace('''def make_icon() -> QIcon:''', '''def is_user_speech(heard: str, said_sentences: list[str]) -> bool:
    W = r"[a-záéíóúñü]{3,}"
    h = heard.lower()
    said_words = set(re.findall(W, " ".join(said_sentences).lower()))
    for m in BARGE_WORDS.finditer(h):
        if not set(re.findall(W, m.group(0))) <= said_words:   # palabra de parada que Jarvis no está diciendo
            return True
    words = re.findall(W, h)
    if len(words) < 3:
        return False
    overlap = sum(w in said_words for w in words) / len(words)
    return overlap < 0.35


def make_icon() -> QIcon:''', 1)
open(p, "w", encoding="utf-8").write(s)
print("ok")
