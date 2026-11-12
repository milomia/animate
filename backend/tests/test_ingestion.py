"""Regression tests for the ingestion package. Every fixture is generated here (PDFs with PyMuPDF, DOCX as
raw OOXML, HTML as strings), so the suite needs no sample files and runs offline.

    cd backend && .venv/bin/python -m unittest tests.test_ingestion -v
"""
import io
import os
import unittest
import zipfile

import fitz

# Parse in-process in these suites; tests/test_secure_pdf.py and tests/test_secure_docx.py cover the sandbox.
os.environ["INGEST_PDF_SANDBOX"] = "0"
os.environ["INGEST_DOCX_SANDBOX"] = "0"

from ingestion import IngestionError, chunk_document, ingest_bytes
from ingestion.chunker import Chunk
from ingestion.models import Block, ParsedDocument

# --- PDF fixtures -----------------------------------------------------------------------------

LOREM = ("The study enrolled adults across several centres and followed each participant for twelve months. "
         "Outcomes were recorded at every visit and reviewed by an independent committee. ")


def make_pdf(*page_painters) -> bytes:
    pdf = fitz.open()
    for paint in page_painters:
        page = pdf.new_page(width=595, height=842)
        paint(page)
    data = pdf.tobytes()
    pdf.close()
    return data


def box(page, x0, y0, x1, y1, text, size=11, font="helv"):
    left = page.insert_textbox(fitz.Rect(x0, y0, x1, y1), text, fontsize=size, fontname=font)
    assert left >= 0, f"text did not fit in the box: {text[:40]!r}"


def blocks_text(doc):
    return [b.text for b in doc.blocks]


def full_text(doc):
    return "\n".join(blocks_text(doc))


class PdfReadingOrderTests(unittest.TestCase):
    def test_two_columns_are_read_down_each_column_under_a_full_width_title(self):
        def page(p):
            box(p, 50, 50, 545, 100, "Annual Review", size=22, font="hebo")
            box(p, 50, 120, 280, 230, "LEFT-ONE " + LOREM)
            box(p, 50, 250, 280, 360, "LEFT-TWO " + LOREM)
            box(p, 315, 120, 545, 230, "RIGHT-ONE " + LOREM)
            box(p, 315, 250, 545, 360, "RIGHT-TWO " + LOREM)

        doc = ingest_bytes("two-column.pdf", make_pdf(page))

        order = [next(i for i, t in enumerate(blocks_text(doc)) if marker in t)
                 for marker in ("Annual Review", "LEFT-ONE", "LEFT-TWO", "RIGHT-ONE", "RIGHT-TWO")]
        self.assertEqual(order, sorted(order), blocks_text(doc))
        self.assertEqual((doc.blocks[0].kind, doc.blocks[0].level), ("heading", 1))

    def test_a_single_column_page_keeps_plain_top_to_bottom_order(self):
        def page(p):
            for i, y in enumerate((60, 180, 300), start=1):
                box(p, 50, y, 545, y + 100, f"PARA-{i} " + LOREM)

        doc = ingest_bytes("single.pdf", make_pdf(page))
        self.assertEqual([t.split()[0] for t in blocks_text(doc)], ["PARA-1", "PARA-2", "PARA-3"])


class PdfStructureTests(unittest.TestCase):
    def test_font_sizes_become_heading_levels_and_body_stays_body(self):
        def page(p):
            box(p, 50, 50, 545, 100, "Clinical Report", size=24, font="hebo")
            box(p, 50, 110, 545, 145, "Methods", size=16, font="hebo")
            box(p, 50, 150, 545, 250, LOREM * 2)
            box(p, 50, 270, 545, 305, "Results", size=16, font="hebo")
            box(p, 50, 310, 545, 410, LOREM * 2)

        doc = ingest_bytes("headings.pdf", make_pdf(page))

        self.assertEqual([(b.kind, b.level, b.text) for b in doc.blocks if b.kind == "heading"],
                         [("heading", 1, "Clinical Report"), ("heading", 2, "Methods"), ("heading", 2, "Results")])
        self.assertEqual(sum(b.kind == "paragraph" for b in doc.blocks), 2)
        self.assertEqual(doc.title, "Clinical Report")

    def test_bullets_become_one_list_block(self):
        def page(p):
            box(p, 50, 60, 545, 130, LOREM)
            for i, item in enumerate(("first item", "second item", "third item")):
                p.insert_text((60, 160 + 16 * i), f"• {item}", fontsize=11)

        doc = ingest_bytes("list.pdf", make_pdf(page))

        lists = [b for b in doc.blocks if b.kind == "list"]
        self.assertEqual(len(lists), 1)
        self.assertEqual(lists[0].text.split("\n"), ["- first item", "- second item", "- third item"])

    def test_wrapped_hyphenated_words_are_rejoined(self):
        def page(p):
            p.insert_text((50, 100), "The committee reviewed the infor-", fontsize=11)
            p.insert_text((50, 114), "mation carefully before voting.", fontsize=11)

        doc = ingest_bytes("hyphen.pdf", make_pdf(page))
        self.assertEqual(blocks_text(doc), ["The committee reviewed the information carefully before voting."])

    def test_a_paragraph_split_across_pages_is_stitched_and_spans_both_pages(self):
        def first(p):
            p.insert_text((50, 790), "The board reviewed the proposal in detail and then", fontsize=11)

        def second(p):
            p.insert_text((50, 60), "agreed to proceed with the next phase of work.", fontsize=11)

        doc = ingest_bytes("split.pdf", make_pdf(first, second))

        self.assertEqual(blocks_text(doc),
                         ["The board reviewed the proposal in detail and then agreed to proceed with the next phase of work."])
        self.assertEqual((doc.blocks[0].page, doc.blocks[0].page_end), (1, 2))

    def test_ligatures_and_invisible_characters_are_cleaned(self):
        # Tested on the cleaner directly: the built-in PDF fonts used for fixtures cannot encode ligature
        # glyphs, but real PDFs do, and every extracted line goes through clean_text.
        from ingestion.textutil import clean_text
        self.assertEqual(clean_text("The eﬃcient oﬃce ​staff\xad ﬁled the  form."),
                         "The efficient office staff filed the form.")


class PdfHeaderFooterTests(unittest.TestCase):
    def test_repeated_headers_footers_and_page_numbers_are_removed_but_body_text_is_kept(self):
        def make_page(n):
            def page(p):
                box(p, 50, 25, 545, 45, "ACME Corp Confidential", size=9)
                box(p, 50, 80, 545, 200, f"BODY-{n} {LOREM}")
                box(p, 50, 800, 545, 825, f"Page {n} of 4", size=9)
            return page

        doc = ingest_bytes("running.pdf", make_pdf(*(make_page(n) for n in range(1, 5))))

        text = full_text(doc)
        self.assertNotIn("ACME", text)
        self.assertNotRegex(text, r"Page \d of 4")
        for n in range(1, 5):
            self.assertIn(f"BODY-{n}", text)

    def test_a_bare_page_number_is_dropped_even_on_a_one_page_document(self):
        def page(p):
            box(p, 50, 80, 545, 200, "ONLY-BODY " + LOREM)
            box(p, 280, 810, 320, 830, "7", size=9)

        doc = ingest_bytes("one-page.pdf", make_pdf(page))
        self.assertEqual(len(doc.blocks), 1)
        self.assertTrue(doc.blocks[0].text.startswith("ONLY-BODY"))


class PdfTableTests(unittest.TestCase):
    ROWS = [["Region", "Q1", "Q2"], ["North", "10", "12"], ["South", "7", "9"], ["East", "15", "18"]]

    def table_page(self, p):
        box(p, 50, 60, 545, 120, "INTRO " + LOREM)
        x0, y0, cw, rh = 60, 150, 140, 30
        for r, row in enumerate(self.ROWS):
            for c, value in enumerate(row):
                rect = fitz.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
                p.draw_rect(rect, width=1)
                box(p, rect.x0 + 6, rect.y0 + 7, rect.x1 - 4, rect.y1 - 2, value)
        box(p, 50, 320, 545, 380, "OUTRO " + LOREM)

    def test_a_ruled_table_keeps_every_row_and_cell_together(self):
        doc = ingest_bytes("table.pdf", make_pdf(self.table_page))

        tables = [b for b in doc.blocks if b.kind == "table"]
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].rows, self.ROWS)
        self.assertIn("| North | 10 | 12 |", tables[0].text)

    def test_table_cells_do_not_also_appear_as_loose_paragraphs(self):
        doc = ingest_bytes("table.pdf", make_pdf(self.table_page))
        prose = " ".join(b.text for b in doc.blocks if b.kind != "table")
        self.assertNotIn("North", prose)
        self.assertEqual([b.kind for b in doc.blocks], ["paragraph", "table", "paragraph"])


class PdfScannedAndBrokenTests(unittest.TestCase):
    def scanned_page(self, p):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 200), False)
        pix.clear_with(210)
        p.insert_image(fitz.Rect(50, 50, 545, 400), pixmap=pix)

    def test_a_scanned_page_is_reported_not_silently_dropped(self):
        def text_page(p):
            box(p, 50, 80, 545, 200, "TEXT-PAGE " + LOREM)

        doc = ingest_bytes("mixed.pdf", make_pdf(text_page, self.scanned_page))

        self.assertTrue(doc.blocks[0].text.startswith("TEXT-PAGE"))
        self.assertEqual(len(doc.warnings), 1)
        self.assertIn("Pages 2", doc.warnings[0])

    def test_a_blank_page_is_not_mistaken_for_a_scan(self):
        doc = ingest_bytes("blank.pdf", make_pdf(lambda p: box(p, 50, 80, 545, 200, LOREM), lambda p: None))
        self.assertEqual(doc.warnings, [])

    def test_garbage_and_encrypted_pdfs_raise_a_clear_error(self):
        with self.assertRaisesRegex(IngestionError, "not a PDF"):
            ingest_bytes("bad.pdf", b"this is not a pdf")
        with self.assertRaisesRegex(IngestionError, "Not a readable PDF"):
            ingest_bytes("truncated.pdf", b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog")
        encrypted = fitz.open()
        encrypted.new_page().insert_text((50, 80), "secret")
        data = encrypted.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
        with self.assertRaisesRegex(IngestionError, "password"):
            ingest_bytes("locked.pdf", data)


# --- DOCX fixtures ----------------------------------------------------------------------------

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')

STYLES = f"""<w:styles {NS}>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>
  <w:style w:type="paragraph" w:styleId="Chapter"><w:name w:val="Chapter"/><w:basedOn w:val="Heading1"/></w:style>
  <w:style w:type="paragraph" w:styleId="Deep"><w:name w:val="Deep"/><w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/></w:style>
</w:styles>"""


def run(text):
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def para(text="", style=None, num_level=None, raw=None):
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if num_level is not None:
        ppr += f'<w:numPr><w:ilvl w:val="{num_level}"/><w:numId w:val="1"/></w:numPr>'
    return f"<w:p><w:pPr>{ppr}</w:pPr>{raw if raw is not None else run(text)}</w:p>"


def cell(text, span=None, vmerge=None):
    props = (f'<w:gridSpan w:val="{span}"/>' if span else "")
    if vmerge is not None:
        props += f'<w:vMerge w:val="{vmerge}"/>' if vmerge else "<w:vMerge/>"
    return f"<w:tc><w:tcPr>{props}</w:tcPr>{para(text)}</w:tc>"


def table(*rows):
    return "<w:tbl>" + "".join("<w:tr>" + "".join(cells) + "</w:tr>" for cells in rows) + "</w:tbl>"


def make_docx(body, styles=STYLES, extra=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("word/document.xml", f"<w:document {NS}><w:body>{body}</w:body></w:document>")
        if styles:
            z.writestr("word/styles.xml", styles)
        for name, content in (extra or {}).items():
            z.writestr(name, content)
    return buffer.getvalue()


class DocxTests(unittest.TestCase):
    def test_styles_become_heading_levels_including_inherited_and_outline_levels(self):
        data = make_docx(
            para("Handbook", "Title") + para("Safety", "Heading1") + para("Dosing", "Heading2")
            + para("Routes", "Deep") + para("Chapter Two", "Chapter") + para("Plain text."))

        doc = ingest_bytes("styles.docx", data)

        self.assertEqual([(b.kind, b.level, b.text) for b in doc.blocks],
                         [("heading", 0, "Handbook"), ("heading", 1, "Safety"), ("heading", 2, "Dosing"),
                          ("heading", 3, "Routes"), ("heading", 1, "Chapter Two"), ("paragraph", 0, "Plain text.")])
        self.assertEqual(doc.title, "Handbook")

    def test_lists_nest_and_text_inside_runs_hyperlinks_and_tabs_is_read(self):
        link = f'<w:hyperlink r:id="rId1">{run("the site")}</w:hyperlink>'
        data = make_docx(
            para(raw=run("See ") + link + "<w:r><w:tab/></w:r>" + run("for details."))
            + para("alpha", num_level=0) + para("beta", num_level=1) + para("gamma", "ListBullet"))

        doc = ingest_bytes("lists.docx", data)

        self.assertEqual(doc.blocks[0].text, "See the site for details.")
        self.assertEqual(doc.blocks[1].kind, "list")
        self.assertEqual(doc.blocks[1].text.split("\n"), ["- alpha", "  - beta", "- gamma"])

    def test_merged_cells_are_repeated_so_every_row_stands_alone(self):
        data = make_docx(table(
            [cell("Region", vmerge="restart"), cell("Sales", span=2)],
            [cell("", vmerge=""), cell("Q1"), cell("Q2")],
            [cell("North"), cell("10"), cell("12")],
        ))

        doc = ingest_bytes("table.docx", data)

        self.assertEqual(len(doc.blocks), 1)
        self.assertEqual(doc.blocks[0].rows,
                         [["Region", "Sales", "Sales"], ["Region", "Q1", "Q2"], ["North", "10", "12"]])

    def test_a_one_column_layout_table_is_read_as_paragraphs_not_a_table(self):
        doc = ingest_bytes("layout.docx", make_docx(table([cell("Boxed note about the schedule.")])))
        self.assertEqual([(b.kind, b.text) for b in doc.blocks], [("paragraph", "Boxed note about the schedule.")])

    def test_tracked_deletions_headers_and_duplicated_alternate_content_are_excluded(self):
        deleted = '<w:del w:id="1" w:author="x"><w:r><w:delText>REMOVED</w:delText></w:r></w:del>'
        alternate = ('<mc:AlternateContent><mc:Choice>' + run("Shown once") + '</mc:Choice>'
                     '<mc:Fallback>' + run("Shown once") + '</mc:Fallback></mc:AlternateContent>')
        data = make_docx(para(raw=run("Kept ") + deleted + run("text.")) + para(raw=alternate),
                         extra={"word/header1.xml": f"<w:hdr {NS}>{para('RUNNING HEADER')}</w:hdr>"})

        doc = ingest_bytes("noise.docx", data)

        self.assertEqual(blocks_text(doc), ["Kept text.", "Shown once"])

    def test_text_boxes_are_skipped_with_a_warning(self):
        box_xml = f"<w:r><w:drawing><w:txbxContent>{para('INSIDE BOX')}</w:txbxContent></w:drawing></w:r>"
        doc = ingest_bytes("box.docx", make_docx(para("Body.") + para(raw=box_xml)))

        self.assertNotIn("INSIDE BOX", full_text(doc))
        self.assertTrue(any("text boxes" in w for w in doc.warnings))

    def test_page_breaks_give_each_block_its_page(self):
        page_break = para(raw='<w:r><w:br w:type="page"/></w:r>')
        data = make_docx(para("On page one.") + page_break + para("On page two.")
                         + para(raw=run("Runs over ") + '<w:r><w:lastRenderedPageBreak/></w:r>' + run("to page three.")))

        doc = ingest_bytes("pages.docx", data)

        self.assertEqual([(b.page, b.page_end) for b in doc.blocks], [(1, None), (2, None), (2, 3)])
        self.assertEqual(doc.pages, 3)

    def test_damaged_files_raise_a_clear_error(self):
        with self.assertRaisesRegex(IngestionError, "not a zip"):
            ingest_bytes("old.docx", b"\xd0\xcf\x11\xe0 legacy word binary")
        empty = io.BytesIO()
        with zipfile.ZipFile(empty, "w") as z:
            z.writestr("hello.txt", "x")
        with self.assertRaisesRegex(IngestionError, "word/document.xml"):
            ingest_bytes("empty.docx", empty.getvalue())

    def test_external_entities_are_not_resolved(self):
        evil = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/hostname">]>'
                f"<w:document {NS}><w:body>{para('&x;')}</w:body></w:document>")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("word/document.xml", evil)
        doc = ingest_bytes("xxe.docx", buffer.getvalue())
        self.assertEqual(blocks_text(doc), [])


# --- HTML -------------------------------------------------------------------------------------

PAGE = """<!doctype html><html><head><title>Product guide</title><script>var track = "SCRIPT-TEXT";</script></head>
<body class="layout has-sidebar">
<header class="site-header"><a href="/">Logo</a><a href="/a">Home</a><a href="/b">Docs</a><a href="/c">Blog</a></header>
<nav><a href="/1">NAV-ONE</a><a href="/2">NAV-TWO</a></nav>
<div class="cookie-banner">We use COOKIES to improve your visit. <button>Accept</button></div>
<div class="quick-links"><a href="/x">One</a><a href="/y">Two</a><a href="/z">Three</a><a href="/w">Four</a><a href="/v">Five</a></div>
<aside class="sidebar">SIDEBAR-TEXT related posts</aside>
<main>
  <h1>Installing the <b>agent</b></h1>
  <p>Download the package and run the <code>setup</code> tool. <br>It takes a minute.</p>
  <h2>Requirements</h2>
  <ul><li>Linux or macOS<ul><li>64-bit only</li></ul></li><li>2 GB of memory</li></ul>
  <ol><li>Download</li><li>Install</li></ol>
  <table><thead><tr><th>Platform</th><th colspan="2">Support</th></tr></thead>
         <tbody><tr><td rowspan="2">Linux</td><td>x86</td><td>yes</td></tr><tr><td>arm</td><td>beta</td></tr></tbody></table>
  <div hidden>HIDDEN-TEXT</div><div style="display: none">ALSO-HIDDEN</div>
  <!-- COMMENT-TEXT -->
  <dl><dt>Port</dt><dd>8080 by default</dd></dl>
</main>
<footer>FOOTER-TEXT &copy; Example Ltd</footer>
</body></html>"""


class HtmlTests(unittest.TestCase):
    def setUp(self):
        self.doc = ingest_bytes("guide.html", PAGE.encode())
        self.text = full_text(self.doc)

    def test_page_furniture_is_removed(self):
        for noise in ("NAV-ONE", "COOKIES", "SIDEBAR-TEXT", "FOOTER-TEXT", "SCRIPT-TEXT", "HIDDEN-TEXT",
                      "ALSO-HIDDEN", "COMMENT-TEXT", "Logo", "Accept", "One Two Three"):
            self.assertNotIn(noise, self.text, noise)

    def test_content_and_its_structure_survive(self):
        self.assertEqual([(b.level, b.text) for b in self.doc.blocks if b.kind == "heading"],
                         [(1, "Installing the agent"), (2, "Requirements")])
        self.assertIn("Download the package and run the setup tool. It takes a minute.", self.text)
        lists = [b.text for b in self.doc.blocks if b.kind == "list"]
        self.assertEqual(lists[0].split("\n"), ["- Linux or macOS", "  - 64-bit only", "- 2 GB of memory"])
        self.assertEqual(lists[1].split("\n"), ["1. Download", "2. Install"])
        self.assertIn("Port: 8080 by default", self.text)
        self.assertEqual(self.doc.title, "Product guide")

    def test_colspan_and_rowspan_are_repeated_into_every_covered_cell(self):
        (table_block,) = [b for b in self.doc.blocks if b.kind == "table"]
        self.assertEqual(table_block.rows, [["Platform", "Support", "Support"],
                                            ["Linux", "x86", "yes"], ["Linux", "arm", "beta"]])

    def test_a_body_class_mentioning_sidebar_cannot_delete_the_page(self):
        doc = ingest_bytes("plain.html", b'<body class="has-sidebar"><div class="page-with-sidebar">'
                                         b"<h1>Title</h1><p>Real content lives here.</p></div></body>")
        self.assertEqual(blocks_text(doc), ["Title", "Real content lives here."])

    def test_without_main_the_body_is_used_and_site_headers_still_go(self):
        doc = ingest_bytes("nomain.html", b'<body><header><a href="/">Home</a></header>'
                                          b"<h1>Report</h1><p>Plain page.</p><footer>Contact us</footer></body>")
        self.assertEqual(blocks_text(doc), ["Report", "Plain page."])

    def test_an_article_header_that_holds_the_title_is_kept(self):
        doc = ingest_bytes("article.html", b"<article><header><h1>Headline</h1></header><p>Body.</p></article>")
        self.assertEqual(blocks_text(doc), ["Headline", "Body."])

    def test_a_list_of_long_link_titles_is_content_not_a_menu(self):
        links = "".join(f'<li><a href="/{i}">A long descriptive title of reference number {i} in the series</a></li>'
                        for i in range(6))
        doc = ingest_bytes("refs.html", f"<main><h2>References</h2><ul>{links}</ul></main>".encode())
        self.assertEqual(doc.blocks[1].kind, "list")
        self.assertEqual(len(doc.blocks[1].text.split("\n")), 6)

    def test_a_page_with_nothing_readable_warns(self):
        doc = ingest_bytes("empty.html", b"<html><body><script>x()</script></body></html>")
        self.assertEqual(doc.blocks, [])
        self.assertEqual(len(doc.warnings), 1)


# --- text and markdown ------------------------------------------------------------------------

class TextTests(unittest.TestCase):
    def test_markdown_headings_lists_tables_and_code_fences(self):
        md = ("# Guide\n\nIntro line one\nwrapped here.\n\n## Steps\n\n- one\n- two\n\n"
              "| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode here\n```\n")
        doc = ingest_bytes("guide.md", md.encode())

        self.assertEqual([(b.kind, b.level, b.text) for b in doc.blocks[:4]],
                         [("heading", 1, "Guide"), ("paragraph", 0, "Intro line one wrapped here."),
                          ("heading", 2, "Steps"), ("list", 0, "- one\n- two")])
        self.assertEqual(doc.blocks[4].rows, [["a", "b"], ["1", "2"]])
        self.assertEqual(doc.blocks[5].text, "code here")

    def test_plain_text_is_paragraphs_and_hash_lines_are_not_headings(self):
        doc = ingest_bytes("note.txt", b"# not a heading\nsecond line\n\nNext paragraph.")
        self.assertEqual(blocks_text(doc), ["# not a heading second line", "Next paragraph."])

    def test_windows_1252_text_is_decoded_with_a_warning(self):
        doc = ingest_bytes("legacy.txt", "Caf\xe9 menu".encode("cp1252"))
        self.assertEqual(blocks_text(doc), ["Café menu"])
        self.assertEqual(len(doc.warnings), 1)

    def test_unsupported_extension_is_rejected(self):
        with self.assertRaisesRegex(IngestionError, "Unsupported"):
            ingest_bytes("sheet.xlsx", b"x")


# --- chunking ---------------------------------------------------------------------------------

def doc_of(*blocks):
    return ParsedDocument(source="doc.pdf", blocks=list(blocks))


def sentences(n, prefix="s"):
    return " ".join(f"{prefix}{i} has exactly five words." for i in range(n))  # 5 words per sentence


class ChunkerTests(unittest.TestCase):
    def test_chunks_carry_their_heading_path_pages_and_source(self):
        doc = doc_of(
            Block("heading", "Safety", level=1, page=1), Block("paragraph", "Intro text.", page=1),
            Block("heading", "Dosing", level=2, page=2), Block("paragraph", "Take one daily.", page=2, page_end=3),
            Block("heading", "Storage", level=1, page=4), Block("paragraph", "Keep cool.", page=4))

        chunks = chunk_document(doc)

        self.assertEqual([(c.section, c.text, c.pages, c.source) for c in chunks],
                         [("Safety", "Intro text.", "1", "doc.pdf"),
                          ("Safety > Dosing", "Take one daily.", "2-3", "doc.pdf"),
                          ("Storage", "Keep cool.", "4", "doc.pdf")])
        self.assertEqual([c.index for c in chunks], [0, 1, 2])
        self.assertEqual(chunks[1].with_context(), "Safety > Dosing\nTake one daily.")

    def test_a_chunk_never_crosses_a_heading(self):
        doc = doc_of(Block("heading", "A", level=1), Block("paragraph", "alpha"),
                     Block("heading", "B", level=1), Block("paragraph", "beta"))
        self.assertEqual([(c.section, c.text) for c in chunk_document(doc)], [("A", "alpha"), ("B", "beta")])

    def test_a_long_section_is_split_at_sentences_with_overlap(self):
        doc = doc_of(Block("heading", "Long", level=1), Block("paragraph", sentences(30)))  # 150 words

        chunks = chunk_document(doc, max_words=50, overlap_words=10)

        self.assertGreater(len(chunks), 2)
        for earlier, later in zip(chunks, chunks[1:]):
            tail = " ".join(earlier.text.split()[-10:])
            self.assertTrue(later.text.startswith(tail), (tail, later.text[:80]))
        for chunk in chunks:
            self.assertTrue(chunk.text.rstrip().endswith("words."))  # never cut mid-sentence
            self.assertEqual(chunk.section, "Long")
        body = " ".join(c.text for c in chunks)
        for i in range(30):
            self.assertIn(f"s{i} has", body)

    def test_a_single_giant_sentence_is_split_by_words_as_a_last_resort(self):
        chunks = chunk_document(doc_of(Block("paragraph", "word " * 500)), max_words=100, overlap_words=0)
        self.assertEqual([len(c.text.split()) for c in chunks], [100] * 5)

    def test_a_small_table_is_one_chunk_between_its_neighbours(self):
        rows = [["Item", "Qty"], ["Bolt", "4"]]
        table = Block("table", "| Item | Qty |\n| --- | --- |\n| Bolt | 4 |", page=2, rows=rows)

        chunks = chunk_document(doc_of(Block("paragraph", "Before."), table, Block("paragraph", "After.")))

        self.assertEqual([(c.kind, c.text) for c in chunks],
                         [("text", "Before."), ("table", table.text), ("text", "After.")])

    def test_a_long_table_is_split_between_rows_and_repeats_its_header(self):
        rows = [["Name", "Notes"]] + [[f"row{i}", "word " * 20] for i in range(40)]
        table = Block("table", "| Name | Notes |\n" + "\n".join("| " + " | ".join(r) + " |" for r in rows[1:]), rows=rows)

        chunks = chunk_document(doc_of(table), max_words=100)

        self.assertGreater(len(chunks), 2)
        seen = []
        for chunk in chunks:
            lines = chunk.text.split("\n")
            self.assertEqual(lines[0], "| Name | Notes |")
            self.assertEqual(chunk.kind, "table")
            for line in lines[2:]:
                self.assertTrue(line.endswith("|") and line.count("|") == 3, line)  # no row was cut
                seen.append(line.split("|")[1].strip())
        self.assertEqual(seen, [f"row{i}" for i in range(40)])

    def test_bad_sizes_are_rejected(self):
        for kwargs in ({"max_words": 0}, {"max_words": 10, "overlap_words": 10}, {"overlap_words": -1}):
            with self.assertRaises(ValueError):
                chunk_document(doc_of(Block("paragraph", "x")), **kwargs)

    def test_empty_and_heading_only_documents_give_no_chunks(self):
        self.assertEqual(chunk_document(doc_of()), [])
        self.assertEqual(chunk_document(doc_of(Block("heading", "Alone", level=1))), [])

    def test_end_to_end_from_a_real_pdf(self):
        def page(p):
            box(p, 50, 50, 545, 100, "Handbook", size=22, font="hebo")
            box(p, 50, 110, 545, 145, "Storage", size=15, font="hebo")
            box(p, 50, 150, 545, 250, "STORE-TEXT " + LOREM)

        (chunk,) = chunk_document(ingest_bytes("handbook.pdf", make_pdf(page)))

        self.assertEqual((chunk.section, chunk.pages, chunk.source), ("Handbook > Storage", "1", "handbook.pdf"))
        self.assertTrue(chunk.text.startswith("STORE-TEXT"))


class NodeTests(unittest.TestCase):
    def test_nodes_embed_the_section_but_not_the_citation_fields(self):
        from llama_index.core.schema import MetadataMode
        from ingestion.nodes import to_nodes

        chunks = [Chunk(text="Take one daily.", source="leaflet.pdf", section="Safety > Dosing",
                        page_start=2, page_end=3, index=0)]

        (node,) = to_nodes(chunks)

        self.assertEqual(node.metadata, {"source": "leaflet.pdf", "kind": "text", "section": "Safety > Dosing", "pages": "2-3"})
        embedded = node.get_content(MetadataMode.EMBED)
        self.assertIn("Safety > Dosing", embedded)
        self.assertNotIn("leaflet.pdf", embedded)
        self.assertIn("leaflet.pdf", node.get_content(MetadataMode.LLM))
        self.assertNotEqual(to_nodes(chunks)[0].node_id, node.node_id)  # each call gets fresh ids


if __name__ == "__main__":
    unittest.main()
