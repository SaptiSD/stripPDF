# PDF Text Extractor (stripPDF)

Turns a PDF (white paper, report, brochure) into a clean, **text-only** copy:

- **Keeps:** body text, section headings (as H1–H4), bullet points, footnote/endnote text.
- **Removes:** photos, charts, diagrams, figure captions and titles, photo credits, chart labels/legends,
  "Source: …" lines, and the running header/footer and page number on every page.
- **Outputs:** a new, simply formatted PDF (with clickable bookmarks for the headings) and a Markdown file.

| File | What it is |
|---|---|
| `strip_pdf.py` | The program. Works on its own from the command line. |
| `app.py` | A small web page (Streamlit) where anyone can upload a PDF and download the result. |
| `requirements.txt` | The two libraries needed: PyMuPDF and Streamlit. |

## Run it on your computer

```bash
pip install -r requirements.txt
```

Command line (writes `report_text_only.pdf` and `report_text_only.md` next to the input):

```bash
python strip_pdf.py report.pdf
```

Web page (opens http://localhost:8501):

```bash
streamlit run app.py
```

## Put it on a website (easiest way: Streamlit Community Cloud, free)

1. **Put the files on GitHub.** Sign in at github.com → **New repository** (e.g. `stripPDF`) → on the empty
   repo page click **uploading an existing file** → drag in `app.py`, `strip_pdf.py`, `requirements.txt`
   (and optionally this README) → **Commit changes**.
2. **Deploy.** Go to https://share.streamlit.io → **Continue with GitHub** → **Create app** →
   **Deploy a public app from GitHub** → pick your repository, branch `main`, main file `app.py` →
   optionally choose a custom address such as `strip-pdf.streamlit.app` → **Deploy**.
3. After a minute or two you get a public link like `https://strip-pdf.streamlit.app`. Share it with
   reviewers: they open it, upload a PDF, and download the text-only version.

To update the site later, edit the files on GitHub; the app redeploys by itself.
Free apps go to sleep after 12 hours with no visitors. The GitHub Action in
`.github/workflows/keep-awake.yml` opens the app in a headless browser every 6 hours so it stays awake
(and wakes it if it did fall asleep). GitHub pauses scheduled actions in a public repo after 60 days with
no commits; if that happens, re-enable it on the repo's **Actions** tab.

*Alternative:* Hugging Face Spaces (huggingface.co/spaces) also hosts Streamlit apps for free without
needing GitHub: create a Space with the **Docker → Streamlit** template and upload the same three files.

## How it decides what to keep

A PDF has no paragraphs or headings inside it, only positioned glyphs. So the program works mainly from
**layout and typography**, not from wording. It reads every character with its font, size, weight, colour and
position, plus the outline of every picture and vector graphic on the page. Then:

1. **Clean the text.** Accents are composed, ligatures (ﬁ → fi) expanded, Word/Wingdings bullets mapped to •,
   and invisible control characters dropped (e.g. the U+0003 InDesign leaves at paragraph ends).
   Letter-spaced display type ("S P A C E") is rebuilt from character positions. PDFs whose fonts have no
   text mapping at all are rejected with a "needs OCR" message.
2. **Find the body text.** The most common font size is the body text. Text that is clearly larger, or
   bold on its own line, is a **heading candidate**.
3. **Drop figure text.** Text on top of a picture or inside a chart that is small or short is a label.
   Small or italic text right next to a picture is a caption. A short bold/coloured line directly above
   a chart is its title. Text in the top/bottom margin that repeats on many pages is a header/footer.
4. **Use the PDF's own bookmarks** when it has them: bookmarked titles become headings at the
   bookmark's level, even when they look like body text. A contents page doesn't fool it, because the
   real heading is the last occurrence near the bookmark's page.
5. **Assemble.** Paragraphs split by a page break or a photo are re-joined, hyphenated line breaks repaired,
   title lines set in mixed sizes merged, a title repeated on the cover and title page kept once, and
   heading sizes ranked into H1–H4 (biggest recurring size = highest level).

Regular expressions are only used for a few text patterns: caption prefixes (`Figure 1:`, `Source:`), bullet
and note markers, page numbers and contents-page dot leaders. Everything else comes from geometry and fonts.
The thresholds are named constants at the top of `strip_pdf.py` if you need to tune them.

The program is split into a **reader** (PDF → a list of headings, paragraphs and list items) and **writers**
(that list → PDF, Markdown, HTML). Supporting another output (e.g. Word) means adding a writer; another input
(e.g. .docx or HTML, where headings are explicit styles/tags) means adding a reader that produces the same list.

## Tests

`tests/test_strip_pdf.py` builds small PDFs on the fly (no sample files needed) and checks text clean-up,
headings from typography and from bookmarks, contents pages, figure captions, running headers/footers,
letter-spaced titles, and that the output PDF's text copies and searches cleanly. Run them before changing
`strip_pdf.py`:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

```bash
python -m pytest
```

GitHub runs the same tests on every push (`.github/workflows/tests.yml`).

## Known limitations

- **Scanned PDFs** (pages that are just pictures, no selectable text) produce an error: they need OCR first.
- **Tables** keep their text, but cell by cell, not as a table. Small-print tables drawn inside boxes may
  be treated as chart text and dropped.
- Text that is **part of an image** (e.g. a chart exported as a picture) was never text, so it can't be kept.
- Reading order follows the order stored in the PDF, which is right for most reports and two-column papers
  but can be off for heavily designed layouts.
- PyMuPDF is licensed under the AGPL. That is fine for an internal or review site; for a commercial
  product, check the license or buy a commercial PyMuPDF license.
