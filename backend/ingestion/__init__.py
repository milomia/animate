"""Local, layout-aware document ingestion: PDF, DOCX, HTML, Markdown and text in, structured chunks out.

    parsed = ingest_bytes("report.pdf", data)       # blocks + warnings
    chunks = chunk_document(parsed)                  # heading path, pages, source on every chunk
    nodes = to_nodes(chunks)                         # LlamaIndex nodes (import ingestion.nodes)

No external API is called anywhere in this package. See each module's docstring for what it handles
and what it does not.
"""
from pathlib import Path
from typing import Union

from .chunker import Chunk, chunk_document
from .docx import parse_docx
from .html import parse_html
from .models import Block, IngestionError, ParsedDocument
from .pdf import parse_pdf
from .text import parse_text

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".html", ".htm", ".txt", ".md", ".markdown"}

__all__ = ["Block", "Chunk", "IngestionError", "ParsedDocument", "SUPPORTED_EXTENSIONS", "chunk_document",
           "ingest_bytes", "ingest_html", "ingest_path", "ingest_web_content"]


def ingest_bytes(name: str, data: bytes) -> ParsedDocument:
    """Parse a file by its extension. Raises IngestionError for unsupported or unreadable files."""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(data, name)
    if suffix == ".docx":
        return parse_docx(data, name)
    if suffix in {".html", ".htm"}:
        return parse_html(data, name)
    if suffix in {".md", ".markdown"}:
        return parse_text(data, name, markdown=True)
    if suffix == ".txt":
        return parse_text(data, name)
    raise IngestionError(f"Unsupported file type: {name}")


def ingest_path(path: Union[str, Path]) -> ParsedDocument:
    path = Path(path)
    return ingest_bytes(path.name, path.read_bytes())


def ingest_html(html: Union[str, bytes], source: str = "page.html") -> ParsedDocument:
    return parse_html(html, source)


def ingest_web_content(url: str, data: bytes, kind: str) -> ParsedDocument:
    """Parse what `ingestion.web.fetch_url` returned; `kind` is its second value."""
    if kind == "pdf":
        return parse_pdf(data, url)
    if kind in {"text", "markdown"}:
        return parse_text(data, url, markdown=kind == "markdown")
    return parse_html(data, url)
