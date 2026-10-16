"""Web page for strip_pdf.py: upload a PDF, download a text-only version.

Run locally:  streamlit run app.py
"""
from pathlib import Path

import streamlit as st

from strip_pdf import convert

st.set_page_config(page_title="PDF Text Extractor", page_icon="📄")

PREVIEW_CSS = """<style>
table { border-collapse: collapse; margin: 0.25rem 0 1rem 0; }
th, td { border: 1px solid rgba(128, 128, 128, 0.5); padding: 0.2rem 0.5rem; text-align: left; vertical-align: top; }
th { background: rgba(128, 128, 128, 0.15); }
</style>"""

st.title("PDF Text Extractor")
st.write(
    "Upload a PDF such as a white paper or report. You get back a clean copy with the body text, "
    "section headings and tables, without photos, charts, captions or page headers/footers."
)


@st.cache_data(show_spinner=False, max_entries=20, ttl=3600)
def run(data: bytes, name: str):
    return convert(data, name)


uploaded = st.file_uploader("Choose a PDF", type="pdf")
st.caption("Files are processed in memory and are not saved to disk. "
           "Scanned PDFs (pictures of pages with no text layer) are not supported.")

if uploaded:
    try:
        with st.spinner("Removing images and captions…"):
            result = run(uploaded.getvalue(), uploaded.name)
    except Exception as err:            # ValueError carries a friendly message; anything else is a bad file
        st.error(str(err) if isinstance(err, ValueError) else f"Could not read this PDF ({err}).")
        st.stop()

    s = result.stats
    cols = st.columns(4)
    cols[0].metric("Pages", s["pages"])
    cols[1].metric("Headings kept", s["headings"])
    cols[2].metric("Words kept", f"{s['words']:,}")
    cols[3].metric("Images & charts removed", s["images_removed"] + s["graphics_removed"])
    n_tables = s.get("tables", 0)
    st.caption(f"Kept {n_tables} table{'' if n_tables == 1 else 's'}. "
               f"Also removed: {s.get('captions_removed', 0)} captions/figure titles, "
               f"{s.get('figure_text_removed', 0)} chart labels, "
               f"{s.get('header_footer_removed', 0)} page headers/footers.")

    stem = Path(uploaded.name).stem
    left, right = st.columns(2)
    left.download_button("Download text-only PDF", result.pdf, f"{stem}_text_only.pdf", "application/pdf",
                         on_click="ignore", type="primary", icon=":material/download:", width="stretch")
    right.download_button("Download as Markdown", result.markdown, f"{stem}_text_only.md", "text/markdown",
                          on_click="ignore", icon=":material/download:", width="stretch")

    st.subheader("Preview")
    with st.container(height=600, border=True):
        st.html(PREVIEW_CSS + result.html)
