"""Child process that parses one PDF or DOCX. Started by `secure.run_in_sandbox`; not meant to be run by hand.

    worker.py <pdf|docx> <source name>      the file is read from stdin

Writes one JSON document to stdout: {"ok": true, "doc": ...} or {"ok": false, "error": "..."}.
Anything a library prints goes to stderr so it cannot corrupt the result.
"""
import json
import os
import socket
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # -I removes the working directory

MAX_TEXT_CHARS = 5_000_000


def _limit_resources(timeout):
    """Caps that stop a runaway parse. Best effort: not every limit is enforced on every OS (macOS ignores
    the address-space limit, for example), which is why the parent also enforces a wall-clock timeout."""
    try:
        import resource
    except ImportError:  # Windows
        return
    limits = [(resource.RLIMIT_CPU, int(timeout) + 5), (resource.RLIMIT_CORE, 0)]
    memory = int(os.getenv("INGEST_PDF_MAX_MEMORY_MB", "2048")) * 1024 * 1024
    limits.append((resource.RLIMIT_AS, memory))
    for which, value in limits:
        try:
            resource.setrlimit(which, (value, value))
        except (ValueError, OSError):
            pass


def _block_network():
    """Make every socket attempt fail. Defence in depth against a library phoning home; it is NOT a
    boundary against native-code exploits, which is what a container with no network is for."""
    def refuse(*args, **kwargs):
        raise OSError("network access is disabled in the document parser")

    socket.socket = refuse
    socket.create_connection = refuse
    socket.getaddrinfo = refuse


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "pdf"
    source = sys.argv[2] if len(sys.argv) > 2 else f"document.{kind}"
    data = sys.stdin.buffer.read()
    result_stream, sys.stdout = sys.stdout, sys.stderr

    from ingestion.models import IngestionError
    from ingestion.secure import parse_pdf_checked

    parsers = {"pdf": parse_pdf_checked}
    try:  # DOCX support is optional: a copy of this package without secure_docx.py still works for PDFs
        from ingestion.secure_docx import parse_docx_checked
        parsers["docx"] = parse_docx_checked
    except ImportError:
        pass
    _limit_resources(float(os.getenv("INGEST_PDF_TIMEOUT", "60")))
    _block_network()
    try:
        if kind not in parsers:
            raise IngestionError(f"Unsupported document type for the sandbox: {kind}")
        doc = parsers[kind](data, source)
        payload = {"ok": True, "doc": asdict(doc)}
        if sum(len(block.text) for block in doc.blocks) > MAX_TEXT_CHARS:
            payload = {"ok": False, "error": f"The {kind.upper()} contains too much text to process safely."}
    except IngestionError as exc:
        payload = {"ok": False, "error": str(exc)}
    except Exception:
        import traceback
        traceback.print_exc()
        payload = {"ok": False, "error": f"Unexpected failure while reading the {kind.upper()}."}
    json.dump(payload, result_stream)
    result_stream.flush()


if __name__ == "__main__":
    main()
