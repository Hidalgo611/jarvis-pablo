def patch(p, pairs):
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert a in s, (p, a[:70])
        s = s.replace(a, b)
    open(p, "w", encoding="utf-8").write(s)


patch("jarvis/tools/docs.py", [
    ('pdf.multi_cell(0, 10, title)', 'pdf.multi_cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")'),
    ('pdf.multi_cell(0, 8, plain)', 'pdf.multi_cell(0, 8, plain, new_x="LMARGIN", new_y="NEXT")'),
    ('pdf.multi_cell(0, 6, prefix + plain, markdown=False)', 'pdf.multi_cell(0, 6, prefix + plain, new_x="LMARGIN", new_y="NEXT")'),
])
patch("jarvis/tools/__init__.py", [
    ('''    names += [n for n, t in REGISTRY.items() if not t.core]       # habilidades propias''',
     '''    names += [n for n, t in REGISTRY.items() if not t.core]       # habilidades propias
    # «búscame X y hazme un Word/PDF/informe»: solo la herramienta que lo hace todo (los modelos pequeños se lían)
    if _re.search(r"(word|pdf|documento|informe)", low) and _re.search(r"(busca|b[uú]scame|investiga|noticias|inf[oó]rmate|"
                                                                       r"qu[eé] (est[aá]|ha) pasa|sobre)", low):
        names = [n for n in names if n not in ("web_search", "web_research", "write_document")]'''),
])
print("ok")
