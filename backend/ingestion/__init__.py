"""Local, layout-aware document ingestion: PDF, DOCX, HTML, Markdown and text in, structured chunks out.

    parsed = ingest_bytes("report.pdf", data)       # blocks + warnings
    chunks = chunk_document(parsed)                  # heading path, pages, source on every chunk
    nodes = to_nodes(chunks)                         # LlamaIndex nodes (import ingestion.nodes)

No external API is called anywhere in this package. See each module's docstring for what it handles
and what it does not.
"""
import os
from pathlib import Path
from typing import Union

from .chunker import Chunk, chunk_document
from .html import parse_html
from .models import Block, IngestionError, ParsedDocument
from .secure import parse_docx_sandboxed, parse_pdf_checked, parse_pdf_sandboxed
from .secure_docx import parse_docx_checked
from .text import parse_text

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".html", ".htm", ".txt", ".md", ".markdown"}

__all__ = ["Block", "Chunk", "IngestionError", "ParsedDocument", "SUPPORTED_EXTENSIONS", "chunk_document",
           "ingest_bytes", "ingest_html", "ingest_path", "ingest_web_content", "read_docx", "read_pdf"]


def _sandboxed(setting: str, sandbox) -> bool:
    return os.getenv(setting, "1") != "0" if sandbox is None else sandbox


def read_pdf(data: bytes, source: str, sandbox=None) -> ParsedDocument:
    """A PDF read defensively: pre-scanned for active content, then parsed in a separate process
    (see ingestion/secure.py). `sandbox=False`, or INGEST_PDF_SANDBOX=0, parses in this process."""
    if _sandboxed("INGEST_PDF_SANDBOX", sandbox):
        return parse_pdf_sandboxed(data, source)
    return parse_pdf_checked(data, source)


def read_docx(data: bytes, source: str, sandbox=None) -> ParsedDocument:
    """A DOCX read defensively: pre-scanned for macros, objects, DDE fields and external references, then
    parsed in a separate process (see ingestion/secure_docx.py). `sandbox=False`, or
    INGEST_DOCX_SANDBOX=0, parses in this process."""
    if _sandboxed("INGEST_DOCX_SANDBOX", sandbox):
        return parse_docx_sandboxed(data, source)
    return parse_docx_checked(data, source)


def ingest_bytes(name: str, data: bytes, sandbox=None) -> ParsedDocument:
    """Parse a file by its extension. Raises IngestionError for unsupported or unreadable files."""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return read_pdf(data, name, sandbox)
    if suffix == ".docx":
        return read_docx(data, name, sandbox)
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
        return read_pdf(data, url)
    if kind in {"text", "markdown"}:
        return parse_text(data, url, markdown=kind == "markdown")
    return parse_html(data, url)
