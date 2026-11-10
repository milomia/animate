"""Layout-aware PDF extraction with PyMuPDF. Everything runs locally; no external service is called.

Per page: text blocks are read with their font sizes, tables are found from ruling lines, repeated
headers/footers are dropped, columns are put in reading order, and a page with no text layer is sent to
OCR when Tesseract is available (otherwise it is reported in `warnings`, never silently skipped).
Across the document: the body font size sets the baseline, larger or bold-only lines become headings,
and a paragraph split by a page or column break is stitched back together.

Known limits: tables without ruling lines are not detected; a table split across pages stays two tables;
text rotated away from horizontal is read as-is; OCR quality is whatever Tesseract gives.
"""
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

import fitz

from .models import Block, IngestionError, ParsedDocument, table_block
from .textutil import clean_text, join_lines

HEADER_FOOTER_BAND = 0.09  # top / bottom share of the page searched for running headers and footers
WIDE_BLOCK_SHARE = 0.62  # a block this wide is full-width and splits a page into bands
HEADING_SIZE_RATIO = 1.15
MAX_HEADING_CHARS = 160
MAX_BOLD_HEADING_CHARS = 80
MIN_PAGE_TEXT_CHARS = 25  # fewer characters than this on a page that has images means "scanned"
MIN_GUTTER_PT = 6.0

PAGE_NUMBER_RE = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.I)
BULLET_RE = re.compile(r"^\s*(?:[•●◦▪■‣∙·\-–—*]|\(?\d{1,2}[.)]|\(?[a-z][.)])\s+")
SENTENCE_END = (".", "!", "?", ":", ";", '"', "”", ")")


@dataclass
class _Raw:
    """One text block (or table) as read off a page, before structure is decided."""
    page: int
    bbox: tuple
    lines: list
    size: float
    bold: bool
    table: Optional[Block] = None

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]


def parse_pdf(data: bytes, source: str = "document.pdf") -> ParsedDocument:
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise IngestionError(f"Not a readable PDF: {exc}") from exc
    with pdf:
        if pdf.needs_pass:
            raise IngestionError("The PDF is password-protected.")
        warnings, scanned, ocr_used = [], [], []
        pages = []  # [(raw blocks, page width, page height)]
        for number, page in enumerate(pdf, start=1):
            raws = _page_raws(page, number)
            if _is_scanned(page, raws):
                raws = _ocr_raws(page, number)
                (ocr_used if raws else scanned).append(number)
            pages.append((raws, page.rect.width, page.rect.height))
        title = (pdf.metadata or {}).get("title") or None

    if ocr_used:
        warnings.append(f"Pages {_ranges(ocr_used)} have no text layer; text was recovered with OCR and may contain errors.")
    if scanned:
        warnings.append(f"Pages {_ranges(scanned)} look scanned (no text layer) and OCR is unavailable, "
                        "so they were skipped. Install Tesseract to read them.")

    pages = _strip_running_headers(pages)
    ordered = [raw for raws, width, _ in pages for raw in _reading_order(raws, width)]
    blocks = _structure(ordered)
    doc = ParsedDocument(source=source, blocks=_merge_continuations(blocks), warnings=warnings, pages=len(pages))
    doc.title = clean_text(title) if title and title.strip() else next(
        (b.text for b in doc.blocks if b.kind == "heading" and b.level <= 1), None)
    return doc


# --- reading one page -------------------------------------------------------------------------

def _page_raws(page, number):
    tables = _tables(page, number)
    raws = []
    for block in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
        if block.get("type") != 0 or _inside_any(block["bbox"], [rect for rect, _ in tables]):
            continue
        raw = _raw_from_block(block, number)
        if raw:
            raws.append(raw)
    raws += [_Raw(number, tuple(rect), [], 0.0, False, table=table) for rect, table in tables]
    return raws


def _raw_from_block(block, number):
    lines, sizes, bold_chars, total = [], Counter(), 0, 0
    for line in block["lines"]:
        text = clean_text("".join(span["text"] for span in line["spans"]))
        if not text:
            continue
        lines.append(text)
        for span in line["spans"]:
            chars = len(span["text"].strip())
            sizes[round(span["size"] * 2) / 2] += chars
            total += chars
            if span["flags"] & 16 or "bold" in span["font"].lower():
                bold_chars += chars
    if not lines or not total:
        return None
    return _Raw(number, tuple(block["bbox"]), lines, sizes.most_common(1)[0][0], bold_chars / total > 0.6)


def _tables(page, number):
    try:
        found = page.find_tables().tables
    except Exception:
        return []
    out = []
    for table in found:
        block = table_block(table.extract(), number)
        if block:
            out.append((fitz.Rect(table.bbox), block))
    return out


def _inside_any(bbox, rects):
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return any(rect.x0 - 1 <= cx <= rect.x1 + 1 and rect.y0 - 1 <= cy <= rect.y1 + 1 for rect in rects)


def _is_scanned(page, raws):
    chars = sum(len(" ".join(raw.lines)) for raw in raws if raw.table is None)
    return chars < MIN_PAGE_TEXT_CHARS and not any(raw.table for raw in raws) and bool(page.get_images())


def _ocr_raws(page, number):
    """Text blocks from OCR, or [] when Tesseract is not installed or finds nothing."""
    try:
        textpage = page.get_textpage_ocr(language=os.getenv("INGEST_OCR_LANG", "eng"), dpi=300, full=True)
        data = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT, textpage=textpage)
    except Exception:
        return []
    return [raw for block in data["blocks"] if block.get("type") == 0 and (raw := _raw_from_block(block, number))]


# --- running headers and footers --------------------------------------------------------------

def _band(raw, height):
    if raw.bbox[3] <= height * HEADER_FOOTER_BAND:
        return "top"
    if raw.bbox[1] >= height * (1 - HEADER_FOOTER_BAND):
        return "bottom"
    return None


def _running_key(raw):
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", raw.text.lower())).strip()


def _strip_running_headers(pages):
    """Drop text that repeats in the top or bottom band of at least half the pages (headers, footers,
    "Page 3 of 10"), and any bare page number in those bands."""
    counts = Counter()
    for raws, _, height in pages:
        counts.update({(_band(r, height), _running_key(r)) for r in raws if r.table is None and _band(r, height)})
    threshold = max(2, math.ceil(len(pages) / 2))
    repeated = {key for key, count in counts.items() if count >= threshold}

    def keep(raw, height):
        band = _band(raw, height)
        if raw.table is not None or not band:
            return True
        return (band, _running_key(raw)) not in repeated and not PAGE_NUMBER_RE.match(raw.text.strip())

    return [([r for r in raws if keep(r, height)], width, height) for raws, width, height in pages]


# --- reading order ----------------------------------------------------------------------------

def _reading_order(raws, page_width):
    """Top to bottom, except that a multi-column band is read column by column. Full-width blocks
    (titles, wide tables) cut the page into bands so a heading above two columns stays above them."""
    ordered, band = [], []

    def flush():
        ordered.extend(_order_band(band, page_width))
        band.clear()

    for raw in sorted(raws, key=lambda r: (r.bbox[1], r.bbox[0])):
        if raw.width >= WIDE_BLOCK_SHARE * page_width:
            flush()
            ordered.append(raw)
        else:
            band.append(raw)
    flush()
    return ordered


def _order_band(band, page_width):
    gutters = _gutters(band, page_width)
    if not gutters:
        return sorted(band, key=lambda r: (r.bbox[1], r.bbox[0]))

    def column(raw):
        return sum(raw.bbox[0] >= gutter for gutter in gutters)

    return sorted(band, key=lambda r: (column(r), r.bbox[1], r.bbox[0]))


def _gutters(band, page_width):
    """x positions of empty vertical strips between columns, found as gaps in the union of block spans."""
    if len(band) < 2:
        return []
    merged = []
    for x0, x1 in sorted((r.bbox[0], r.bbox[2]) for r in band):
        if merged and x0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    min_gap = max(MIN_GUTTER_PT, 0.012 * page_width)
    return [(a[1] + b[0]) / 2 for a, b in zip(merged, merged[1:])
            if b[0] - a[1] >= min_gap and 0.15 * page_width <= (a[1] + b[0]) / 2 <= 0.85 * page_width]


# --- structure --------------------------------------------------------------------------------

def _structure(ordered):
    sizes = Counter()
    for raw in ordered:
        if raw.table is None:
            sizes[raw.size] += sum(len(line) for line in raw.lines)
    body = sizes.most_common(1)[0][0] if sizes else 0.0
    heading_sizes = sorted({r.size for r in ordered if _is_big_heading(r, body)}, reverse=True)
    level_of = {size: min(rank + 1, 4) for rank, size in enumerate(heading_sizes)}
    bold_level = min(len(heading_sizes) + 1, 5)

    blocks = []
    for raw in ordered:
        if raw.table is not None:
            blocks.append(raw.table)
        elif _is_big_heading(raw, body):
            blocks.append(Block("heading", join_lines(raw.lines), level=level_of[raw.size], page=raw.page))
        elif _is_bold_heading(raw):
            blocks.append(Block("heading", join_lines(raw.lines), level=bold_level, page=raw.page))
        else:
            blocks.extend(_text_blocks(raw))
    return blocks


def _is_big_heading(raw, body):
    return (raw.table is None and body > 0 and raw.size >= body * HEADING_SIZE_RATIO
            and len(raw.text) <= MAX_HEADING_CHARS and len(raw.lines) <= 3)


def _is_bold_heading(raw):
    text = raw.text.strip()
    return (raw.table is None and raw.bold and len(raw.lines) == 1 and len(text) <= MAX_BOLD_HEADING_CHARS
            and not text.endswith((".", ",", ";", ":")) and not BULLET_RE.match(text))


def _text_blocks(raw):
    """A text block becomes paragraphs and bullet lists: a bullet starts a new item, other lines wrap."""
    parts = []  # [("p" | "li", [lines])]
    for line in raw.lines:
        if BULLET_RE.match(line):
            parts.append(("li", [BULLET_RE.sub("", line, count=1)]))
        elif parts:
            parts[-1][1].append(line)
        else:
            parts.append(("p", [line]))
    blocks = []
    for kind, lines in parts:
        text = join_lines(lines)
        if kind == "li":
            item = "- " + text
            if blocks and blocks[-1].kind == "list":
                blocks[-1].text += "\n" + item
            else:
                blocks.append(Block("list", item, page=raw.page))
        elif text:
            blocks.append(Block("paragraph", text, page=raw.page))
    return blocks


def _merge_continuations(blocks):
    """Stitch a paragraph that was split by a page or column break back together: the earlier part does
    not end a sentence and the later part starts lowercase."""
    out = []
    for block in blocks:
        prev = out[-1] if out else None
        if (prev and prev.kind == block.kind == "paragraph" and not prev.text.endswith(SENTENCE_END)
                and block.text[:1].islower()):
            prev.text = join_lines([prev.text, block.text])
            if block.page != prev.page:
                prev.page_end = block.page
        else:
            out.append(block)
    return out


def _ranges(numbers):
    return ", ".join(str(n) for n in numbers)
