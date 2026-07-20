"""Tests for email normalization."""

from aivus_backend.email_agent import parsing


def test_strip_quotes_and_signature_plaintext():
    text = (
        "Thanks, sounds good! We can start next week.\n\n"
        "On Mon, Jan 1, 2026 at 10:00 AM Jane <jane@client.com> wrote:\n"
        "> Original request here\n> more quoted history\n\n"
        "--\nBest regards\nBob\nCEO, Studio"
    )
    cleaned = parsing.clean_body(text=text)
    assert "Thanks, sounds good! We can start next week." in cleaned
    assert "Original request here" not in cleaned
    assert "CEO, Studio" not in cleaned


def test_clean_body_html_drops_quoted_block():
    html = (
        "<div>Hi, we need a corporate video in NYC.<br>Thanks</div>"
        "<blockquote>On Mon Jane wrote:<br>old quoted message</blockquote>"
    )
    cleaned = parsing.clean_body(html=html)
    assert "corporate video in NYC" in cleaned
    assert "old quoted message" not in cleaned


def test_clean_body_empty():
    assert parsing.clean_body() == ""


def test_html_to_text_preserves_block_line_breaks():
    html = (
        "<div><p>First line.</p><p>Second line.</p>"
        "<p>Third line with a <br>hard break.</p></div>"
    )
    text = parsing.html_to_text(html)
    assert "First line." in text
    assert "Second line." in text
    # Adjacent paragraphs must not collapse into a single word run.
    assert "First line.Second line." not in text
    assert "\n" in text
    # <br> inside a paragraph becomes a newline too.
    assert "hard break" in text
    assert "a \nhard break" in text or "a\nhard break" in text


def test_html_to_text_strips_scripts_and_styles():
    html = (
        "<html><head><style>body{color:red}</style></head>"
        "<body><p>Hello</p><script>alert(1)</script></body></html>"
    )
    text = parsing.html_to_text(html)
    assert text.strip().startswith("Hello")
    assert "alert" not in text
    assert "color:red" not in text


def test_canonical_subject_strips_prefixes():
    assert parsing.canonical_subject("Re: Fwd: RE:  New project") == "New project"
    assert parsing.canonical_subject("New project") == "New project"
    assert parsing.canonical_subject("") == ""


def test_threading_fields():
    raw = {
        "Message-ID": "<abc@client>",
        "In-Reply-To": "<prev@agent>",
        "References": "<root@client> <prev@agent>",
        "Subject": "Re: New project",
    }
    fields = parsing.threading_fields(raw)
    assert fields["message_id_header"] == "<abc@client>"
    assert fields["in_reply_to"] == "<prev@agent>"
    assert fields["references"] == "<root@client> <prev@agent>"
    assert fields["canonical_subject"] == "New project"


def test_extract_headers_normalizes_names():
    headers = parsing.extract_headers({"Auto-Submitted": "auto-replied", "X-Y": "z"})
    assert headers["auto-submitted"] == "auto-replied"
    assert headers["x-y"] == "z"
