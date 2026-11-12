"""What every extractor produces: an ordered list of structural blocks plus warnings."""
from dataclasses import dataclass, field
from typing import Optional

from .textutil import clean_text


class IngestionError(ValueError):
    """A document that cannot be read at all (corrupt, encrypted, unsupported). Carries an HTTP status
    so an API layer can return it directly."""
    status_code = 422


@dataclass
class Finding:
    """One kind of risky feature found in an untrusted file by a pre-scan (secure.py, secure_docx.py)."""
    name: str
    severity: str  # "high" (rejected by default) | "medium" | "info"
    meaning: str
    count: int


@dataclass
class Block:
    kind: str  # "heading" | "paragraph" | "list" | "table"
    text: str  # tables: markdown; lists: one "- item" per line
    level: int = 0  # headings: 0 = document title, 1 = top section ... 6
    page: Optional[int] = None  # page the block starts on
    page_end: Optional[int] = None  # page it ends on, when it runs across a page break
    rows: Optional[list] = None  # tables: list of rows (header row first)


@dataclass
class ParsedDocument:
    source: str
    blocks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    pages: Optional[int] = None
    title: Optional[str] = None

    def stats(self) -> dict:
        counts = {kind: 0 for kind in ("heading", "paragraph", "list", "table")}
        for block in self.blocks:
            counts[block.kind] = counts.get(block.kind, 0) + 1
        return {"pages": self.pages, "warnings": len(self.warnings), **counts}


def _cell(value) -> str:
    return clean_text(" ".join(str(value or "").split())).replace("|", "\\|")


def rows_to_markdown(rows) -> str:
    header, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def tidy_rows(rows) -> list:
    """Clean cells, pad ragged rows and drop rows and columns that are entirely empty."""
    rows = [[_cell(c) for c in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    keep = [i for i in range(width) if any(row[i] for row in rows)]
    return [[row[i] for i in keep] for row in rows]


def is_real_table(rows) -> bool:
    """A grid needs at least two rows and two columns to be worth keeping as a table: single-row or
    single-column grids are almost always layout boxes, not data."""
    return len(rows) >= 2 and len(rows[0]) >= 2


def table_block(rows, page=None) -> Optional[Block]:
    rows = tidy_rows(rows)
    if not rows or not is_real_table(rows):
        return None
    return Block("table", rows_to_markdown(rows), page=page, rows=rows)


def table_or_paragraphs(rows, page=None) -> list:
    """A real table becomes one table block; a layout grid becomes one paragraph per distinct cell."""
    rows = tidy_rows(rows)
    if is_real_table(rows):
        return [Block("table", rows_to_markdown(rows), page=page, rows=rows)]
    seen, blocks = None, []
    for row in rows:
        for text in row:
            if text and text != seen:
                blocks.append(Block("paragraph", text, page=page))
            seen = text
    return blocks
