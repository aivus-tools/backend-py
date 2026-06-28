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

# Localized display titles for final-document covers/filenames. The document body
# itself is generated/translated in the brief language by the LLM; only these
# fixed labels need a lookup. Falls back to English for languages not listed.
_DOCUMENT_TITLE_BY_LANG: dict[str, dict[str, str]] = {
    "production_brief": {
        "en": "Production Brief",
        "ru": "Производственный бриф",
        "es": "Brief de Producción",
        "fr": "Brief de Production",
        "de": "Produktions-Briefing",
        "it": "Brief di Produzione",
        "pt": "Briefing de Produção",
        "zh": "制作简报",
        "ja": "制作ブリーフ",
        "ko": "프로덕션 브리프",
    },
    "vendor_email": {
        "en": "Vendor Outreach Email",
        "ru": "Письмо подрядчикам",
        "es": "Correo para Proveedores",
        "fr": "E-mail aux Prestataires",
        "de": "Anschreiben an Dienstleister",
        "it": "Email per i Fornitori",
        "pt": "E-mail para Fornecedores",
        "zh": "供应商邀约邮件",
        "ja": "ベンダー向けメール",
        "ko": "벤더 발송 이메일",
    },
    "deliverables_checklist": {
        "en": "Deliverables Checklist",
        "ru": "Чек-лист поставки",
        "es": "Lista de Entregables",
        "fr": "Liste des Livrables",
        "de": "Liste der Liefergegenstände",
        "it": "Elenco dei Deliverable",
        "pt": "Lista de Entregáveis",
        "zh": "交付物清单",
        "ja": "納品物チェックリスト",
        "ko": "산출물 체크리스트",
    },
}


def document_title_for(kind: str, language: str = "") -> str:
    """Localized display title for a final-document kind. Falls back to English
    for unknown languages and to a generic label for unknown kinds."""
    by_lang = _DOCUMENT_TITLE_BY_LANG.get(kind)
    if not by_lang:
        return "Brief Document"
    return by_lang.get((language or "").lower()) or by_lang["en"]


def _build_pdf_html(document: BriefFinalDocument) -> str:
    brief = document.brief
    language = brief.document_language or "en"
    title = document_title_for(document.kind, language)
    project_name = brief.title or "Creative Brief"
    created = brief.created_at.strftime("%B %d, %Y") if brief.created_at else ""

    return f"""<!DOCTYPE html>
<html lang="{language}">
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
