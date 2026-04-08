import logging

import weasyprint

from aivus_backend.projects.models import BRIEF_SECTION_KEYS
from aivus_backend.projects.models import Brief

logger = logging.getLogger(__name__)

PDF_CSS = """\
@page {
    size: A4;
    margin: 22mm 18mm 25mm 18mm;
    @bottom-center {
        content: "Page " counter(page) " of " counter(pages);
        font-family: sans-serif;
        font-size: 9px;
        color: #9ca3af;
    }
}

body {
    font-family: sans-serif;
    font-size: 11px;
    line-height: 1.6;
    color: #1f2937;
    margin: 0;
    padding: 0;
}

.cover {
    text-align: center;
    padding-top: 140px;
    page-break-after: always;
}

.cover-brand {
    font-size: 13px;
    font-weight: 600;
    color: #6b7280;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 48px;
}

.cover h1 {
    font-size: 28px;
    font-weight: 700;
    color: #1f2937;
    margin: 0 0 8px 0;
}

.cover-subtitle {
    font-size: 14px;
    color: #6b7280;
    margin: 0 0 60px 0;
}

.cover-meta {
    font-size: 12px;
    color: #4b5675;
    line-height: 1.8;
}

.section {
    margin-bottom: 20px;
    page-break-inside: avoid;
}

h2 {
    font-size: 15px;
    font-weight: 700;
    color: #1f2937;
    border-bottom: 2px solid #eef0f4;
    padding-bottom: 6px;
    margin: 28px 0 12px 0;
}

h3 {
    font-size: 13px;
    font-weight: 600;
    color: #374151;
    margin: 16px 0 6px 0;
}

p {
    margin: 0 0 8px 0;
}

ul, ol {
    padding-left: 20px;
    margin: 0 0 8px 0;
}

li {
    margin-bottom: 4px;
}

strong {
    font-weight: 600;
    color: #111827;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
}

th, td {
    border: 1px solid #e5e7eb;
    padding: 6px 10px;
    text-align: left;
    font-size: 10px;
}

th {
    background: #f9fafb;
    font-weight: 600;
}

hr {
    border: none;
    border-top: 1px solid #eef0f4;
    margin: 24px 0;
}

a {
    color: #2563eb;
    text-decoration: none;
}

blockquote {
    border-left: 3px solid #2563eb;
    margin: 8px 0;
    padding: 4px 14px;
    color: #6b7280;
}
"""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_pdf_html(
    brief: Brief, document_sections: dict, structured_data: dict
) -> str:
    structured = structured_data or {}
    project_name = _escape(str(structured.get("projectName", "Creative Brief")))
    client_name = _escape(str(structured.get("clientName", "")))

    sections_html = []
    for key in BRIEF_SECTION_KEYS:
        html = (document_sections or {}).get(key, "")
        if html:
            sections_html.append(f'<div class="section">{html}</div>')

    body = "\n".join(sections_html)

    client_line = ""
    if client_name:
        client_line = f"Prepared for: {client_name}<br/>"

    created = ""
    if brief.created_at:
        created = brief.created_at.strftime("%B %d, %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><style>{PDF_CSS}</style></head>
<body>
  <div class="cover">
    <div class="cover-brand">AIVUS</div>
    <h1>{project_name}</h1>
    <div class="cover-subtitle">Creative Brief</div>
    <div class="cover-meta">
      {client_line}
      {created}
    </div>
  </div>
  {body}
</body>
</html>"""


def render_brief_pdf(
    brief: Brief,
    document_sections: dict | None = None,
    structured_data: dict | None = None,
) -> bytes:
    sections = (
        brief.document_sections if document_sections is None else document_sections
    )
    structured = brief.structured_data if structured_data is None else structured_data
    html_string = _build_pdf_html(brief, sections, structured)
    return weasyprint.HTML(string=html_string).write_pdf()
