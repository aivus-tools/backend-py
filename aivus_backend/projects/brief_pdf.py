"""PDF rendering for final brief documents."""

import logging

import weasyprint

from aivus_backend.projects.models import BriefFinalDocument

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

h1 {
    font-size: 20px;
    font-weight: 700;
    color: #111827;
    margin: 24px 0 12px 0;
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

DOCUMENT_TITLE_BY_KIND = {
    "production_brief": "Production Brief",
    "vendor_email": "Vendor Outreach Email",
    "deliverables_checklist": "Deliverables Checklist",
}


def _build_pdf_html(document: BriefFinalDocument) -> str:
    brief = document.brief
    title = DOCUMENT_TITLE_BY_KIND.get(document.kind, "Brief Document")
    project_name = brief.title or "Creative Brief"
    created = brief.created_at.strftime("%B %d, %Y") if brief.created_at else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><style>{PDF_CSS}</style></head>
<body>
  <div class="cover">
    <div class="cover-brand">AIVUS</div>
    <h1>{project_name}</h1>
    <div class="cover-subtitle">{title}</div>
    <div class="cover-meta">{created}</div>
  </div>
  {document.html}
</body>
</html>"""


def render_final_document_pdf(document: BriefFinalDocument) -> bytes:
    html_string = _build_pdf_html(document)
    return weasyprint.HTML(string=html_string).write_pdf()
