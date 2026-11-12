"""Reading an untrusted DOCX defensively. The same three layers as secure.py, adapted to Office files.

A .docx is a zip of XML parts, so the risks are different from a PDF's:

- Macros and objects: a VBA project, embedded OLE objects or packages, and ActiveX controls can run code
  when a person opens the file in Word. Extracting text never runs them, but a document that carries them
  is not one to accept silently.
- Fields: DDE/DDEAUTO fields can launch a command, and MACROBUTTON can trigger a macro. INCLUDETEXT,
  INCLUDEPICTURE and LINK pull in other files or URLs.
- External relationships: a remote template (the "template injection" technique), linked objects, frames
  and images point outside the file. A remote image or template makes the reader fetch a URL when opened,
  which tells the sender the file was read and, over a Windows file share, can leak credentials.
- Zip abuse: a decompression bomb, an absurd number of entries, entry names that try to escape a folder,
  and headers that lie about sizes. Reads are bounded by the bytes actually decompressed, not the claim.
- Hidden text: runs formatted as hidden, tiny, or white on a plain background are removed and reported
  (see docx.py), because they are a way to plant instructions for an AI model.

By default a file with any high-risk feature is rejected; INGEST_DOCX_POLICY=warn accepts it with a
warning. Nothing is executed, fetched or followed either way. As with PDFs, parse in a child process
(parse_docx_sandboxed) and, for hostile input, put the whole service in a container with no network.
"""
import io
import os
import re
import zipfile

from lxml import etree

from .docx import W, _w, parse_docx, read_part, safe_xml_parser
from .models import Finding, IngestionError, ParsedDocument

MAX_ENTRIES = int(os.getenv("INGEST_DOCX_MAX_ENTRIES", "2000"))
MAX_UNCOMPRESSED_BYTES = int(os.getenv("INGEST_DOCX_MAX_UNCOMPRESSED_MB", "200")) * 1024 * 1024
MAX_RATIO = 200  # an entry that expands more than this is a decompression bomb ...
RATIO_MIN_BYTES = 5 * 1024 * 1024  # ... once it is at least this large
SMALL_PART_BYTES = 5 * 1024 * 1024  # rels and content-type parts are tiny; anything bigger is refused

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

# Parts whose presence is a finding: (matcher, name, severity, meaning)
PART_RULES = [
    (lambda n: n in {"word/vbaproject.bin", "word/vbadata.xml"}, "vbaProject", "high", "a VBA macro project"),
    (lambda n: n.startswith("word/embeddings/"), "embeddings", "high", "embedded objects (OLE objects or packages)"),
    (lambda n: n.startswith("word/activex/"), "activeX", "high", "ActiveX controls"),
    (lambda n: n.startswith("customui/") or "/customui/" in n, "customUI", "medium", "a custom ribbon that can bind macros"),
]

# External relationship types -> (severity, meaning). Anything else external is "medium".
EXTERNAL_RULES = {
    "attachedTemplate": ("high", "a remote template (template injection)"),
    "oleObject": ("high", "an externally linked object"),
    "frame": ("high", "an external frame"),
    "subDocument": ("high", "an external sub-document"),
    "image": ("medium", "an externally hosted image (reveals when the file is opened)"),
    "hyperlink": ("info", "an external link (not followed)"),
}

# Field instructions that matter, by their first word.
FIELD_RULES = {
    "DDEAUTO": ("high", "a DDE field that can run a command"),
    "DDE": ("high", "a DDE field that can run a command"),
    "MACROBUTTON": ("high", "a macro button field"),
    "INCLUDETEXT": ("medium", "a field that pulls in another file or URL"),
    "INCLUDEPICTURE": ("medium", "a field that pulls in a picture from a file or URL"),
    "LINK": ("medium", "a linked-object field"),
    "IMPORT": ("medium", "a field that imports a file"),
}


def _policy() -> str:
    return "warn" if os.getenv("INGEST_DOCX_POLICY", "reject").lower() == "warn" else "reject"


def check_docx(data: bytes) -> list:
    """Raise IngestionError for a file that is not a DOCX or is over a limit; otherwise return the risky
    features found (a list of Finding, possibly empty)."""
    if data[:8] == OLE_MAGIC:
        raise IngestionError("This is a legacy Word file or a password-protected Office file, not a .docx.")
    if data[:2] != b"PK":
        raise IngestionError("The file is not a .docx (it is not a zip archive).")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise IngestionError("The file is not a readable .docx (damaged zip archive).") from exc

    found = {}

    def add(name, severity, meaning):
        count = found[name][3] + 1 if name in found else 1
        found[name] = (name, severity, meaning, count)

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise IngestionError(f"The document has {len(infos)} internal files; the limit is {MAX_ENTRIES}.")
        if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
            raise IngestionError("The document would expand to too much data to read safely.")
        for info in infos:
            if info.file_size >= RATIO_MIN_BYTES and info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                raise IngestionError("The document contains a decompression bomb.")
        names = {info.filename for info in infos}
        if "word/document.xml" not in names:
            raise IngestionError("Not a .docx file: word/document.xml is missing.")

        for filename in names:
            lowered = filename.lower()
            if filename.startswith("/") or "\\" in filename or ".." in filename.split("/") or re.match(r"^[a-z]:", lowered):
                add("unsafePath", "high", "file names that try to escape their folder")
            for matches, name, severity, meaning in PART_RULES:
                if matches(lowered):
                    add(name, severity, meaning)

        if "[Content_Types].xml" in names and b"macroEnabled" in read_part(archive, "[Content_Types].xml", SMALL_PART_BYTES):
            add("macroEnabled", "high", "a macro-enabled document type (.docm)")

        parser = safe_xml_parser()
        for filename in sorted(names):
            if filename.endswith(".rels"):
                _scan_relationships(archive, filename, parser, add)
            elif re.match(r"^word/[^/]+\.xml$", filename):
                try:
                    root = etree.fromstring(read_part(archive, filename), parser=parser)
                except etree.XMLSyntaxError:
                    continue  # the parser meets the same damage later; for document.xml it reports it
                for instruction in _field_instructions(root):
                    match = re.match(r"\s*([A-Za-z]+)", instruction)
                    keyword = match.group(1).upper() if match else ""
                    if keyword in FIELD_RULES:
                        add(f"field:{keyword}", *FIELD_RULES[keyword])
    return [Finding(*value) for _, value in sorted(found.items())]


def _scan_relationships(archive, filename, parser, add):
    try:
        root = etree.fromstring(read_part(archive, filename, SMALL_PART_BYTES), parser=parser)
    except etree.XMLSyntaxError:
        return
    for rel in root.iter(f"{{{RELS_NS}}}Relationship"):
        if (rel.get("TargetMode") or "").lower() != "external":
            continue
        kind = (rel.get("Type") or "").rsplit("/", 1)[-1]
        severity, meaning = EXTERNAL_RULES.get(kind, ("medium", "an external reference"))
        add(f"external:{kind or 'unknown'}", severity, meaning)


def _field_instructions(root):
    """The instruction text of every field, joined across the fragments Word splits it into, so
    "DD" + "EAUTO" cannot dodge the scan. Simple fields keep theirs in an attribute."""
    stack = []  # open fields: {"parts": [...], "done": bool}
    for element in root.iter():
        tag = element.tag
        if tag == _w("fldChar"):
            kind = element.get(_w("fldCharType"))
            if kind == "begin":
                stack.append({"parts": [], "done": False})
            elif kind == "separate" and stack and not stack[-1]["done"]:
                stack[-1]["done"] = True
                yield "".join(stack[-1]["parts"])
            elif kind == "end" and stack:
                field = stack.pop()
                if not field["done"]:
                    yield "".join(field["parts"])
        elif tag == _w("instrText") and stack and not stack[-1]["done"]:
            stack[-1]["parts"].append(element.text or "")
        elif tag == _w("fldSimple"):
            yield element.get(_w("instr")) or ""


def parse_docx_checked(data: bytes, source: str = "document.docx") -> ParsedDocument:
    """check_docx, apply the policy, then parse in this process. This is also what the sandbox child runs."""
    findings = check_docx(data)
    high = [f for f in findings if f.severity == "high"]
    if high and _policy() == "reject":
        raise IngestionError("The document was rejected because it contains " + ", ".join(
            sorted({f.meaning for f in high})) + ". Set INGEST_DOCX_POLICY=warn to accept such files.")
    doc = parse_docx(data, source)
    for finding in findings:
        if finding.severity == "high":
            doc.warnings.append(f"Contains {finding.meaning} ({finding.count}); accepted because the policy is 'warn'.")
        elif finding.name == "external:hyperlink":
            doc.warnings.append(f"Contains {finding.count} external link(s); none were followed.")
        elif finding.severity == "medium":
            doc.warnings.append(f"Contains {finding.meaning} ({finding.count}); not fetched or run.")
    return doc
