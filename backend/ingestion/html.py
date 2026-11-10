"""HTML to structured blocks: strip the page furniture, keep the content's structure.

Removed: scripts, styles, navigation, footers, sidebars, cookie/consent banners, hidden elements and
link-only menus. Kept: headings, paragraphs, lists (with nesting), tables (colspan/rowspan repeated into
every cell they cover) and definition lists. The main content is `<main>`, `[role=main]` or a single
`<article>` when present, otherwise the body. Everything is parsed locally; nothing is fetched here.

Class-name matching is deliberately cautious: an element is only dropped for its class/id when it is a
small part of the page, so a wrapper like `page-with-sidebar` can never delete the whole document.
"""
import re
from typing import Union

from bs4 import BeautifulSoup, NavigableString, Tag

from .models import Block, ParsedDocument, table_or_paragraphs
from .textutil import clean_text

ALWAYS_REMOVE = ["script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed",
                 "input", "select", "button", "textarea", "dialog", "nav", "aside", "footer"]
BOILERPLATE_RE = re.compile(
    r"(^|[\s_-])(cookie|cookies|consent|gdpr|banner|sidebar|menu|navbar|nav|breadcrumb|breadcrumbs|advert|ads?|"
    r"promo|popup|modal|newsletter|subscribe|social|share|sharing|related|comments?|masthead|skip|toc)($|[\s_-])", re.I)
HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
HEADINGS = {f"h{i}": i for i in range(1, 7)}
BLOCK_TAGS = {"p", "div", "section", "article", "main", "ul", "ol", "li", "table", "pre", "blockquote", "dl",
              "figure", "figcaption", "hr", "header", "details", "summary", "form", "fieldset", "address",
              "body", "html", *HEADINGS}
MAX_BOILERPLATE_SHARE = 0.4  # an element matched by class/id must be below this share of the page text
NAV_MIN_LINKS = 4
NAV_LINK_SHARE = 0.8
NAV_MAX_WORDS_PER_LINK = 6


def parse_html(html: Union[str, bytes], source: str = "page.html") -> ParsedDocument:
    soup = BeautifulSoup(html, "lxml")
    title = clean_text(soup.title.get_text(" ")) if soup.title else None
    for br in soup.find_all("br"):
        br.replace_with("\n")

    root = _main_content(soup)
    _remove_boilerplate(root)

    walker = _Walker()
    walker.container(root)
    blocks = _dedupe(walker.blocks)
    doc = ParsedDocument(source=source, blocks=blocks, title=title or None)
    if not doc.title:
        doc.title = next((b.text for b in blocks if b.kind == "heading"), None)
    if not blocks:
        doc.warnings.append("No readable text was found in the page.")
    return doc


# --- cleaning ---------------------------------------------------------------------------------

def _main_content(soup):
    root = soup.find("main") or soup.find(attrs={"role": "main"})
    if root is None:
        articles = soup.find_all("article")
        root = articles[0] if len(articles) == 1 else None
    return root or soup.body or soup


def _text_len(element):
    return len(element.get_text(" ", strip=True))


def _remove_boilerplate(root):
    page_text = max(_text_len(root), 1)
    for element in list(root.find_all(True)):
        if element.decomposed or element is root or not element.name:
            continue
        attrs = element.attrs or {}
        hidden = ("hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true"
                  or HIDDEN_STYLE_RE.search(str(attrs.get("style", ""))))
        role = str(attrs.get("role", "")).lower()
        if (element.name in ALWAYS_REMOVE or hidden or role in {"navigation", "banner", "contentinfo", "complementary"}
                or (element.name == "header" and _is_site_header(element))):
            element.decompose()
            continue
        label = " ".join(attrs.get("class", []) if isinstance(attrs.get("class"), list) else [str(attrs.get("class", ""))])
        label += " " + str(attrs.get("id", ""))
        if (element.name not in {"body", "html", "table", "tr", "td", "th"} and BOILERPLATE_RE.search(label)
                and _text_len(element) <= MAX_BOILERPLATE_SHARE * page_text):
            element.decompose()
    for element in reversed(root.find_all(["ul", "ol", "div", "section"])):
        if not element.decomposed and _is_link_menu(element):
            element.decompose()


def _is_site_header(header):
    """A <header> that holds a heading is the article's own title block; any other is site chrome."""
    return not header.find(list(HEADINGS)) or _link_share(header) > 0.5


def _link_share(element):
    total = _text_len(element)
    return sum(_text_len(a) for a in element.find_all("a")) / total if total else 0.0


def _is_link_menu(element):
    links = element.find_all("a")
    if len(links) < NAV_MIN_LINKS or _link_share(element) < NAV_LINK_SHARE:
        return False
    words = sum(len(a.get_text(" ", strip=True).split()) for a in links)
    return words / len(links) <= NAV_MAX_WORDS_PER_LINK


# --- structure --------------------------------------------------------------------------------

def _inline(element) -> str:
    return clean_text(re.sub(r"[ \t\r\f\v]*\n[ \t\r\f\v]*", " ", element.get_text("")))


class _Walker:
    def __init__(self):
        self.blocks = []

    def container(self, element):
        inline = []

        def flush():
            text = clean_text(re.sub(r"\s+", " ", "".join(inline)))
            if text:
                self.blocks.append(Block("paragraph", text))
            inline.clear()

        for child in element.children:
            if isinstance(child, NavigableString):
                if child.__class__.__name__ in {"Comment", "Doctype", "ProcessingInstruction", "Declaration"}:
                    continue
                inline.append(str(child))
            elif child.name in BLOCK_TAGS:
                flush()
                self._block(child)
            elif isinstance(child, Tag):
                inline.append(child.get_text(""))
        flush()

    def _block(self, tag):
        name = tag.name
        if name in HEADINGS:
            text = _inline(tag)
            if text:
                self.blocks.append(Block("heading", text, level=HEADINGS[name]))
        elif name in {"p", "figcaption", "summary", "address", "pre"}:
            text = clean_text(tag.get_text("")) if name == "pre" else _inline(tag)
            if text:
                self.blocks.append(Block("paragraph", text))
        elif name in {"ul", "ol"}:
            lines = self._list_lines(tag, 0)
            if lines:
                self.blocks.append(Block("list", "\n".join(lines)))
        elif name == "table":
            self._table(tag)
        elif name == "dl":
            self._definitions(tag)
        elif name == "hr":
            return
        else:  # div, section, article, blockquote, figure, form, details, li ...
            self.container(tag)

    def _list_lines(self, list_tag, depth):
        lines = []
        for number, item in enumerate(list_tag.find_all("li", recursive=False), start=1):
            nested = item.find_all(["ul", "ol"], recursive=False)
            own = clean_text(re.sub(r"\s+", " ", "".join(
                str(c) if isinstance(c, NavigableString) else c.get_text("")
                for c in item.children if c not in nested)))
            marker = f"{number}." if list_tag.name == "ol" else "-"
            if own:
                lines.append("  " * min(depth, 4) + f"{marker} {own}")
            for sub in nested:
                lines.extend(self._list_lines(sub, depth + 1))
        return lines

    def _definitions(self, dl):
        term = None
        for child in dl.find_all(["dt", "dd"], recursive=False):
            text = _inline(child)
            if child.name == "dt":
                term = text
            elif text:
                self.blocks.append(Block("paragraph", f"{term}: {text}" if term else text))

    def _table(self, table):
        if table.find("table"):  # a table that wraps other tables is page layout: read its cells in order
            for cell in table.find_all(["td", "th"]):
                if cell.find_parent("table") is table:
                    self.container(cell)
            return
        self.blocks.extend(table_or_paragraphs(_table_grid(table)))


def _table_grid(table):
    """Rows of cell text with colspan repeated across columns and rowspan carried down rows."""
    rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    grid, carried = [], {}
    for r, tr in enumerate(rows):
        row, col = [], 0

        def drain():
            nonlocal col
            while (r, col) in carried:
                row.append(carried.pop((r, col)))
                col += 1

        for cell in tr.find_all(["td", "th"], recursive=False):
            drain()
            text = _inline(cell)
            colspan = max(1, min(_attr_int(cell, "colspan"), 64))
            rowspan = max(1, min(_attr_int(cell, "rowspan"), 500))
            for _ in range(colspan):
                row.append(text)
                for down in range(1, rowspan):
                    carried[(r + down, col)] = text
                col += 1
        drain()
        grid.append(row)
    return grid


def _attr_int(cell, name):
    try:
        return int(str(cell.get(name, 1)).strip() or 1)
    except ValueError:
        return 1


def _dedupe(blocks):
    out = []
    for block in blocks:
        if out and out[-1].kind == block.kind == "paragraph" and out[-1].text == block.text:
            continue
        out.append(block)
    return out
