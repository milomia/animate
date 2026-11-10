"""Plain text and Markdown: headings (`#`), bullet lists, pipe tables and paragraphs."""
import re

from .models import Block, ParsedDocument, table_block
from .textutil import clean_text, join_lines

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+(.*)$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def decode(data: bytes):
    """(text, warning or None). UTF-8 first, then Windows-1252, the usual encoding of legacy exports."""
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "The file is not UTF-8; it was read as Windows-1252."


def parse_text(data: bytes, source: str = "document.txt", markdown: bool = False) -> ParsedDocument:
    text, warning = decode(data)
    doc = ParsedDocument(source=source, warnings=[warning] if warning else [])
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    paragraph, items, rows = [], [], []
    in_fence = False

    def flush():
        if paragraph:
            doc.blocks.append(Block("paragraph", join_lines(clean_text(line) for line in paragraph)))
        if items:
            doc.blocks.append(Block("list", "\n".join(items)))
        if rows:
            table = table_block(rows)
            if table:
                doc.blocks.append(table)
            else:
                doc.blocks.extend(Block("paragraph", " ".join(row)) for row in rows)
        paragraph.clear(), items.clear(), rows.clear()

    for line in lines:
        if markdown and line.strip().startswith("```"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            if line.strip():
                paragraph.append(line)
            continue
        if not line.strip():
            flush()
            continue
        heading = HEADING_RE.match(line) if markdown else None
        if heading:
            flush()
            doc.blocks.append(Block("heading", clean_text(heading.group(2)), level=len(heading.group(1))))
        elif markdown and line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            if not rows:
                flush()
            if not TABLE_SEPARATOR_RE.match(line):
                rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        elif markdown and LIST_RE.match(line):
            if paragraph or rows:
                flush()
            items.append("- " + clean_text(LIST_RE.match(line).group(1)))
        else:
            if items or rows:
                flush()
            paragraph.append(line)
    flush()
    doc.title = next((b.text for b in doc.blocks if b.kind == "heading" and b.level <= 1), None)
    return doc
