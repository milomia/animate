"""Turn a ParsedDocument into retrieval chunks that know where they came from.

Each chunk carries its heading path ("Safety > Dosing"), the pages it spans and its source file, so a
retrieved passage can be placed and cited. Chunks never cross a heading, and a table is never merged into
prose or cut through a row: a long table is split between rows with its header row repeated.

`max_words` limits the new text in a chunk; `overlap_words` of the previous chunk's tail are prepended
within a section so a sentence cut at a boundary is still whole in one of the two chunks.
"""
import re
from dataclasses import dataclass
from typing import Optional

from .models import ParsedDocument, rows_to_markdown
from .textutil import word_count

DEFAULT_MAX_WORDS = 300
DEFAULT_OVERLAP_WORDS = 40
TABLE_WORD_ALLOWANCE = 2  # a table may be this many times max_words before it is split between rows
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")


@dataclass
class Chunk:
    text: str
    source: str
    section: str = ""  # heading path joined with " > "
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    kind: str = "text"  # "text" | "table"
    index: int = 0

    @property
    def pages(self) -> str:
        if self.page_start is None:
            return ""
        end = self.page_end if self.page_end is not None else self.page_start
        return str(self.page_start) if end == self.page_start else f"{self.page_start}-{end}"

    def with_context(self) -> str:
        """The chunk text prefixed by its heading path, for consumers that only see the text."""
        return f"{self.section}\n{self.text}" if self.section else self.text


def chunk_document(doc: ParsedDocument, max_words: int = DEFAULT_MAX_WORDS,
                   overlap_words: int = DEFAULT_OVERLAP_WORDS) -> list:
    if max_words < 1 or not 0 <= overlap_words < max_words:
        raise ValueError("need max_words >= 1 and 0 <= overlap_words < max_words")
    chunks, path, pending = [], [], []  # path: [(level, heading text)]; pending: text units of this section

    def section():
        return " > ".join(text for _, text in path)

    def flush():
        chunks.extend(_pack(pending, section(), doc.source, max_words, overlap_words))
        pending.clear()

    for block in doc.blocks:
        if block.kind == "heading":
            flush()
            while path and path[-1][0] >= block.level:
                path.pop()
            path.append((block.level, block.text))
        elif block.kind == "table":
            flush()
            chunks.extend(_table_chunks(block, section(), doc.source, max_words))
        else:
            pending.append((block.text, block.kind, block.page, block.page_end if block.page_end is not None else block.page))
    flush()
    for index, chunk in enumerate(chunks):
        chunk.index = index
    return chunks


def _split_unit(text, kind, page, page_end, max_words):
    """A unit longer than max_words is cut at sentence (or list-item) boundaries, and by words as a last resort."""
    if word_count(text) <= max_words:
        return [(text, page, page_end)]
    segments = text.split("\n") if kind == "list" else SENTENCE_SPLIT_RE.split(text)
    joiner = "\n" if kind == "list" else " "
    pieces, current, words = [], [], 0
    for segment in segments:
        count = word_count(segment)
        if count > max_words:  # one enormous sentence or line
            if current:
                pieces.append(joiner.join(current))
                current, words = [], 0
            tokens = segment.split()
            pieces.extend(" ".join(tokens[i:i + max_words]) for i in range(0, len(tokens), max_words))
        elif words + count > max_words and current:
            pieces.append(joiner.join(current))
            current, words = [segment], count
        else:
            current.append(segment)
            words += count
    if current:
        pieces.append(joiner.join(current))
    return [(piece, page, page_end) for piece in pieces]


def _pack(units, section, source, max_words, overlap_words):
    pieces = []
    for text, kind, page, page_end in units:
        pieces.extend(_split_unit(text, kind, page, page_end, max_words))
    chunks, current, words, tail = [], [], 0, ""

    def emit():
        nonlocal tail
        body = "\n\n".join(text for text, _, _ in current)
        pages = [p for _, start, end in current for p in (start, end) if p is not None]
        text = (tail + " " + body).strip() if tail else body
        chunks.append(Chunk(text=text, source=source, section=section,
                            page_start=min(pages) if pages else None, page_end=max(pages) if pages else None))
        tail = " ".join(body.split()[-overlap_words:]) if overlap_words else ""

    for piece in pieces:
        count = word_count(piece[0])
        if current and words + count > max_words:
            emit()
            current, words = [], 0
        current.append(piece)
        words += count
    if current:
        emit()
    return chunks


def _table_chunks(block, section, source, max_words):
    rows = block.rows or []
    base = dict(source=source, section=section, page_start=block.page,
                page_end=block.page_end if block.page_end is not None else block.page, kind="table")
    if not rows or word_count(block.text) <= max_words * TABLE_WORD_ALLOWANCE:
        return [Chunk(text=block.text, **base)]
    header, body = rows[0], rows[1:]
    chunks, group, words = [], [], 0
    for row in body:
        count = word_count(" ".join(row))
        if group and words + count > max_words:
            chunks.append(Chunk(text=rows_to_markdown([header] + group), **base))
            group, words = [], 0
        group.append(row)
        words += count
    if group:
        chunks.append(Chunk(text=rows_to_markdown([header] + group), **base))
    return chunks
