"""The upload and URL routes, end to end through FastAPI, with a mock embedding model and no network.

    cd backend && .venv/bin/python -m unittest tests.test_app_ingestion -v
"""
import io
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import fitz
from fastapi.testclient import TestClient
from llama_index.core import MockEmbedding

import app as app_module
# Parse in-process in these suites; tests/test_secure_pdf.py and tests/test_secure_docx.py cover the sandbox.
os.environ["INGEST_PDF_SANDBOX"] = "0"
os.environ["INGEST_DOCX_SANDBOX"] = "0"

from ingestion import IngestionError
from tests.test_ingestion import LOREM, STYLES, box, make_docx, make_pdf, para

EMBED = MockEmbedding(embed_dim=8)


def pdf_bytes(marker="ALPHA"):
    return make_pdf(lambda p: box(p, 50, 60, 545, 200, f"{marker} " + LOREM))


def scanned_pdf_bytes():
    def page(p):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 200), False)
        pix.clear_with(210)
        p.insert_image(fitz.Rect(50, 50, 545, 400), pixmap=pix)
    return make_pdf(page)


class AppIngestionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        for patcher in (patch.object(app_module, "FILES_DIR", self.folder),
                        patch.object(app_module, "get_embed_model", return_value=EMBED)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app_module.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def upload(self, name, data):
        return self.client.post("/api/upload", files={"file": (name, data)}, data={"filename": name})

    def stored_sources(self):
        index = app_module.app.state.vector_index
        return sorted(node.metadata["source"] for node in index.docstore.docs.values())

    def test_a_pdf_upload_reports_chunks_and_is_saved_and_indexed(self):
        response = self.upload("report.pdf", pdf_bytes())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["chunks"], body["warnings"]), (1, []))
        self.assertTrue((self.folder / "report.pdf").exists())
        self.assertEqual(self.stored_sources(), ["report.pdf"])

    def test_docx_and_html_and_markdown_uploads_work(self):
        docx = make_docx(para("Safety", "Heading1") + para("Take one tablet daily."))
        self.assertEqual(self.upload("leaflet.docx", docx).json()["chunks"], 1)
        self.assertEqual(self.upload("page.html", b"<main><h1>Guide</h1><p>Body text.</p></main>").json()["chunks"], 1)
        self.assertEqual(self.upload("notes.md", b"# Notes\n\nSome text.").json()["chunks"], 1)
        self.assertEqual(self.stored_sources(), ["leaflet.docx", "notes.md", "page.html"])

    def test_reuploading_a_file_replaces_its_chunks_instead_of_duplicating(self):
        self.upload("report.pdf", pdf_bytes("FIRST"))
        self.upload("report.pdf", pdf_bytes("SECOND"))

        self.assertEqual(self.stored_sources(), ["report.pdf"])
        texts = [n.text for n in app_module.app.state.vector_index.docstore.docs.values()]
        self.assertTrue(texts[0].startswith("SECOND"))

    def test_a_corrupt_upload_is_rejected_with_422_and_never_stored(self):
        response = self.upload("broken.pdf", b"not really a pdf")

        self.assertEqual(response.status_code, 422)
        self.assertIn("not a PDF", response.json()["detail"])
        self.assertFalse((self.folder / "broken.pdf").exists())
        self.assertEqual(self.stored_sources(), [])

    def test_risky_pdfs_and_docx_files_are_rejected_with_the_reason_and_never_stored(self):
        from tests.test_secure_docx import macro_docx
        from tests.test_secure_pdf import pdf_with, with_javascript

        for name, data, reason in (("script.pdf", pdf_with(with_javascript), "JavaScript"),
                                   ("macro.docx", macro_docx(), "VBA macro project")):
            with self.subTest(name=name):
                response = self.upload(name, data)
                self.assertEqual(response.status_code, 422)
                self.assertIn(reason, response.json()["detail"])
                self.assertFalse((self.folder / name).exists())
        self.assertEqual(self.stored_sources(), [])

    def test_a_scanned_pdf_with_no_text_is_rejected_and_says_why(self):
        response = self.upload("scan.pdf", scanned_pdf_bytes())

        self.assertEqual(response.status_code, 422)
        self.assertIn("No text could be extracted", response.json()["detail"])
        self.assertIn("scanned", response.json()["detail"])
        self.assertFalse((self.folder / "scan.pdf").exists())

    def test_unsupported_types_and_oversized_files_are_rejected(self):
        self.assertEqual(self.upload("sheet.xlsx", b"x").status_code, 400)
        with patch.object(app_module, "MAX_UPLOAD_BYTES", 10):
            self.assertEqual(self.upload("big.txt", b"x" * 11).status_code, 413)

    def test_path_traversal_in_the_filename_is_flattened(self):
        self.client.post("/api/upload", files={"file": ("x.txt", b"Some plain text here.")},
                         data={"filename": "../../etc/evil.txt"})
        self.assertTrue((self.folder / "evil.txt").exists())
        self.assertFalse((self.folder.parent / "evil.txt").exists())

    def test_startup_indexes_stored_files_and_skips_ones_it_cannot_read(self):
        (self.folder / "good.md").write_bytes(b"# Good\n\nReadable text.")
        (self.folder / "bad.docx").write_bytes(b"not a zip")
        (self.folder / ".DS_Store").write_bytes(b"junk")
        with TestClient(app_module.app):
            self.assertEqual(self.stored_sources(), ["good.md"])
            self.assertEqual(list(app_module.app.state.node_ids), ["good.md"])

    def test_the_web_route_ingests_a_fetched_page(self):
        html = b"<nav><a href='/'>NAV</a></nav><main><h1>Dosage</h1><p>Take one tablet daily.</p></main>"
        with patch.object(app_module, "fetch_url", return_value=(html, "html")), \
                patch.object(app_module, "query_index", side_effect=self.echo_index):
            response = self.client.post("/api/query_web", data={"url": "https://example.com/p", "query": "dose?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], "example.com/p::Dosage::Take one tablet daily.")

    @staticmethod
    async def echo_index(index, prompt):
        (node,) = index.docstore.docs.values()
        return f"{node.metadata['source'].split('//')[1]}::{node.metadata['section']}::{node.text}"

    def test_the_web_route_turns_ingestion_errors_into_422(self):
        with patch.object(app_module, "fetch_url", side_effect=IngestionError("That URL points to a private address.")):
            response = self.client.post("/api/query_web", data={"url": "http://10.0.0.1/", "query": "x"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("private address", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
