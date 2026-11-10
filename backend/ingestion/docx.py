"""DOCX extraction straight from the OOXML, with no external service and no extra dependency (lxml only).

Word styles become headings ("Title" is level 0, "Heading N" level N, plus outline levels and style
inheritance), bulleted/numbered paragraphs become list items, and tables keep their rows: merged cells
are repeated so every row stands alone when retrieved. Headers, footers, comments and tracked
deletions are not part of the body text and are left out. Page numbers are approximate: they come
from Word's saved page-break markers, so a file never opened in Word may report a single page.

Not extracted: text boxes, footnotes and endnotes, and content inside images.
"""
import io
import re
import zipfile

from lxml import etree

from .models import Block, IngestionError, ParsedDocument, table_or_paragraphs
from .textutil import clean_text

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
MAX_XML_BYTES = 80 * 1024 * 1024  # guards against zip bombs


def _w(tag):
    return f"{{{W}}}{tag}"


_SKIP = {_w("txbxContent"), f"{{{MC}}}Fallback", _w("instrText"), _w("delText")}
_BREAK = object()  # marks a page break inside a paragraph's text parts
_LIST_STYLE_RE = re.compile(r"^list (bullet|number|paragraph|continue)", re.I)


def parse_docx(data: bytes, source: str = "document.docx") -> ParsedDocument:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise IngestionError("Not a .docx file (it is not a zip archive; legacy .doc files are not supported).") from exc
    with archive:
        if "word/document.xml" not in archive.namelist():
            raise IngestionError("Not a .docx file: word/document.xml is missing.")
        if archive.getinfo("word/document.xml").file_size > MAX_XML_BYTES:
            raise IngestionError("The document is too large to read safely.")
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        try:
            root = etree.fromstring(archive.read("word/document.xml"), parser=parser)
            styles = _read_styles(etree.fromstring(archive.read("word/styles.xml"), parser=parser)) \
                if "word/styles.xml" in archive.namelist() else {}
        except etree.XMLSyntaxError as exc:
            raise IngestionError(f"The document XML is damaged: {exc}") from exc

    body = root.find(_w("body"))
    if body is None:
        raise IngestionError("The document has no body.")
    reader = _Reader(styles)
    reader.walk(body)
    blocks = reader.finish()

    doc = ParsedDocument(source=source, blocks=blocks, pages=reader.page)
    doc.title = next((b.text for b in blocks if b.kind == "heading" and b.level <= 1), None)
    if reader.saw_text_box:
        doc.warnings.append("The document contains text boxes, which are not extracted.")
    return doc


# --- styles -----------------------------------------------------------------------------------

def _read_styles(root):
    styles = {}
    for style in root.iter(_w("style")):
        style_id = style.get(_w("styleId"))
        name = style.find(_w("name"))
        based_on = style.find(_w("basedOn"))
        outline = style.find(f"{_w('pPr')}/{_w('outlineLvl')}")
        styles[style_id] = {
            "name": (name.get(_w("val")) if name is not None else "") or "",
            "based_on": based_on.get(_w("val")) if based_on is not None else None,
            "outline": _int(outline.get(_w("val"))) if outline is not None else None,
        }
    return styles


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _style_chain(styles, style_id):
    for _ in range(10):  # bounded: a style file can contain a cycle
        style = styles.get(style_id)
        if not style:
            return
        yield style
        style_id = style["based_on"]


def _heading_level(styles, style_id, outline):
    """0 for Title, 1..9 for headings, None for body text."""
    if outline is not None and outline < 9:
        return outline + 1
    for style in _style_chain(styles, style_id):
        name = style["name"].lower()
        if name == "title":
            return 0
        match = re.match(r"heading\s*(\d)", name)
        if match:
            return int(match.group(1))
        if style["outline"] is not None and style["outline"] < 9:
            return style["outline"] + 1
    match = re.match(r"heading(\d)$", (style_id or "").lower())
    return int(match.group(1)) if match else None


def _is_list_style(styles, style_id):
    return any(_LIST_STYLE_RE.match(style["name"]) for style in _style_chain(styles, style_id))


# --- body -------------------------------------------------------------------------------------

class _Reader:
    def __init__(self, styles):
        self.styles = styles
        self.blocks = []
        self.page = 1
        self.saw_text_box = False

    def walk(self, container):
        for child in container:
            tag = child.tag
            if tag == _w("p"):
                self._paragraph(child)
            elif tag == _w("tbl"):
                self.blocks.extend(table_or_paragraphs(self._table_rows(child), self.page))
            elif tag == _w("sdt"):  # content controls wrap ordinary paragraphs and tables
                content = child.find(_w("sdtContent"))
                if content is not None:
                    self.walk(content)

    def _paragraph(self, p):
        ppr = p.find(_w("pPr"))
        style_id = outline = None
        numbered = False
        indent = 0
        if ppr is not None:
            style = ppr.find(_w("pStyle"))
            style_id = style.get(_w("val")) if style is not None else None
            lvl = ppr.find(_w("outlineLvl"))
            outline = _int(lvl.get(_w("val"))) if lvl is not None else None
            num = ppr.find(_w("numPr"))
            if num is not None:
                num_id = num.find(_w("numId"))
                numbered = num_id is None or num_id.get(_w("val")) != "0"
                ilvl = num.find(_w("ilvl"))
                indent = _int(ilvl.get(_w("val")), 0) if ilvl is not None else 0
        parts = self._parts(p)
        leading = 0
        for part in parts:
            if part is _BREAK:
                leading += 1
            elif part.strip():
                break
        total = sum(part is _BREAK for part in parts)
        text = clean_text("".join(part for part in parts if part is not _BREAK))
        start, end = self.page + leading, self.page + total
        self.page = end
        if not text:
            return
        page_end = end if end != start else None
        level = _heading_level(self.styles, style_id, outline)
        if level is not None:
            self.blocks.append(Block("heading", text, level=level, page=start, page_end=page_end))
        elif numbered or _is_list_style(self.styles, style_id):
            item = "  " * min(indent, 4) + "- " + text
            if self.blocks and self.blocks[-1].kind == "list" and self.blocks[-1].page_end is None:
                self.blocks[-1].text += "\n" + item
            else:
                self.blocks.append(Block("list", item, page=start, page_end=page_end))
        else:
            self.blocks.append(Block("paragraph", text, page=start, page_end=page_end))

    def _parts(self, element):
        parts = []

        def walk(node):
            for child in node:
                tag = child.tag
                if not isinstance(tag, str):
                    continue
                if tag == _w("txbxContent"):
                    self.saw_text_box = True
                if tag in _SKIP:
                    continue
                if tag == _w("t"):
                    parts.append(child.text or "")
                elif tag in (_w("tab"), _w("cr")):
                    parts.append(" ")
                elif tag == _w("br"):
                    parts.append(_BREAK if child.get(_w("type")) == "page" else " ")
                elif tag == _w("lastRenderedPageBreak"):
                    parts.append(_BREAK)
                elif tag == _w("noBreakHyphen"):
                    parts.append("-")
                else:
                    walk(child)

        walk(element)
        return parts

    def _table_rows(self, table):
        """Rows of cell text, with merged cells repeated: horizontally across the span, vertically down
        from the cell above, so each row is meaningful on its own."""
        rows = []
        for tr in table.findall(_w("tr")):
            previous = rows[-1] if rows else []
            row = []
            for tc in tr.findall(_w("tc")):
                props = tc.find(_w("tcPr"))
                span, vmerge = 1, None
                if props is not None:
                    grid = props.find(_w("gridSpan"))
                    span = max(1, min(_int(grid.get(_w("val")), 1), 64)) if grid is not None else 1
                    merge = props.find(_w("vMerge"))
                    if merge is not None:
                        vmerge = merge.get(_w("val"), "continue")
                if vmerge == "continue":
                    text = previous[len(row)] if len(row) < len(previous) else ""
                else:
                    text = " ".join(filter(None, (clean_text("".join(
                        part for part in self._parts(p) if part is not _BREAK)) for p in tc.iter(_w("p")))))
                row.extend([text] * span)
            rows.append(row)
        return rows

    def finish(self):
        return [b for b in self.blocks if b.text]
