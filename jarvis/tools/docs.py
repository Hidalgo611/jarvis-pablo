"""Documentos: Word (.docx) y PDF a partir de texto con formato sencillo (estilo markdown)."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from ..config import DOCS_OUT
from . import S, tool

ACCENT = (0x0B, 0x4F, 0x7A)


def _safe(name: str) -> str:
    n = re.sub(r'[<>:"/\\|?*\n\r\t]+', " ", name).strip()[:80]
    return n or "Documento"


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    return p.with_name(f"{p.stem} ({time.strftime('%H-%M-%S')}){p.suffix}")


def _links(t: str) -> str:
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                  lambda m: m.group(2) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})", t)


def _blocks(content: str):
    for raw in _links(content).replace("\r", "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            yield ("h" + str(len(m.group(1))), m.group(2).strip())
        elif re.match(r"^\s*[-*•]\s+", line):
            yield ("bullet", re.sub(r"^\s*[-*•]\s+", "", line))
        elif re.match(r"^\s*\d+[.)]\s+", line):
            yield ("number", re.sub(r"^\s*\d+[.)]\s+", "", line))
        else:
            yield ("p", line.strip())


def _runs(par, text: str) -> None:
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if part:
            r = par.add_run(part)
            r.bold = i % 2 == 1


def _docx(title: str, content: str, path: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(11)
    h = doc.add_heading(title, level=0)
    for r in h.runs:
        r.font.color.rgb = RGBColor(*ACCENT)
    sub = doc.add_paragraph()
    rr = sub.add_run(f"Preparado por J.A.R.V.I.S. · {time.strftime('%d/%m/%Y %H:%M')}")
    rr.italic = True
    rr.font.size = Pt(9)
    rr.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    sub.alignment = WD_ALIGN_PARAGRAPH.LEFT
    for kind, text in _blocks(content):
        if kind.startswith("h"):
            hh = doc.add_heading(text, level=int(kind[1]))
            for r in hh.runs:
                r.font.color.rgb = RGBColor(*ACCENT)
        elif kind == "bullet":
            _runs(doc.add_paragraph(style="List Bullet"), text)
        elif kind == "number":
            _runs(doc.add_paragraph(style="List Number"), text)
        else:
            _runs(doc.add_paragraph(), text)
    doc.save(path)


def _font_path() -> tuple[str, str]:
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    reg, bold = fonts / "segoeui.ttf", fonts / "segoeuib.ttf"
    if not reg.exists():
        reg, bold = fonts / "arial.ttf", fonts / "arialbd.ttf"
    return str(reg), str(bold)


def _pdf(title: str, content: str, path: Path) -> None:
    from fpdf import FPDF

    reg, bold = _font_path()
    pdf = FPDF(format="A4")
    pdf.set_margins(20, 18, 20)
    pdf.set_auto_page_break(True, 18)
    pdf.add_font("UI", "", reg)
    pdf.add_font("UI", "B", bold)
    pdf.add_page()
    pdf.set_text_color(*ACCENT)
    pdf.set_font("UI", "B", 22)
    pdf.multi_cell(0, 10, title)
    pdf.set_text_color(110, 110, 110)
    pdf.set_font("UI", "", 9)
    pdf.cell(0, 8, f"Preparado por J.A.R.V.I.S. · {time.strftime('%d/%m/%Y %H:%M')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    for kind, text in _blocks(content):
        plain = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        if kind.startswith("h"):
            pdf.ln(3)
            pdf.set_text_color(*ACCENT)
            pdf.set_font("UI", "B", {"h1": 16, "h2": 14, "h3": 12}[kind])
            pdf.multi_cell(0, 8, plain)
        else:
            pdf.set_text_color(30, 30, 30)
            pdf.set_font("UI", "", 11)
            prefix = "•  " if kind == "bullet" else ""
            pdf.multi_cell(0, 6, prefix + plain, markdown=False)
            pdf.ln(1)
    pdf.output(str(path))


@tool("write_document", "Crea un documento Word (.docx) y/o PDF con el contenido dado y lo abre. El contenido admite '# Título', '## Sección', '- viñetas' y **negrita**. Incluye una sección '## Fuentes' con los enlaces si vienen de internet.",
      {"title": S("Título del documento"), "content": S("Contenido completo, bien redactado"),
       "format": S("Formato", ["docx", "pdf", "both"]),
       "open_after": {"type": "boolean", "description": "Abrir al terminar (por defecto sí)"}},
      ["title", "content"], label=lambda a: f"Redactando «{a.get('title', '')}»")
def write_document(title: str, content: str, format: str = "docx", open_after: bool = True) -> str:
    base = DOCS_OUT / _safe(title)
    made = []
    if format in ("docx", "both"):
        p = _unique(base.with_suffix(".docx"))
        _docx(title, content, p)
        made.append(p)
    if format in ("pdf", "both"):
        p = _unique(base.with_suffix(".pdf"))
        _pdf(title, content, p)
        made.append(p)
    if open_after and made:
        os.startfile(made[0])
    return "Documento creado: " + "; ".join(str(m) for m in made)


@tool("convert_to_pdf", "Convierte un documento Word existente (.docx) a PDF.", {"path": S("Ruta del .docx")}, ["path"],
      label="Exportando a PDF")
def convert_to_pdf(path: str) -> str:
    src = Path(path)
    if not src.exists():
        return "No encuentro ese archivo."
    dst = _unique(src.with_suffix(".pdf"))
    try:
        import win32com.client
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        d = word.Documents.Open(str(src))
        d.SaveAs(str(dst), FileFormat=17)
        d.Close()
        word.Quit()
    except Exception:
        from docx import Document
        doc = Document(str(src))
        text = "\n".join(("# " if p.style.name.startswith("Heading") or p.style.name == "Title" else "") + p.text
                         for p in doc.paragraphs)
        _pdf(src.stem, text, dst)
    os.startfile(dst)
    return f"PDF creado: {dst}"
