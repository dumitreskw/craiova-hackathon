"""DOCX and PDF export of reports.

Both serialize the same audience-specific blocks as the Markdown/HTML render
(:func:`app.report.build_blocks`), so a client DOCX can never show more than
the client HTML does.

DOCX follows the house layout used for due-diligence deliverables: A4, Arial,
a red header line ("- CONFIDENTIAL -" by default), a confidentiality footer
with page numbers, sections as bordered tables with a bold title row, and the
numbered evidence list with each screenshot under an italic caption. Header
and footer text come from ``OSINT_REPORT_HEADER`` / ``OSINT_REPORT_FOOTER``.

PDF prints the HTML render with headless Chromium (Playwright), so it matches
the on-screen preview, embedded screenshots included.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

DEFAULT_HEADER = "- CONFIDENTIAL -"
DEFAULT_FOOTER = ("This report is confidential and intended solely for the requesting party. "
                  "It is based on open sources available at the time of collection.")
RED = RGBColor(0xC0, 0x00, 0x00)
GREY = RGBColor(0x55, 0x55, 0x55)
TITLE_FILL = "D9D9D9"
IMAGE_WIDTH = Inches(6.2)


def header_text() -> str:
    return os.environ.get("OSINT_REPORT_HEADER", DEFAULT_HEADER)


def footer_text() -> str:
    return os.environ.get("OSINT_REPORT_FOOTER", DEFAULT_FOOTER)


def filename(title: str, audience: str, date: str, ext: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "report"
    return f"{slug}_{audience}_{date}.{ext}"


# --- DOCX -------------------------------------------------------------------------

def to_docx(blocks: list[tuple[str, Any]]) -> bytes:
    doc = Document()
    _page_setup(doc)
    _base_font(doc)
    _header_footer(doc)
    title: str | None = None  # a section's title row is drawn once, on its first block
    for kind, val in blocks:
        if kind == "h1":
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _run(p, val, bold=True, size=16)
        elif kind == "meta":
            _meta_table(doc, val)
        elif kind == "h2":
            title = val
        elif kind == "p":
            if not val:
                continue
            _section_table(doc, title, [_Para(val)])
            title = None
        elif kind == "claims":
            _claims_table(doc, title, val)
            title = None
        elif kind == "ul":
            _section_table(doc, title, [_Para(f"• {x}") for x in val])
            title = None
        elif kind == "evidence":
            _evidence(doc, title, val)
            title = None
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


class _Para:
    def __init__(self, text: str):
        self.text = text


def _page_setup(doc: Document) -> None:
    s = doc.sections[0]
    s.orientation = WD_ORIENT.PORTRAIT
    s.page_width, s.page_height = Cm(21.0), Cm(29.7)
    s.left_margin = s.right_margin = Cm(2.0)
    s.top_margin, s.bottom_margin = Cm(2.0), Cm(2.0)


def _base_font(doc: Document) -> None:
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(10)
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), "Arial")
    style.paragraph_format.space_after = Pt(2)


def _header_footer(doc: Document) -> None:
    s = doc.sections[0]
    hp = s.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(hp, header_text(), bold=True, size=11, color=RED)
    fp = s.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(fp, footer_text(), italic=True, size=7, color=GREY)
    np_ = s.footer.add_paragraph()
    np_.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(np_, "Page ", size=8, color=GREY)
    _field(np_, "PAGE")
    _run(np_, " of ", size=8, color=GREY)
    _field(np_, "NUMPAGES")


def _field(paragraph, code: str) -> None:
    run = paragraph.add_run()
    run.font.size = Pt(8)
    for tag, text in (("begin", None), (None, code), ("end", None)):
        if tag:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), tag)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = f" {text} "
        run._r.append(el)


def _run(paragraph, text: str, bold=False, italic=False, size=None, color=None):
    r = paragraph.add_run(text)
    r.bold, r.italic = bold, italic
    if size:
        r.font.size = Pt(size)
    if color is not None:
        r.font.color.rgb = color
    return r


def _shade(cell, fill: str) -> None:
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def _table(doc: Document, cols: int):
    t = doc.add_table(rows=0, cols=cols)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    return t


def _title_row(table, title: str | None) -> None:
    if not title:
        return
    row = table.add_row()
    cell = row.cells[0].merge(row.cells[-1]) if len(row.cells) > 1 else row.cells[0]
    _shade(cell, TITLE_FILL)
    p = cell.paragraphs[0]
    _run(p, title, bold=True, size=12)


def _spacer(doc: Document) -> None:
    doc.add_paragraph().paragraph_format.space_after = Pt(6)


def _meta_table(doc: Document, meta: dict) -> None:
    t = _table(doc, 2)
    for k, v in meta.items():
        cells = t.add_row().cells
        _run(cells[0].paragraphs[0], str(k), bold=True, size=9)
        _run(cells[1].paragraphs[0], str(v), size=9)
    _widths(t, [Cm(4.0), Cm(13.0)])
    _spacer(doc)


def _section_table(doc: Document, title: str | None, paras: list[_Para]) -> None:
    t = _table(doc, 1)
    _title_row(t, title)
    cell = t.add_row().cells[0]
    first = True
    for para in paras:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        _run(p, para.text, size=9)
    _spacer(doc)


def _claims_table(doc: Document, title: str | None, claims: list[tuple[str, list[int]]]) -> None:
    t = _table(doc, 2)
    _title_row(t, title)
    head = t.add_row().cells
    for cell, label in zip(head, ("Finding", "Evidence")):
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(p, label, bold=True, size=10)
    for text, refs in claims:
        cells = t.add_row().cells
        _claim_text(cells[0].paragraphs[0], text)
        p = cells[1].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(p, ", ".join(f"[{n}]" for n in refs) if refs else "—", size=9)
    _widths(t, [Cm(14.5), Cm(2.5)])
    _spacer(doc)


_LABEL = re.compile(r"^(?P<sev>\[[A-Z]+\] )?(?P<text>.*?)(?P<label> \((?:unverified|leaked record, "
                    r"unverified|analyst inference)\))?(?P<flag> \[sensitive\])?$", re.DOTALL)


def _claim_text(paragraph, text: str) -> None:
    """Claim text with its severity in bold and its status label in italics."""
    m = _LABEL.match(text)
    if not m:
        _run(paragraph, text, size=9)
        return
    if m.group("sev"):
        _run(paragraph, m.group("sev"), bold=True, size=9)
    _run(paragraph, m.group("text"), size=9)
    if m.group("label"):
        _run(paragraph, m.group("label"), italic=True, size=9, color=GREY)
    if m.group("flag"):
        _run(paragraph, m.group("flag"), bold=True, size=9, color=RED)


def _evidence(doc: Document, title: str | None, items: list[tuple[str, Path | None]]) -> None:
    t = _table(doc, 1)
    _title_row(t, title)
    cell = t.add_row().cells[0]
    first = True
    for line, img in items:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        _run(p, line, italic=img is not None, size=8 if img is not None else 9)
        if img is not None:
            pic = cell.add_paragraph()
            pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pic.add_run().add_picture(str(img), width=IMAGE_WIDTH)
    _spacer(doc)


def _widths(table, widths) -> None:
    for row in table.rows:
        cells = row.cells
        if len({c._tc for c in cells}) != len(widths):
            continue  # merged title row
        for cell, w in zip(cells, widths):
            cell.width = w


# --- PDF --------------------------------------------------------------------------

PRINT_CSS = ("<style>@page{size:A4;margin:18mm 16mm}body{max-width:none;margin:0;"
             "font:11px/1.45 Arial,Helvetica,sans-serif}h1{font-size:20px}h2{font-size:15px;"
             "margin-top:1.2em;border-bottom:1px solid #999}.evidence{font-size:10px}"
             ".evidence img{max-width:100%}</style>")


async def to_pdf(html: str) -> bytes:
    """Print the HTML render to A4 PDF with headless Chromium."""
    from playwright.async_api import async_playwright

    page_html = html.replace("</head>", PRINT_CSS + "</head>", 1)
    header = header_text().replace("<", "&lt;")
    footer = footer_text().replace("<", "&lt;")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(page_html, wait_until="load")
            return await page.pdf(
                format="A4", print_background=True, display_header_footer=True,
                margin={"top": "22mm", "bottom": "22mm", "left": "16mm", "right": "16mm"},
                header_template=("<div style='width:100%;text-align:center;font:bold 9px Arial;"
                                 f"color:#c00000'>{header}</div>"),
                footer_template=("<div style='width:100%;text-align:center;font:italic 7px Arial;"
                                 f"color:#555;padding:0 16mm'>{footer} &middot; Page "
                                 "<span class='pageNumber'></span> of "
                                 "<span class='totalPages'></span></div>"))
        finally:
            await browser.close()
