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

The program reads every line of text with its font size, weight and position, plus the position of every
picture and vector graphic on the page, then applies a few rules:

1. The most common font size is the **body text**. Text that is clearly larger, or bold on its own line,
   is a **heading**; the recurring heading sizes become heading levels (biggest = H1).
2. Text **on top of a picture or inside a chart** that is small or short is a label → removed.
3. Small or italic text **right next to a picture** is a caption → removed. So is a short bold/colored
   line directly above a chart (the chart title), and any line starting with `Figure 1:`, `Source:`,
   `Credit:`, `Photo:` etc.
4. Text in the top/bottom margin that **repeats on many pages** is a header/footer → removed.
5. Paragraphs split by a page break or a photo are joined back together; hyphenated line breaks are repaired.

The thresholds are named constants at the top of `strip_pdf.py` if you need to tune them.

## Known limitations

- **Scanned PDFs** (pages that are just pictures, no selectable text) produce an error: they need OCR first.
- **Tables** keep their text, but cell by cell, not as a table. Small-print tables drawn inside boxes may
  be treated as chart text and dropped.
- Text that is **part of an image** (e.g. a chart exported as a picture) was never text, so it can't be kept.
- Reading order follows the order stored in the PDF, which is right for most reports and two-column papers
  but can be off for heavily designed layouts.
- PyMuPDF is licensed under the AGPL. That is fine for an internal or review site; for a commercial
  product, check the license or buy a commercial PyMuPDF license.
