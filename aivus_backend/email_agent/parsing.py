"""Email normalization (Stage 3).

Strip quoted history and signatures so the model sees only the latest message,
and pull the headers needed for anti-loop and thread stitching. Plain text is the
primary path (mail-parser-reply, which also strips signatures); HTML is unwrapped
with quotequail, converted to text, then run through the same signature stripper.
"""

from __future__ import annotations

import re

import quotequail
from lxml import etree
from lxml import html as lxml_html
from mailparser_reply import EmailReplyParser

from aivus_backend.email_agent.safety import normalize_headers

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(re|fwd?|aw|rv)\s*:\s*", re.IGNORECASE)
_DEFAULT_LANGUAGES = ("en",)


def strip_quotes_and_signature(text: str, languages: tuple[str, ...] = ()) -> str:
    """Return the latest reply with quoted history and signature removed."""
    if not text or not text.strip():
        return ""
    parser = EmailReplyParser(languages=list(languages or _DEFAULT_LANGUAGES))
    return parser.read(text).latest_reply or ""


def html_to_text(html: str) -> str:
    """Best-effort HTML-to-text extraction."""
    if not html or not html.strip():
        return ""
    try:
        document = lxml_html.fromstring(html)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return ""
    return document.text_content()


def _html_top_reply(html: str) -> str:
    segments = quotequail.quote_html(html)
    top = "".join(fragment for is_top, fragment in segments if is_top)
    return top or html


def clean_body(text: str = "", html: str = "", languages: tuple[str, ...] = ()) -> str:
    """Return the clean latest message, preferring the plain-text part."""
    if text and text.strip():
        return strip_quotes_and_signature(text, languages)
    if html and html.strip():
        top_reply = _html_top_reply(html)
        return strip_quotes_and_signature(html_to_text(top_reply), languages)
    return ""


def canonical_subject(subject: str) -> str:
    """Strip repeated Re:/Fwd: prefixes for stable thread stitching."""
    result = (subject or "").strip()
    while True:
        stripped = _SUBJECT_PREFIX_RE.sub("", result, count=1).strip()
        if stripped == result:
            return result
        result = stripped


def extract_headers(raw_headers: dict) -> dict[str, str]:
    """Normalize headers (lower-cased names) for storage and anti-loop checks."""
    return normalize_headers(raw_headers)


def threading_fields(raw_headers: dict) -> dict[str, str]:
    """Pull the RFC threading fields plus a canonical subject from headers."""
    headers = normalize_headers(raw_headers)
    return {
        "message_id_header": headers.get("message-id", ""),
        "in_reply_to": headers.get("in-reply-to", ""),
        "references": headers.get("references", ""),
        "canonical_subject": canonical_subject(headers.get("subject", "")),
    }
