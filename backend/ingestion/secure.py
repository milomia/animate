"""Reading an untrusted PDF defensively.

Three layers, each independent of the others:

1. Check before parsing (`check_pdf`): the file really starts like a PDF; page and object counts are within
   limits; every object is scanned for features that run code or reach outside the file (JavaScript,
   launch actions, embedded files, rich media, XFA forms, form submission). The scan decodes escaped
   names ("/J#53" is "/JS") and looks inside compressed object streams. By default a PDF with any
   high-risk feature is rejected; set INGEST_PDF_POLICY=warn to accept it with a warning instead.
2. Parse in a separate process (`parse_pdf_sandboxed`): a child interpreter that gets the file on stdin, a
   scrubbed environment (no API keys or paths), an empty working directory, a wall-clock timeout, CPU,
   core-dump and (where the OS supports it) memory limits, and a socket layer that refuses connections. If
   the parser crashes or hangs, the caller gets an error and the service keeps running.
3. Filter what is extracted: hidden text (tiny, or white on a plain background) is removed and reported, see
   pdf.py, because it is a way to plant instructions for an AI model that no human reviewer would see.

What this does NOT give you: a process with a closed-down network and filesystem is a speed bump, not a
security boundary. A real parser exploit can undo in-process restrictions (the socket block and rlimits
here). For hostile input, run the service in a container with no network, a read-only filesystem, a
non-root user and memory/CPU limits, and keep PyMuPDF patched. Parsing in a child process added about 0.2
seconds per PDF in a local measurement; set INGEST_PDF_SANDBOX=0 to parse in-process (tests, trusted files).
"""
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz

from .models import Block, Finding, IngestionError, ParsedDocument
from .pdf import parse_pdf

logger = logging.getLogger("aimate.ingestion")

WORKER = Path(__file__).with_name("worker.py")
MAX_PDF_PAGES = int(os.getenv("INGEST_PDF_MAX_PAGES", "500"))
MAX_PDF_OBJECTS = int(os.getenv("INGEST_PDF_MAX_OBJECTS", "200000"))
PARSE_TIMEOUT_SECONDS = float(os.getenv("INGEST_PDF_TIMEOUT", "60"))
MAX_MEMORY_MB = int(os.getenv("INGEST_PDF_MAX_MEMORY_MB", "2048"))
MAX_RESULT_BYTES = 40 * 1024 * 1024
HEADER_WINDOW = 1024  # a PDF header must appear within the first KB

_NAME_RE = re.compile(r"/[^\x00-\x20/<>\[\]()%]+")
_ESCAPE_RE = re.compile(r"#([0-9A-Fa-f]{2})")

# name -> (severity, what it means). "high" is rejected by default.
RISKY_NAMES = {
    "/JS": ("high", "JavaScript"),
    "/JavaScript": ("high", "JavaScript"),
    "/Launch": ("high", "a launch action that can run a program"),
    "/EmbeddedFile": ("high", "an embedded file"),
    "/RichMedia": ("high", "embedded rich media"),
    "/XFA": ("high", "an XFA form"),
    "/SubmitForm": ("high", "a form submission to an external address"),
    "/ImportData": ("high", "a form data import"),
    "/GoToE": ("high", "a jump into an embedded file"),
    "/GoToR": ("medium", "a link to another file"),
    "/AA": ("medium", "automatic actions"),
    "/OpenAction": ("info", "an action on open"),
    "/URI": ("info", "an external link (not followed)"),
}


def _policy() -> str:
    return "warn" if os.getenv("INGEST_PDF_POLICY", "reject").lower() == "warn" else "reject"


def check_pdf(data: bytes) -> list:
    """Raise IngestionError for a file that is not a PDF or is over a limit; otherwise return the risky
    features found (a list of Finding, possibly empty)."""
    if b"%PDF-" not in data[:HEADER_WINDOW]:
        raise IngestionError("The file is not a PDF (no %PDF header).")
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise IngestionError(f"Not a readable PDF: {exc}") from exc
    with pdf:
        if pdf.needs_pass:
            raise IngestionError("The PDF is password-protected.")
        if pdf.page_count > MAX_PDF_PAGES:
            raise IngestionError(f"The PDF has {pdf.page_count} pages; the limit is {MAX_PDF_PAGES}.")
        if pdf.xref_length() > MAX_PDF_OBJECTS:
            raise IngestionError("The PDF has too many internal objects to read safely.")
        counts = {}
        for xref in range(1, pdf.xref_length()):
            try:
                text = pdf.xref_object(xref)
            except Exception:
                continue  # an unreadable object is ignored here; the parser will meet it too
            for token in _NAME_RE.findall(text):
                name = _ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), token)
                if name in RISKY_NAMES:
                    counts[name] = counts.get(name, 0) + 1
    return [Finding(name, *RISKY_NAMES[name], count) for name, count in sorted(counts.items())]


def parse_pdf_checked(data: bytes, source: str = "document.pdf") -> ParsedDocument:
    """check_pdf, apply the policy, then parse in this process. This is also what the sandbox child runs."""
    findings = check_pdf(data)
    high = [f for f in findings if f.severity == "high"]
    if high and _policy() == "reject":
        raise IngestionError("The PDF was rejected because it contains " + ", ".join(
            sorted({f.meaning for f in high})) + ". Set INGEST_PDF_POLICY=warn to accept such files.")
    doc = parse_pdf(data, source)
    for finding in findings:
        if finding.severity == "high":
            doc.warnings.append(f"Contains {finding.meaning} ({finding.count}); accepted because the policy is 'warn'.")
        elif finding.name == "/URI":
            doc.warnings.append(f"Contains {finding.count} external link(s); none were followed.")
        elif finding.severity == "medium":
            doc.warnings.append(f"Contains {finding.meaning} ({finding.count}); not executed.")
    return doc


# --- the sandboxed path -----------------------------------------------------------------------

# Settings the child may inherit from the service, and nothing else. Set only if the service set them.
PASS_THROUGH = ("INGEST_DOCX_POLICY", "INGEST_DOCX_MAX_ENTRIES", "INGEST_DOCX_MAX_UNCOMPRESSED_MB", "INGEST_OCR_LANG")


def child_environment() -> dict:
    """Everything the parser process is allowed to know: nothing from the service's environment except
    the ingestion settings above, so API keys, tokens and paths cannot leak into or out of it."""
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "INGEST_PDF_MAX_MEMORY_MB": str(MAX_MEMORY_MB),
                   "INGEST_PDF_MAX_PAGES": str(MAX_PDF_PAGES), "INGEST_PDF_MAX_OBJECTS": str(MAX_PDF_OBJECTS),
                   "INGEST_PDF_POLICY": _policy(), "INGEST_PDF_TIMEOUT": str(PARSE_TIMEOUT_SECONDS)}
    environment.update({name: os.environ[name] for name in PASS_THROUGH if name in os.environ})
    return environment


def parse_pdf_sandboxed(data: bytes, source: str = "document.pdf", timeout: float = None) -> ParsedDocument:
    return run_in_sandbox("pdf", data, source, timeout)


def parse_docx_sandboxed(data: bytes, source: str = "document.docx", timeout: float = None) -> ParsedDocument:
    return run_in_sandbox("docx", data, source, timeout)


def run_in_sandbox(kind: str, data: bytes, source: str, timeout: float = None) -> ParsedDocument:
    """Parse `data` ("pdf" or "docx") in a child process; see the module docstring for what that buys."""
    timeout = PARSE_TIMEOUT_SECONDS if timeout is None else timeout
    label = kind.upper()
    with tempfile.TemporaryDirectory(prefix="ingest-") as workdir:
        try:
            result = subprocess.run(
                [sys.executable, "-I", str(WORKER), kind, source], input=data, capture_output=True,
                timeout=timeout, env=child_environment(), cwd=workdir)
        except subprocess.TimeoutExpired as exc:
            raise IngestionError(f"Reading the {label} timed out after {timeout:g} seconds.") from exc
    if result.returncode != 0:
        logger.warning("%s parser process failed (exit %s): %s", label, result.returncode,
                       result.stderr[-300:].decode("utf-8", "replace"))
        raise IngestionError(f"The {label} could not be processed safely (the parser process failed).")
    if len(result.stdout) > MAX_RESULT_BYTES:
        raise IngestionError(f"The {label} produced too much text to process safely.")
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise IngestionError(f"The {label} could not be processed safely (unreadable parser output).") from exc
    if not payload.get("ok"):
        raise IngestionError(str(payload.get("error") or f"The {label} could not be read."))
    return _document_from_json(payload["doc"], label)


def _document_from_json(data: dict, label: str = "document") -> ParsedDocument:
    try:
        return ParsedDocument(
            source=str(data["source"]), pages=data.get("pages"), title=data.get("title"),
            warnings=[str(w) for w in data.get("warnings", [])],
            blocks=[Block(**{k: b.get(k) for k in ("kind", "text", "level", "page", "page_end", "rows")
                             if k in b}) for b in data["blocks"]])
    except (KeyError, TypeError) as exc:
        raise IngestionError(f"The {label} could not be processed safely (malformed parser output).") from exc
