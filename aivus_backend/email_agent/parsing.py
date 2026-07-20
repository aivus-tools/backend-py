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


_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "article",
        "section",
        "header",
        "footer",
        "hr",
        "table",
    }
)
_DROP_TAGS = frozenset({"script", "style", "head", "meta", "title"})
_WS_INLINE_RE = re.compile(r"[ \t]+")
_WS_LEADING_RE = re.compile(r"\n[ \t]+")
_WS_TRAILING_RE = re.compile(r"[ \t]+\n")
_WS_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _emit_html_text(element, parts: list[str]) -> None:
    tag = element.tag if isinstance(element.tag, str) else None
    if tag == "br":
        parts.append("\n")
        if element.tail:
            parts.append(element.tail)
        return
    is_block = tag in _BLOCK_TAGS
    if is_block:
        parts.append("\n")
    if element.text:
        parts.append(element.text)
    for child in element:
        _emit_html_text(child, parts)
    if is_block:
        parts.append("\n")
    if element.tail:
        parts.append(element.tail)


def html_to_text(html: str) -> str:
    """HTML → text with block-level line breaks preserved.

    ``lxml``'s built-in ``text_content()`` concatenates all text nodes with no
    separator, so ``<p>hello</p><p>world</p>`` collapses to ``helloworld`` and
    the vendor sees the whole email as one wall of text. Walk the tree manually
    so paragraphs, list items and ``<br>`` become real newlines the way a mail
    client would render them. Whitespace is then normalised so runs of blank
    lines from prettified HTML do not blow up the preview.
    """
    if not html or not html.strip():
        return ""
    try:
        document = lxml_html.fromstring(html)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return ""

    for bad in list(document.iter(*_DROP_TAGS)):
        bad.drop_tree()

    parts: list[str] = []
    _emit_html_text(document, parts)
    text = "".join(parts)
    text = _WS_INLINE_RE.sub(" ", text)
    text = _WS_LEADING_RE.sub("\n", text)
    text = _WS_TRAILING_RE.sub("\n", text)
    text = _WS_BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


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
