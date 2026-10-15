"""Regression tests for strip_pdf.py.

Every PDF used here is generated on the fly with PyMuPDF, so the suite needs no sample files.
Run from the project folder with:  python -m pytest
"""
from __future__ import annotations

import re
import unicodedata

import pymupdf
import pytest

import strip_pdf
from strip_pdf import clean_text, convert

LETTER = pymupdf.paper_rect("letter")
LEFT, RIGHT, BOTTOM = 72, LETTER.width - 72, LETTER.height - 72

PARAGRAPHS = [
    "The society met eleven times this year, usually on the first Tuesday of the month in the old "
    "schoolhouse. Attendance was steady, and most meetings ran a little over an hour, with tea served "
    "afterwards for anyone who wanted to stay and talk.",
    "Volunteers repaired the north wall of the kitchen garden after the winter storms loosened several "
    "courses of brick. The work took three weekends and was finished before the fruit trees came into "
    "blossom, which saved the espaliered pears from further damage.",
    "Membership grew by forty households, mostly families who had visited during the open days. Each new "
    "member received a seed packet, a map of the grounds and an invitation to the autumn lecture series "
    "held in the orangery.",
    "Income from plant sales covered the cost of the new greenhouse glazing for the second year running. "
    "A small grant from the parish council paid for a ramp at the main gate, so that visitors using "
    "wheelchairs can now reach the lower lawns.",
    "The spring fair drew the largest crowd on record despite a cold wind in the morning. Stalls sold out "
    "of tomato seedlings by noon, and the trail through the woodland was so popular that it will become a "
    "permanent feature.",
    "Soil samples were taken from twelve beds in March and again in September. The results show that the "
    "raised beds hold moisture better than the old borders, although they need more compost each year to "
    "keep their structure.",
    "Visitor numbers peaked in July, when the rose walk was in full flower and the evening concerts brought "
    "in people from neighbouring towns. Numbers fell sharply in November, as they do every year once the "
    "clocks change.",
    "Next year the committee plans to restore the glasshouse boiler, replant the herb garden along its "
    "original lines and record the memories of members who have worked in the garden for more than "
    "thirty years.",
]


# --- Building test PDFs ---------------------------------------------------------
class Writer:
    """Lays out text blocks top to bottom in a one-column page, like a word processor would."""

    def __init__(self, doc: pymupdf.Document):
        self.doc = doc
        self.new_page()

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=LETTER.width, height=LETTER.height)
        self.y = 72

    def add(self, text: str, size: float = 11, font: str = "helv", space_after: float | None = None) -> None:
        spare = self.page.insert_textbox(pymupdf.Rect(LEFT, self.y, RIGHT, BOTTOM), text, fontsize=size, fontname=font)
        assert spare >= 0, f"test text doesn't fit on the page: {text[:30]!r}"
        self.y = BOTTOM - spare + (size if space_after is None else space_after)


def running_header_footer(doc: pymupdf.Document) -> None:
    for page in doc:
        page.insert_text((LEFT, 40), "NORTHWIND GARDEN SOCIETY", fontname="helv", fontsize=8)
        page.insert_text((LEFT, LETTER.height - 32), f"Annual Review 2026 | page {page.number + 1}",
                         fontname="helv", fontsize=8)


def bookmarked_pdf(with_outline: bool = True) -> bytes:
    """Headings set exactly like the body text (only the PDF's outline marks them), after a
    contents page that lists the same titles."""
    sections = [(1, "Introduction", 1), (2, "Scope", 1), (1, "Methods", 2), (2, "Sampling", 2), (1, "Results", 3)]
    doc = pymupdf.open()
    w = Writer(doc)
    w.add("Contents", space_after=20)
    for _, title, _ in sections:
        w.add(title, space_after=20)
    paragraphs = iter(PARAGRAPHS)
    for _, title, page in sections:
        if page == doc.page_count:
            w.new_page()
        w.add(title)
        w.add(next(paragraphs))
    if with_outline:
        doc.set_toc([[level, title, page + 1] for level, title, page in sections])
    return doc.tobytes()


def typographic_pdf() -> bytes:
    """No bookmarks: a title set in two sizes on the cover and again on the title page, then bold
    16 pt section headings and bold body-size sub-headings."""
    doc = pymupdf.open()
    w = Writer(doc)
    for page in ("cover", "title page"):
        if page == "title page":
            w.new_page()
        w.add("Annual Review of the", size=14, font="hebo", space_after=0)
        w.add("Northwind Garden Society", size=28, font="hebo")
    paragraphs = iter(PARAGRAPHS)
    for i, (heading, subheading) in enumerate([("Membership", "New members"), ("Finances", "Grants"),
                                               ("Events", "Spring fair")]):
        if i == 2:
            w.new_page()
        w.add(heading, size=16, font="hebo", space_after=8)
        w.add(next(paragraphs))
        w.add(subheading, font="hebo", space_after=6)
        w.add(next(paragraphs))
    return doc.tobytes()


FIGURE_TITLE = "Monthly visitors"
IMAGE_LABEL = "Rose walk opens"
PHOTO_NOTE = "Taken from the east terrace on a July evening."
CAPTION = "Figure 1: Visitors per month in the walled garden, 2026"


def figure_pdf() -> bytes:
    """Three report pages with a running header and footer. Page 1 has a photo between two body
    paragraphs, with a bold title above it, a label printed on it, and two lines under it: a small
    italic note and a body-size "Figure 1:" caption. Each is removed by a different rule."""
    doc = pymupdf.open()
    w = Writer(doc)
    w.add("Visitors", size=16, font="hebo", space_after=8)
    w.add(PARAGRAPHS[6], space_after=12)
    w.add(FIGURE_TITLE, font="hebo", space_after=4)
    photo = pymupdf.Rect(LEFT, w.y, LEFT + 300, w.y + 180)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 36), False)
    pixmap.clear_with(170)
    w.page.insert_image(photo, pixmap=pixmap)
    w.page.insert_text((photo.x0 + 10, photo.y0 + 20), IMAGE_LABEL, fontname="helv", fontsize=8)
    w.y = photo.y1 + 4
    w.add(PHOTO_NOTE, size=8, font="heit", space_after=4)
    w.add(CAPTION, space_after=14)
    w.add(PARAGRAPHS[7])
    for heading, paragraphs in [("Grounds", PARAGRAPHS[1:3]), ("Money", PARAGRAPHS[3:5])]:
        w.new_page()
        w.add(heading, size=16, font="hebo", space_after=8)
        for text in paragraphs:
            w.add(text)
    running_header_footer(doc)
    return doc.tobytes()


DIRTY_TITLE = "Annual \ufb01nancial review"                     # U+FB01 "fi" ligature
DIRTY_TEXT = (
    "The \ufb01nancial statements were prepared by the treasurer\u2019s of\ufb01ce and checked by two "
    "trustees. Our non\u2011pro\ufb01t status means that every surplus goes back into the garden, and the "
    "cafe\u0301 by the gate now pays for its own staff.\ufffd Spending on tools, seed and "
    "repairs stayed within the budget agreed in Janu\u200bary, and the reserve fund is unchanged."
)


def dirty_pdf() -> bytes:
    """A page whose text layer has what real typesetting tools leave behind: ligature characters,
    a decomposed accent, a non-breaking hyphen, a zero-width space and an unmappable glyph."""
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    writer = pymupdf.TextWriter(page.rect)
    writer.fill_textbox(pymupdf.Rect(LEFT, 72, RIGHT, 100), DIRTY_TITLE, font=pymupdf.Font("hebo"), fontsize=16)
    writer.fill_textbox(pymupdf.Rect(LEFT, 110, RIGHT, 300), DIRTY_TEXT, font=pymupdf.Font("helv"), fontsize=11)
    writer.write_text(page)
    Writer(doc).add(PARAGRAPHS[0])                            # page 2: ordinary text
    return doc.tobytes()


SPACED_TITLE = "OPEN GARDEN TRUST"


def letter_spaced_pdf() -> bytes:
    """A display title set with wide character spacing (Tc), which extracts as 'O P E N G A R D E N'."""
    doc = pymupdf.open()
    w = Writer(doc)
    w.page.insert_text((LEFT, 100), SPACED_TITLE, fontname="hebo", fontsize=22)
    xref = w.page.get_contents()[-1]
    stream = doc.xref_stream(xref).decode("latin-1")
    doc.update_stream(xref, re.sub(r"\bTf\b", "Tf 7 Tc", stream, count=1).encode("latin-1"))
    w.y = 130
    for text in PARAGRAPHS[:3]:
        w.add(text)
    return doc.tobytes()


# --- Helpers for checking results -------------------------------------------------
def outline(pdf: bytes) -> list[tuple[int, str]]:
    return [(level, title) for level, title, _ in pymupdf.open("pdf", pdf).get_toc()]


def blocks(markdown: str) -> list[str]:
    return markdown.strip().split("\n\n")


def source_text(pdf: bytes) -> str:
    return "".join(page.get_text(flags=pymupdf.TEXT_PRESERVE_LIGATURES) for page in pymupdf.open("pdf", pdf))


def output_text(pdf: bytes) -> str:
    flags = pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE
    return "".join(page.get_text(flags=flags) for page in pymupdf.open("pdf", pdf))


def unclean_chars(text: str) -> set[str]:
    """Characters that must never reach the output: controls, invisible format characters,
    private-use glyphs, U+FFFD and ligature characters (U+FB00-FB06)."""
    return {c for c in text.replace("\n", "")
            if unicodedata.category(c) in ("Cc", "Cf", "Co") or c == "\ufffd" or "\ufb00" <= c <= "\ufb06"}


# --- 1. Character clean-up ----------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("Heading\x03", "Heading"),                              # InDesign's end-of-paragraph control char
    ("\x01Start\x7f", "Start"),
    ("\uf0b7 Item", "\u2022 Item"),                          # Wingdings private-use bullet -> U+2022
    ("\ufb01nancial o\ufb03ce \ufb02ow", "financial office flow"),
    ("cafe\u0301", "caf\u00e9"),                             # e + combining acute -> U+00E9
    ("non\u2011profit", "non-profit"),
    ("zero\u200bwidth", "zerowidth"),                        # zero-width space dropped
    ("\u0645\u200c\u06cc", "\u0645\u200c\u06cc"),            # zero-width non-joiner kept (Persian)
    ("bad\ufffdglyph", "badglyph"),
    ("tab\there", "tab here"),
    ("Plain text stays the same.", "Plain text stays the same."),
])
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


# --- 2. Headings from the PDF's bookmarks ---------------------------------------------
def test_bookmarked_headings_use_outline_levels():
    result = convert(bookmarked_pdf())
    expected = [(1, "Introduction"), (2, "Scope"), (1, "Methods"), (2, "Sampling"), (1, "Results")]
    assert outline(result.pdf) == expected
    headings = [b for b in blocks(result.markdown) if b.startswith("#")]
    assert headings == ["# Introduction", "## Scope", "# Methods", "## Sampling", "# Results"]


def test_contents_page_stays_paragraphs():
    result = convert(bookmarked_pdf())
    # The contents page comes first and repeats the titles; only the later occurrences are headings.
    assert blocks(result.markdown)[:7] == ["Contents", "Introduction", "Scope", "Methods", "Sampling",
                                           "Results", "# Introduction"]


def test_without_bookmarks_body_styled_headings_are_paragraphs():
    # Control: the same PDF without an outline has nothing that looks like a heading.
    result = convert(bookmarked_pdf(with_outline=False))
    assert result.stats["headings"] == 0
    assert outline(result.pdf) == []


# --- Headings from typography alone -------------------------------------------------------
def test_typographic_heading_levels():
    result = convert(typographic_pdf())
    # The two title lines merge into one heading, and the cover's copy of it is dropped.
    assert outline(result.pdf) == [
        (1, "Annual Review of the Northwind Garden Society"),
        (2, "Membership"), (3, "New members"),
        (2, "Finances"), (3, "Grants"),
        (2, "Events"), (3, "Spring fair"),
    ]


# --- 3. Round trip -------------------------------------------------------------------------
@pytest.mark.parametrize("make_pdf", [bookmarked_pdf, typographic_pdf, figure_pdf, dirty_pdf, letter_spaced_pdf])
def test_round_trip_keeps_outline(make_pdf):
    first = convert(make_pdf())
    second = convert(first.pdf)
    assert outline(first.pdf), "the test PDF should produce headings"
    assert outline(second.pdf) == outline(first.pdf)


# --- 4. Clean output text ------------------------------------------------------------------
def test_output_text_is_clean():
    src = dirty_pdf()
    assert {"\ufb01", "\ufffd", "\u200b", "\u2011"} <= set(source_text(src)), "the test PDF should be dirty"

    result = convert(src)
    text = output_text(result.pdf)
    assert not unclean_chars(text)
    assert not unclean_chars(result.markdown)
    assert not any(unclean_chars(title) for _, title in outline(result.pdf))
    assert outline(result.pdf)[0] == (1, "Annual financial review")
    for word in ("financial", "office", "non-profit", "January"):
        assert word in text


def test_words_with_ligatures_are_searchable():
    result = convert(dirty_pdf())
    doc = pymupdf.open("pdf", result.pdf)
    for word in ("financial", "office", "profit"):
        assert any(page.search_for(word) for page in doc), word


def test_output_font_really_uses_ligatures(monkeypatch):
    """Guards the two tests above: they only prove something if the output is typeset with
    ligature glyphs. If this fails, MuPDF stopped using them and unligature_text_maps() may no
    longer be needed."""
    monkeypatch.setattr(strip_pdf, "unligature_text_maps", lambda doc: None)
    assert "\ufb01" in output_text(convert(dirty_pdf()).pdf)


# --- 5. Figures, captions, running headers and footers ---------------------------------------
def test_figure_text_and_page_furniture_removed():
    result = convert(figure_pdf())
    md = result.markdown
    for gone in (FIGURE_TITLE, IMAGE_LABEL, PHOTO_NOTE, "Figure 1", "NORTHWIND GARDEN SOCIETY", "Annual Review 2026"):
        assert gone not in md
    for kept in (PARAGRAPHS[6], PARAGRAPHS[7], *PARAGRAPHS[1:5]):
        assert kept in blocks(md)
    assert outline(result.pdf) == [(1, "Visitors"), (1, "Grounds"), (1, "Money")]
    assert result.stats["images_removed"] == 1
    assert result.stats["figure_text_removed"] == 1           # the label on the photo
    assert result.stats["captions_removed"] == 3              # figure title, note, caption
    assert result.stats["header_footer_removed"] == 6


# --- 6. Letter-spaced display type --------------------------------------------------------
def test_letter_spaced_title_becomes_words():
    src = letter_spaced_pdf()
    assert "O P E N G A R D E N" in " ".join(source_text(src).split()), "the test PDF should be letter-spaced"

    result = convert(src)
    assert blocks(result.markdown)[0] == f"# {SPACED_TITLE}"
    assert outline(result.pdf) == [(1, SPACED_TITLE)]
