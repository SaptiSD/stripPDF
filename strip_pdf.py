#!/usr/bin/env python3
"""
strip_pdf.py - make a text-only copy of a PDF.

Keeps the body text and the section headings. Drops photos, charts and other
graphics together with the text that belongs to them: captions, figure titles,
chart labels, photo credits, and the running header/footer on every page.

Usage:
    python strip_pdf.py report.pdf                 # -> report_text_only.pdf + report_text_only.md
    python strip_pdf.py report.pdf -o clean.pdf    # choose the output name

Requires: pip install pymupdf
"""
from __future__ import annotations

import argparse
import html
import io
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")   # silence PyMuPDF's add-on advert

# --- Tuning knobs (PDF points; 72 pt = 1 inch) -------------------------------
MIN_FIGURE_SIDE = 36    # graphics smaller than this are decoration (icons, rules, bullets)
BACKGROUND_SHARE = 0.8  # graphics covering more of the page than this are backgrounds
CAPTION_GAP = 24        # max distance between a figure and its caption or title
MARGIN_SHARE = 0.08     # top/bottom band searched for running headers and footers
MAX_HEADING_WORDS = 25  # longer "headings" are really large-print paragraphs
MIN_TABLE_ROWS = 3      # a borderless table needs at least this many aligned rows (header included)

TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
BOLD_HINTS = ("bold", "black", "heavy", "semibold", "demi")
CAPTION_RE = re.compile(
    r"^(fig(ure)?\.?\s*\d+|figure\s*:|sources?\s*:|(photo|image)?\s*credits?\s*:|"
    r"photo(graph)?\s*:|image\s*:|courtesy\b|chart\s*\d+|exhibit\s*\d+|illustration\s*:)",
    re.I,
)
BULLETS = "•●▪■◦‣∙○▶►▸▹➤➢✓✔❖◆"       # Word/Wingdings private-use bullets are mapped to "•" first
BULLET_RE = re.compile(rf"^[{BULLETS}]")
LONE_BULLET_RE = re.compile(rf"[{BULLETS}\-–o]")             # a bullet split from its item text
NUMBERED_RE = re.compile(r"^\d{1,3}[.)]\s+[A-Z]")           # "1. First point"
NOTE_RE = re.compile(r"^\d{1,3}[.)]?\s+[A-Z“\"']")         # "12 Author. Title..." in small print
DOT_LEADER_RE = re.compile(r"\s*(\.\s*){4,}\d*\s*$")         # "Chapter title.......12"
URL_TAIL_RE = re.compile(r"(https?://|www\.)\S*$")
PAGE_NUMBER_RE = re.compile(r"(page\s*)?(\d+(\s*(/|of)\s*\d+)?|x{0,3}(ix|iv|v?i{0,3}))")
SENTENCE_END = tuple('.!?:;"”’)]')

# Character clean-up applied to all extracted text (keys are Unicode code points).
TYPO_FIXES = {
    0x09: " ", 0x0A: " ", 0x0D: " ",                                   # tabs/newlines -> space
    0x00A0: " ", 0x2007: " ", 0x202F: " ",                             # non-breaking spaces
    0x2010: "-", 0x2011: "-",                                          # hyphens the output font lacks
    0xFB00: "ff", 0xFB01: "fi", 0xFB02: "fl", 0xFB03: "ffi", 0xFB04: "ffl", 0xFB05: "st", 0xFB06: "st",
    0xF0B7: "•", 0xF0A7: "▪", 0xF076: "•", 0xF0D8: "•", 0xF0FC: "✓",  # Word/Wingdings bullets
}
KEEP_FORMAT_CHARS = {chr(0x200C), chr(0x200D)}   # zero-width (non-)joiners matter in some scripts
UNKNOWN_CHAR = chr(0xFFFD)                       # what a PDF gives for glyphs it can't map to Unicode


# --- Data holders -------------------------------------------------------------
@dataclass
class Line:
    html: str
    size: float
    bold: bool
    italic: bool
    color: int
    rect: pymupdf.Rect

    @property
    def text(self) -> str:
        return plain(self.html)


@dataclass
class Segment:
    """A run of lines that read as one unit: a paragraph or a heading."""
    lines: list[Line]

    @property
    def rect(self) -> pymupdf.Rect:
        r = pymupdf.Rect(self.lines[0].rect)
        for line in self.lines[1:]:
            r |= line.rect
        return r

    @property
    def size(self) -> float:
        sizes = Counter()
        for l in self.lines:
            sizes[l.size] += len(l.text)
        return sizes.most_common(1)[0][0]

    @property
    def bold(self) -> bool:
        return all(l.bold for l in self.lines)

    @property
    def italic(self) -> bool:
        return all(l.italic for l in self.lines)

    @property
    def color(self) -> int:
        return Counter(l.color for l in self.lines).most_common(1)[0][0]

    @property
    def html(self) -> str:
        return join_lines([l.html for l in self.lines])

    @property
    def text(self) -> str:
        return join_lines([l.text for l in self.lines])


@dataclass
class TableData:
    rect: pymupdf.Rect
    rows: list[list[str]]   # cell text as HTML; the first row is the header
    lines: set[int]         # id() of the text lines the table replaces


@dataclass
class PageData:
    number: int
    rect: pymupdf.Rect
    blocks: list[list[Line]]
    images: list[pymupdf.Rect]
    graphics: list[pymupdf.Rect]
    tables: list[TableData] = field(default_factory=list)


@dataclass
class Element:
    kind: str     # "h" (heading), "p" (paragraph) or "t" (table)
    html: str
    page: int
    size: float = 0.0
    level: int = 0
    rect: pymupdf.Rect | None = None
    bookmark: int | None = None   # index into the PDF's bookmark list, if this is a bookmarked heading
    rows: list[list[str]] | None = None   # table cells, for kind "t"


@dataclass
class Result:
    pdf: bytes
    markdown: str
    html: str
    stats: dict = field(default_factory=dict)


# --- Small helpers ------------------------------------------------------------
SOFT_JOIN = chr(0x2060)  # (WORD JOINER) marks a line-end hyphen; resolved later by fix_hyphens()


def join_lines(parts: list[str]) -> str:
    """Join wrapped lines; a line ending in '-' or inside a URL continues on the next line."""
    out = ""
    for part in parts:
        part = part.strip()
        if not out:
            out = part
        elif out.endswith("-") and re.match(r"(and|or|to)\b", part):
            out += " " + part  # "defence-" + "and security-related": a suspended hyphen
        elif out.endswith("-") and part[:1].isalpha():
            out += SOFT_JOIN + part
        elif out.endswith(("-", "—", "/")) and part[:1].isalnum():
            out += part        # "2023-" + "2024", "sectors—" + "Positioning"
        elif URL_TAIL_RE.search(out) and (part[:1].islower() if out.endswith(".")
                                          else re.match(r"[\w%-]+[/.%=&_?-]", part)):
            out += part        # a URL broken over two lines ("www.ses." + "com/press-release/...")
        else:
            out += " " + part
    return re.sub(r"\s+", " ", out).strip()


def fix_hyphens(elements: list[Element]) -> None:
    """'for-' + 'mulas' -> 'formulas' when the document uses that word elsewhere; else keep
    the hyphen ('five-' + 'year' -> 'five-year')."""
    words = Counter()
    for e in elements:
        words.update(w.lower() for w in re.findall(r"[\w-]+", plain(e.html).replace(SOFT_JOIN, " ")))

    def pick(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        solid = (a + b).lower()
        return a + b if words[solid] > words[f"{a}-{b}".lower()] else f"{a}-{b}"

    for e in elements:
        e.html = re.sub(rf"(\w+)-{SOFT_JOIN}(\w+)", pick, e.html).replace(SOFT_JOIN, "")


def clean_text(text: str) -> str:
    """Make extracted text safe to re-typeset: compose accents, expand ligatures, map symbol-font
    bullets, and drop invisible control/format characters and unmappable glyphs (e.g. the U+0003
    InDesign leaves at paragraph ends, which the output font would draw as a box)."""
    text = unicodedata.normalize("NFC", text).translate(TYPO_FIXES)
    return "".join(c for c in text if c in KEEP_FORMAT_CHARS
                   or (c != UNKNOWN_CHAR and unicodedata.category(c) not in ("Cc", "Cf", "Co", "Cs")))


def unreadable_share(doc) -> float:
    """Share of characters the PDF can't map to real Unicode (broken or missing font encodings)."""
    chars = [c for page in doc for c in page.get_text() if not c.isspace()]
    bad = sum(c == UNKNOWN_CHAR or (unicodedata.category(c) == "Co" and ord(c) not in TYPO_FIXES)
              for c in chars)
    return bad / max(len(chars), 1)


def is_letter_spaced(text: str) -> bool:
    """'S PAC E F O U N DAT I O N': wide letter spacing makes the PDF report spaces between letters."""
    tokens = text.split()
    return len(tokens) >= 5 and sum(len(t) == 1 for t in tokens) >= 0.6 * len(tokens)


def untrack(chars: list[dict], size: float) -> str:
    """Rebuild letter-spaced text from character positions: real word gaps are clearly wider
    than the (already wide) gaps between letters."""
    letters = [c for c in chars if not c["c"].isspace()]
    gaps = [b["bbox"][0] - a["bbox"][2] for a, b in zip(letters, letters[1:])]
    letter_gap = sorted(gaps)[len(gaps) // 2] if gaps else 0
    out = letters[0]["c"] if letters else ""
    for gap, ch in zip(gaps, letters[1:]):
        out += (" " if gap > letter_gap + 0.2 * size else "") + ch["c"]
    return out


def is_bold(span) -> bool:
    return bool(span["flags"] & pymupdf.TEXT_FONT_BOLD) or any(h in span["font"].lower() for h in BOLD_HINTS)


def is_italic(span) -> bool:
    font = span["font"].lower()
    return bool(span["flags"] & pymupdf.TEXT_FONT_ITALIC) or "italic" in font or "oblique" in font


def make_line(raw) -> Line | None:
    if abs(raw["dir"][1]) > 0.1:          # rotated text: chart axes, side banners
        return None
    parts, styles = [], Counter()
    total = bold = italic = 0
    for span in raw["spans"]:
        text = clean_text("".join(ch["c"] for ch in span["chars"]))
        if not text:
            continue
        superscript = span["flags"] & pymupdf.TEXT_FONT_SUPERSCRIPT and text.strip()
        escaped = html.escape(text, quote=False)
        parts.append(f"<sup>{escaped.strip()}</sup>" if superscript else escaped)
        n = len(text.strip())
        if not n or superscript:
            continue
        total += n
        styles[(round(span["size"] * 2) / 2, span["color"])] += n
        bold += n if is_bold(span) else 0
        italic += n if is_italic(span) else 0
    if not total:
        return None
    (size, color), _ = styles.most_common(1)[0]
    text = re.sub(r"\s+", " ", "".join(parts)).strip()
    if is_letter_spaced(text):
        chars = [ch for span in raw["spans"] for ch in span["chars"]]
        text = html.escape(clean_text(untrack(chars, size)), quote=False)
    return Line(text, size, bold >= 0.8 * total, italic >= 0.8 * total, color, pymupdf.Rect(raw["bbox"]))


def find_figures(page) -> tuple[list[pymupdf.Rect], list[pymupdf.Rect], list[pymupdf.Rect]]:
    """Return (raster image areas, vector graphic areas) big enough to be figures, plus every
    sizeable vector area including page-sized ones (places a ruled table could be)."""
    page_area = page.rect.get_area()

    def big(r: pymupdf.Rect) -> bool:
        return not r.is_empty and min(r.width, r.height) >= MIN_FIGURE_SIDE

    images = [r for r in (pymupdf.Rect(i["bbox"]) & page.rect for i in page.get_image_info())
              if big(r) and r.get_area() < BACKGROUND_SHARE * page_area]
    try:
        drawn = [r for r in (pymupdf.Rect(c) & page.rect for c in page.cluster_drawings()) if big(r)]
    except Exception:                      # malformed vector content - just skip it
        drawn = []
    graphics = [r for r in drawn if r.get_area() < BACKGROUND_SHARE * page_area]
    return images, graphics, drawn


def contains(areas: list[pymupdf.Rect], r: pymupdf.Rect) -> bool:
    centre = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
    return any(centre in a for a in areas)


def overlaps_horizontally(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    return min(a.x1, b.x1) - max(a.x0, b.x0) >= 0.3 * min(a.width, b.width)


def touching_figure(r: pymupdf.Rect, figures: list[pymupdf.Rect], above_only=False) -> bool:
    """True if r sits just above (or, unless above_only, just below) a figure."""
    for f in figures:
        if not overlaps_horizontally(r, f):
            continue
        if -2 <= f.y0 - r.y1 <= CAPTION_GAP:
            return True
        if not above_only and -2 <= r.y0 - f.y1 <= CAPTION_GAP:
            return True
    return False


def beside_figure(r: pymupdf.Rect, figures: list[pymupdf.Rect]) -> bool:
    """True if r sits level with a figure, just to its left or right (e.g. a chart's axis labels)."""
    for f in figures:
        if min(r.y1, f.y1) - max(r.y0, f.y0) > 0 and (
                -2 <= f.x0 - r.x1 <= CAPTION_GAP or -2 <= r.x0 - f.x1 <= CAPTION_GAP):
            return True
    return False


def footer_key(text: str) -> str:
    """Page-independent form of a header/footer: no digits, punctuation or case."""
    return re.sub(r"[\W\d_]+", "", text.lower())


def is_page_number(text: str) -> bool:
    return bool(PAGE_NUMBER_RE.fullmatch(text.strip(" |–-").lower()))


def plain(fragment: str) -> str:
    return html.unescape(re.sub(r"</?sup>", "", fragment))


# --- Tables -------------------------------------------------------------------
def find_tables(page, lines: list[Line], images, graphics, drawn) -> list[TableData]:
    """Tables on a page: ruled ones (drawn with lines or cell borders), then borderless ones,
    including tables whose rows are only shaded bands."""
    tables = ruled_tables(page, lines, images, drawn)
    paths = page.get_drawings() if graphics else []
    charts = [g for g in graphics if not is_row_bands(g, paths)]
    return tables + borderless_tables(lines, images + charts + [t.rect for t in tables])


def is_row_bands(region: pymupdf.Rect, paths: list[dict]) -> bool:
    """True if a vector area is nothing but full-width filled stripes: the shaded rows of a table
    (a chart has bars, axes and gridlines instead)."""
    inside = [p for p in paths if pymupdf.Rect(p["rect"]) in region + (-1, -1, 1, 1)]
    return bool(inside) and all(
        p.get("fill") is not None and all(item[0] in ("re", "qu") for item in p["items"])
        and pymupdf.Rect(p["rect"]).width >= 0.9 * region.width for p in inside)


def ruled_tables(page, lines: list[Line], images, drawn) -> list[TableData]:
    """PyMuPDF's table finder is exact for ruled tables, but slow on whole pages and it also
    "finds" charts. So it only runs on vector-graphic areas holding several text lines, and its
    answers must pass is_real_table()."""
    tables = []
    for region in drawn:
        if contains(images, region) or sum(contains([region], l.rect) for l in lines) < 4:
            continue
        try:
            found = page.find_tables(clip=region, strategy="lines").tables
        except Exception:                  # odd vector content - treat as no table
            continue
        for t in found:
            raw = t.extract()
            if is_real_table(raw):
                rect = pymupdf.Rect(t.bbox)
                tables.append(TableData(rect, tidy_rows(raw), {id(l) for l in lines if contains([rect], l.rect)}))
    return tables


def is_real_table(rows: list[list[str | None]]) -> bool:
    """Charts with grid lines come back from the finder as 'tables' too, but with mostly empty
    cells or a whole axis of tick labels stacked in one cell. A real table has 2+ rows and 2+
    columns, mostly filled with short text."""
    cells = [c for row in rows for c in row if c is not None]      # None = covered by a merged cell
    filled = [c for c in cells if c.strip()]
    if len(rows) < 2 or max(map(len, rows)) < 2 or not filled:
        return False
    lines_per_cell = sum(c.count("\n") + 1 for c in filled) / len(filled)
    return (len(filled) >= 0.6 * len(cells) and len(cells) >= 0.5 * sum(map(len, rows))
            and lines_per_cell <= 4)


def borderless_tables(lines: list[Line], exclude: list[pymupdf.Rect]) -> list[TableData]:
    """Tables without ruling lines. Each cell arrives from the PDF as its own text line, so such a
    table shows up as consecutive rows of short, widely spaced pieces of text that line up in
    columns. Text inside pictures and charts is skipped (chart labels line up too)."""
    free = sorted((l for l in lines if not contains(exclude, l.rect)), key=lambda l: (l.rect.y0, l.rect.x0))
    rows: list[list[Line]] = []
    for line in free:
        if rows and abs(mid_y(line) - mid_y(rows[-1][0])) < 0.4 * line.size:
            rows[-1].append(line)
        else:
            rows.append([line])
    for row in rows:
        row.sort(key=lambda l: l.rect.x0)

    def multi_cell(row: list[Line]) -> bool:
        return len(row) >= 2 and all(b.rect.x0 - a.rect.x1 >= min(a.size, b.size) for a, b in zip(row, row[1:]))

    def gap_above(row: list[Line], prev: list[Line]) -> float:
        return row[0].rect.y0 - max(l.rect.y1 for l in prev)

    tables, run, width = [], [], 0
    for row in rows + [[]]:                # the empty sentinel row closes the last run
        if run and continues_cells(row, run[-1], width):
            run[-1] = run[-1] + row        # wrapped second line of some cells in the row above
            continue
        if multi_cell(row) and (not run or gap_above(row, run[-1]) <= 2 * row[0].rect.height):
            run.append(row)
            width = max(width, len(row))
            continue
        if table := grid_from_rows(run):
            tables.append(table)
        run = [row] if multi_cell(row) else []
        width = len(row) if run else 0
    return tables


def continues_cells(row: list[Line], prev: list[Line], width: int) -> bool:
    """True if row holds the wrapped next lines of cells in the row above: fewer pieces than a full
    row, set tight underneath it, and each piece inside one of its cells."""
    if not row or len(row) >= width or row[0].rect.y0 - max(l.rect.y1 for l in prev) > 0.4 * row[0].size:
        return False
    for line in row:
        inside_a_cell = any(p.rect.x0 - 3 <= line.rect.x0 <= p.rect.x1 for p in prev)
        crosses_next_column = any(line.rect.x0 + 3 < p.rect.x0 < line.rect.x1 for p in prev)
        if not inside_a_cell or crosses_next_column:
            return False
    return True


def grid_from_rows(run: list[list[Line]]) -> TableData | None:
    """Turn aligned rows of cell lines into a table, or None if they don't look like one."""
    if len(run) < MIN_TABLE_ROWS:
        return None
    cells = [l for row in run for l in row]
    words = sorted(len(l.text.split()) for l in cells)
    if words[len(words) // 2] > 3 or words[-1] > 12:
        return None                        # prose, e.g. two text columns side by side
    columns: list[list[float]] = []        # x-ranges covered by each column, left to right
    for x0, x1 in sorted((l.rect.x0, l.rect.x1) for l in cells):
        if columns and x0 <= columns[-1][1] + 2:
            columns[-1][1] = max(columns[-1][1], x1)
        else:
            columns.append([x0, x1])
    if len(columns) < 2 or len(columns) > max(map(len, run)):
        return None                        # cells don't line up in columns
    grid = [[""] * len(columns) for _ in run]
    for r, row in enumerate(run):
        for line in row:
            c = next(i for i, (x0, x1) in enumerate(columns) if x0 - 0.1 <= line.rect.x0 <= x1)
            grid[r][c] = join_lines([grid[r][c], line.html]) if grid[r][c] else line.html
    rect = pymupdf.Rect(cells[0].rect)
    for l in cells[1:]:
        rect |= l.rect
    return TableData(rect, grid, {id(l) for l in cells})


def mid_y(line: Line) -> float:
    return (line.rect.y0 + line.rect.y1) / 2


def tidy_rows(rows: list[list[str | None]]) -> list[list[str]]:
    """Clean the finder's cell text and drop empty rows and columns."""
    rows = [[html.escape(re.sub(r"\s+", " ", clean_text(c or "")).strip(), quote=False) for c in row] for row in rows]
    rows = [r for r in rows if any(r)]
    width = max(map(len, rows))
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [i for i in range(width) if any(r[i] for r in rows)]
    return [[r[i] for i in keep] for r in rows]


def page_parts(p: PageData):
    """The page's text in reading order: runs of ordinary lines, with each table placed where
    its first cell appears."""
    table_of = {line_id: t for t in p.tables for line_id in t.lines}
    done = set()
    for block in p.blocks:
        run = []
        for line in block:
            table = table_of.get(id(line))
            if table is None:
                run.append(line)
                continue
            if run:
                yield run
                run = []
            if id(table) not in done:
                done.add(id(table))
                yield table
        if run:
            yield run


# --- The pipeline -------------------------------------------------------------
def read_pages(doc) -> list[PageData]:
    pages = []
    for page in doc:
        blocks = []
        for b in page.get_text("rawdict", flags=TEXT_FLAGS)["blocks"]:   # rawdict: per-character positions
            lines = [l for l in (make_line(raw) for raw in b.get("lines", [])) if l]
            if lines:
                blocks.append(lines)
        images, graphics, drawn = find_figures(page)
        tables = find_tables(page, [l for b in blocks for l in b], images, graphics, drawn)
        # A ruled table's border is a vector graphic too, but it is not a figure to strip.
        graphics = [g for g in graphics if not any((g & t.rect).get_area() > 0.5 * t.rect.get_area() for t in tables)]
        pages.append(PageData(page.number, page.rect, blocks, images, graphics, tables))
    return pages


def split_block(lines: list[Line], is_heading_style, starts_item, note_number) -> list[Segment]:
    """Split a text block into paragraphs/headings at style changes, vertical gaps,
    and the start of bullet points or numbered notes."""
    segments, current, last_note = [], [], None
    for line in lines:
        note = note_number(line)
        next_note = note is not None and last_note is not None and note == last_note + 1   # "23 ispace..."
        if current:
            prev = current[-1]
            gap = line.rect.y0 - prev.rect.y1
            if (is_heading_style(line) != is_heading_style(prev)
                    or (is_heading_style(line) and line.size != prev.size)
                    or abs(line.size - prev.size) >= 1.5
                    or gap > 0.6 * prev.size
                    or starts_item(line) or next_note):
                segments.append(Segment(current))
                current = []
        if note is not None and (next_note or starts_item(line)):
            last_note = note
        current.append(line)
    if current:
        segments.append(Segment(current))
    return segments


def convert(pdf_bytes: bytes, name: str = "document") -> Result:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    if doc.needs_pass:
        raise ValueError("This PDF is password-protected.")
    pages = read_pages(doc)

    # 1. The body text style is whatever style most of the characters use.
    sizes, colors = Counter(), Counter()
    for p in pages:
        for block in p.blocks:
            for line in block:
                sizes[line.size] += len(line.text)
                colors[line.color] += len(line.text)
    if not sizes:
        raise ValueError("No text found - this looks like a scanned PDF (images only), "
                         "which would need OCR first.")
    if unreadable_share(doc) > 0.3:
        raise ValueError("Most of this PDF's text can't be decoded (its fonts carry no Unicode "
                         "mapping), so it would need OCR first.")
    body = sizes.most_common(1)[0][0]
    body_color = colors.most_common(1)[0][0]

    def is_heading_style(line: Line) -> bool:
        return line.size >= body + 0.8 or (line.bold and line.size >= body - 0.6)

    def starts_item(line: Line) -> bool:
        text = line.text
        return bool(BULLET_RE.match(text) or NUMBERED_RE.match(text)
                    or (line.size < body - 0.5 and NOTE_RE.match(text)))

    def note_number(line: Line) -> int | None:
        """The number a small-print note line starts with ("12 Author. Title..."), if any."""
        m = re.match(r"(\d{1,3})[.)]?\s+\S", line.text)
        return int(m[1]) if m and line.size < body - 0.5 else None

    def segments(block: list[Line]) -> list[Segment]:
        return split_block(block, is_heading_style, starts_item, note_number)

    # 2. Running headers/footers: same text in the page margins on many pages.
    seen = Counter()
    for p in pages:
        keys = set()
        for block in p.blocks:
            for seg in segments(block):
                r = seg.rect
                if r.y1 < p.rect.height * MARGIN_SHARE or r.y0 > p.rect.height * (1 - MARGIN_SHARE):
                    keys.add(footer_key(seg.text))
        seen.update(keys)
    repeated = {k for k, n in seen.items() if k and n >= max(3, 0.25 * len(pages))}

    # The PDF's own bookmarks, when it has them, are the most reliable list of headings:
    # (level, comparable title, 0-based target page).
    outline = [(level, heading_key(title), page - 1) for level, title, page in doc.get_toc()]

    # 3. Classify every segment: keep as heading/paragraph, or drop as figure text.
    stats = Counter()
    elements: list[Element] = []
    for p in pages:
        figures = p.images + p.graphics
        stats["images_removed"] += len(p.images)
        stats["graphics_removed"] += len(p.graphics)
        for part in page_parts(p):
            if isinstance(part, TableData):
                stats["tables"] += 1
                elements.append(Element("t", table_html(part.rows), p.number, rect=part.rect, rows=part.rows))
                continue
            for seg in segments(part):
                r, size, text = seg.rect, seg.size, seg.text
                n_lines, n_words = len(seg.lines), len(text.split())
                small = size < body - 0.5
                in_margin = r.y1 < p.rect.height * MARGIN_SHARE or r.y0 > p.rect.height * (1 - MARGIN_SHARE)

                if in_margin and (footer_key(text) in repeated or is_page_number(text)):
                    stats["header_footer_removed"] += 1
                elif contains(p.images, r) and (small or (n_words <= 12 and size < body + 0.8)):
                    stats["figure_text_removed"] += 1            # labels/captions printed on a picture
                elif contains(p.graphics, r) and (size <= 0.8 * body
                                                  or (small and (n_lines <= 2 or n_words < 5 * n_lines))):
                    stats["figure_text_removed"] += 1            # chart labels, legends
                elif small and n_words <= 4 and beside_figure(r, figures):
                    stats["figure_text_removed"] += 1            # axis labels just outside a chart
                elif CAPTION_RE.match(text) and size <= body + 0.5 and n_lines <= 4:
                    stats["captions_removed"] += 1               # "Source: ...", "Figure 3: ..."
                elif (small or (seg.italic and size <= body)) and n_lines <= 6 and touching_figure(r, figures):
                    stats["captions_removed"] += 1               # small print next to a picture
                elif (n_lines <= 2 and size <= body + 0.5 and (seg.bold or seg.color != body_color)
                      and touching_figure(r, figures, above_only=True)):
                    stats["captions_removed"] += 1               # chart/figure title
                else:
                    looks_like_heading = (
                        is_heading_style(seg.lines[0]) and n_words <= MAX_HEADING_WORDS
                        and re.search(r"[A-Za-z]", text) and not re.search(r"https?://|www\.|\w@\w", text)
                        and (size >= body + 0.8 or (n_lines <= 2 and n_words <= 15 and not text.endswith("."))))
                    mark = find_bookmark(outline, text, p.number) if n_words <= MAX_HEADING_WORDS else None
                    elements.append(Element("h" if looks_like_heading else "p", DOT_LEADER_RE.sub("", seg.html),
                                            p.number, size, rect=r, bookmark=mark))

    apply_bookmarks(elements)
    elements = merge_elements(elements)
    fix_hyphens(elements)
    assign_heading_levels(elements, body, len(pages), outline)

    stats.update(pages=len(pages),
                 headings=sum(e.kind == "h" for e in elements),
                 paragraphs=sum(e.kind == "p" for e in elements),
                 words=sum(len(plain(re.sub(r"<[^>]+>", " ", e.html)).split()) for e in elements))
    title = (doc.metadata or {}).get("title") or Path(name).stem
    body_html = build_html(elements)
    bookmarks = {f"h{i}": plain(e.html) for i, e in enumerate(elements) if e.kind == "h"}
    return Result(pdf=html_to_pdf(body_html, title, bookmarks),
                  markdown=build_markdown(elements),
                  html=body_html,
                  stats=dict(stats))


def heading_key(text: str) -> str:
    """Compare headings ignoring case, punctuation and leading section numbers ('2.1 Scope')."""
    return re.sub(r"[\W_]+", "", re.sub(r"^[\d.\s]+", "", text.lower()))


def find_bookmark(outline: list[tuple[int, str, int]], text: str, page: int) -> int | None:
    """Index of the bookmark with this title that points at (or next to) this page, if any."""
    key = heading_key(text)
    matches = [i for i, (_, k, target) in enumerate(outline) if key and k == key and abs(target - page) <= 1]
    return min(matches, key=lambda i: abs(outline[i][2] - page)) if matches else None


def apply_bookmarks(elements: list[Element]) -> None:
    """Make bookmarked text a heading, even if it doesn't look like one. A title can occur more
    than once near its page (a contents list, then the heading itself); the heading is the last
    occurrence, so the earlier ones keep whatever role their typography gave them."""
    last = {e.bookmark: i for i, e in enumerate(elements) if e.bookmark is not None}
    for i, e in enumerate(elements):
        if e.bookmark is not None and last[e.bookmark] != i:
            e.bookmark = None
        elif e.bookmark is not None:
            e.kind = "h"


def is_title_continuation(prev: Element, e: Element) -> bool:
    """True if heading e continues heading prev: same page, stacked directly underneath.
    Display titles often mix sizes ("THE STATE OF THE" small, "GLOBAL SPACE ECONOMY" huge)."""
    if e.page != prev.page or not overlaps_horizontally(prev.rect, e.rect) or e.rect.y0 < prev.rect.y0:
        return False
    gap = e.rect.y0 - prev.rect.y1
    return gap < (e.size if e.size == prev.size else 0.5 * min(e.size, prev.size))


def ends_mid_sentence(fragment: str) -> bool:
    """True unless the text ends a sentence; a trailing footnote marker ("...economy.<sup>12</sup>")
    doesn't count."""
    return not plain(re.sub(r"(\s*<sup>[^<]*</sup>)+\s*$", "", fragment)).rstrip().endswith(SENTENCE_END)


def continues_paragraph(prev: Element, e: Element) -> bool:
    """True if paragraph e carries on paragraph prev, which stopped mid-sentence: e starts in lower
    case, or sits tight under prev in the same column (the PDF stored one paragraph as two blocks,
    e.g. where it wraps around a photo), or prev ended a page and e starts the next one. A numbered
    note or list item ("12 Author...", "3. Step") always starts afresh."""
    text = plain(e.html)
    if text[:1].islower():
        return True
    if abs(e.size - prev.size) >= 1 or re.match(r"\d{1,3}[.)]?\s", text):
        return False
    if e.page == prev.page:
        return overlaps_horizontally(prev.rect, e.rect) and -e.size <= e.rect.y0 - prev.rect.y1 <= 0.5 * e.size
    return e.page == prev.page + 1 and len(plain(prev.html).split()) >= 8


def merge_elements(elements: list[Element]) -> list[Element]:
    """Re-join paragraphs split by a page break or a picture, and headings split over lines or
    sizes; drop a heading repeated with no text in between (cover page + title page)."""
    merged: list[Element] = []
    for e in elements:
        prev = merged[-1] if merged else None
        if prev and e.kind == prev.kind == "p" and LONE_BULLET_RE.fullmatch(plain(prev.html)):
            prev.html = "• " + e.html
        elif (prev and e.kind == prev.kind == "p" and not BULLET_RE.match(plain(e.html))
                and ends_mid_sentence(prev.html) and continues_paragraph(prev, e)):
            prev.html = join_lines([prev.html, e.html])
            prev.rect = prev.rect | e.rect if e.page == prev.page else e.rect
            prev.page = e.page
        elif (prev and e.kind == prev.kind == "h" and is_title_continuation(prev, e)
              and (prev.bookmark is None or e.bookmark is None)):   # two bookmarks = two headings
            prev.html = join_lines([prev.html, e.html])
            prev.rect |= e.rect
            prev.size = max(prev.size, e.size)
            prev.bookmark = e.bookmark if prev.bookmark is None else prev.bookmark
        else:
            merged.append(e)

    result, recent_headings = [], set()
    for e in merged:
        if e.kind == "h" and len(e.html.split()) > MAX_HEADING_WORDS:
            e.kind = "p"             # a "heading" that grew into a paragraph is a paragraph
        if e.kind != "h":
            recent_headings.clear()
        elif heading_key(plain(e.html)) in recent_headings:
            continue
        else:
            recent_headings.add(heading_key(plain(e.html)))
        result.append(e)
    return result


def assign_heading_levels(elements: list[Element], body: float, n_pages: int,
                          outline: list[tuple[int, str, int]]) -> None:
    """Recurring heading sizes become H1, H2, H3 (biggest first) and bold body-size headings
    come last. A one-off heading bigger than all of them (a document title) gets H1 and pushes
    the others down a level; a one-off size between two levels takes the higher one. Headings
    found in the PDF's bookmarks use the bookmark level instead."""
    headings = [e for e in elements if e.kind == "h"]
    counts = Counter(e.size for e in headings if e.size >= body + 0.8)
    min_count = 3 if n_pages >= 10 else 2
    levels = sorted((s for s, n in counts.items() if n >= min_count), reverse=True)[:3] \
        or sorted(counts, reverse=True)[:3]
    offset = 1 if levels and max(counts) > levels[0] else 0
    for e in headings:
        if e.bookmark is not None:
            e.level = outline[e.bookmark][0]
        elif e.size < body + 0.8 or not levels:
            e.level = len(levels) + 1 + offset
        elif e.size > levels[0]:
            e.level = 1
        else:
            e.level = max(i for i, s in enumerate(levels) if s >= e.size - 0.25) + 1 + offset
        e.level = min(e.level, 4)

    # A one-off size on the title page, after the title and before the first real section heading,
    # is a subtitle ("2026 Analysis of ..."): keep it as text, not as a chapter heading.
    first_section = next((i for i, e in enumerate(elements) if e.kind == "h" and e.size in levels), len(elements))
    title = next((e for e in elements[:first_section] if e.kind == "h" and e.level == 1), None)
    for e in elements[:first_section]:
        if (title and e.kind == "h" and e is not title and e.page == title.page and e.bookmark is None
                and e.size >= body + 0.8 and counts[e.size] < min_count):
            e.kind = "p"


# --- Output -------------------------------------------------------------------
CSS = """
body { font-family: sans-serif; font-size: 11pt; line-height: 1.45; color: #222; }
h1 { font-size: 20pt; margin: 20pt 0 8pt 0; color: #111; }
h2 { font-size: 15pt; margin: 16pt 0 6pt 0; color: #111; }
h3 { font-size: 12.5pt; margin: 12pt 0 4pt 0; color: #111; }
h4 { font-size: 11pt; margin: 10pt 0 4pt 0; color: #111; }
p { margin: 0 0 8pt 0; }
p.bullet { margin: 0 0 4pt 14pt; }
sup { font-size: 7pt; }
table { border-collapse: collapse; margin: 2pt 0 12pt 0; width: 100%; }
th, td { border: 0.5pt solid #9aa5b1; padding: 3pt 5pt; text-align: left; vertical-align: top;
         font-size: 9.5pt; line-height: 1.3; }
th { background-color: #eef3fa; font-weight: bold; }
"""


def table_html(rows: list[list[str]]) -> str:
    header, *body = rows
    out = ["<table><tr>" + "".join(f"<th>{c}</th>" for c in header) + "</tr>"]
    out += ["<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in body]
    return "".join(out) + "</table>"


def table_markdown(rows: list[list[str]]) -> str:
    def cell(c: str) -> str:
        return html.unescape(c).replace(SOFT_JOIN, "").replace("|", "\\|") or " "
    lines = ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return "\n".join([lines[0], "|" + " --- |" * len(rows[0])] + lines[1:])


def build_html(elements: list[Element]) -> str:
    out = []
    for i, e in enumerate(elements):
        if e.kind == "h":
            out.append(f'<h{e.level} id="h{i}">{e.html}</h{e.level}>')
        elif e.kind == "t":
            out.append(e.html)
        elif BULLET_RE.match(e.html):
            out.append(f'<p class="bullet">• {BULLET_RE.sub("", e.html).strip()}</p>')
        else:
            out.append(f"<p>{e.html}</p>")
    return "\n".join(out)


def build_markdown(elements: list[Element]) -> str:
    out = []
    for e in elements:
        text = html.unescape(e.html)
        if e.kind == "h":
            out.append(f"{'#' * e.level} {re.sub(r'</?sup>', '', text)}")
        elif e.kind == "t":
            out.append(table_markdown(e.rows))
        elif BULLET_RE.match(text):
            out.append("- " + BULLET_RE.sub("", text).strip())
        else:
            out.append(text)
    return "\n\n".join(out) + "\n"


def html_to_pdf(body_html: str, title: str, bookmarks: dict[str, str]) -> bytes:
    page_rect = pymupdf.paper_rect("letter")
    frame = page_rect + (72, 72, -72, -72)        # 1-inch margins
    story = pymupdf.Story(html=body_html, user_css=CSS)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    toc, page_no, more = [], 0, True

    def record_heading(pos):
        if pos.heading and pos.open_close & 1:
            toc.append([pos.heading, bookmarks.get(pos.id, pos.text.strip()), pos.page + 1])

    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(frame)
        story.element_positions(record_heading, {"page": page_no})
        story.draw(device)
        writer.end_page()
        page_no += 1
    writer.close()

    out = pymupdf.open("pdf", buffer.getvalue())
    for page in out:                                # page numbers in the footer
        page.insert_text((page_rect.width / 2 - 20, page_rect.height - 36),
                         f"{page.number + 1} / {out.page_count}", fontsize=8, color=(0.5, 0.5, 0.5))
    level = 0
    for entry in toc:                               # bookmarks may only step down one level at a time
        entry[0] = level = min(entry[0], level + 1)
    out.set_toc(toc)
    out.set_metadata({"title": f"{title} (text only)", "producer": "strip_pdf.py"})
    unligature_text_maps(out)
    return out.tobytes(garbage=3, deflate=True)


def unligature_text_maps(doc) -> None:
    """MuPDF always typesets "fi", "fl", "ffi"... as single ligature glyphs and maps them to the
    characters U+FB01 etc., so text copied or searched in the output reads "ﬁnancial". Point
    those glyphs at the plain letters instead (the page looks the same)."""
    def utf16(code: int) -> str:
        return "".join(f"{ord(c):04x}" for c in TYPO_FIXES.get(code, chr(code)))

    def fix_range(m: re.Match) -> str:      # "<006d> <006e> <fb01>" -> "<006d> <006e> [<00660069> <0066006c>]"
        first, last, start = int(m[1], 16), int(m[2], 16), int(m[3], 16)
        return f"<{m[1]}> <{m[2]}> [{' '.join(f'<{utf16(start + i)}>' for i in range(last - first + 1))}]"

    done = set()
    for xref in range(1, doc.xref_length()):
        kind, value = doc.xref_get_key(xref, "ToUnicode")
        if kind != "xref" or value in done:
            continue
        done.add(value)
        cmap_xref = int(value.split()[0])
        cmap = doc.xref_stream(cmap_xref).decode("latin-1")
        fixed = re.sub(r"^<([0-9a-f]{4})> <([0-9a-f]{4})> <(fb0[0-6])>$", fix_range, cmap, flags=re.I | re.M)
        fixed = re.sub(r"^(<[0-9a-f]{4}>) <(fb0[0-6])>$", lambda m: f"{m[1]} <{utf16(int(m[2], 16))}>",
                       fixed, flags=re.I | re.M)
        if fixed != cmap:
            doc.update_stream(cmap_xref, fixed.encode("latin-1"))


# --- Command line ---------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Make a text-only copy of a PDF (no images or captions).")
    parser.add_argument("pdf", type=Path, help="input PDF")
    parser.add_argument("-o", "--output", type=Path, help="output PDF (default: <input>_text_only.pdf)")
    args = parser.parse_args()

    out_pdf = args.output or args.pdf.with_name(f"{args.pdf.stem}_text_only.pdf")
    try:
        result = convert(args.pdf.read_bytes(), args.pdf.name)
    except ValueError as err:
        sys.exit(f"Error: {err}")
    out_pdf.write_bytes(result.pdf)
    out_pdf.with_suffix(".md").write_text(result.markdown, encoding="utf-8")
    print(f"Wrote {out_pdf} and {out_pdf.with_suffix('.md')}")
    print(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in result.stats.items()))


if __name__ == "__main__":
    main()
