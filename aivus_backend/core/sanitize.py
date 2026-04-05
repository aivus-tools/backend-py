import nh3

ALLOWED_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "ul",
    "ol",
    "li",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "a",
    "div",
    "span",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "blockquote",
    "pre",
    "code",
    "img",
    "sub",
    "sup",
}

ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "*": {"class", "id"},
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height"},
    "div": {"data-section"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}


def sanitize_html(html: str) -> str:
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
    )


def sanitize_sections(sections: dict[str, str]) -> dict[str, str]:
    return {key: sanitize_html(value) for key, value in sections.items()}
