"""Safety guardrails for the email agent (Stage 3).

Security lives here in code, not in the prompt. An inbound email is untrusted
input, so the guarantees that matter are structural:

- anti-loop / self-detection so the agent never ping-pongs with autoresponders,
  bounces, mailing lists, or its own outbound mail (S3-17);
- recipient pinning + an action allowlist + content-as-data framing so a prompt
  injection cannot redirect a reply or trigger an unsafe action (S3-18);
- a per-thread rate cap as a hard backstop (S3-19).
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

from aivus_backend.email_agent.models import EmailDirection

if TYPE_CHECKING:
    from aivus_backend.email_agent.models import EmailThread


AGENT_HEADER = "x-aivus-agent"

BULK_PRECEDENCE = {"bulk", "junk", "list", "auto_reply"}

NO_REPLY_LOCAL_PARTS = {
    "mailer-daemon",
    "postmaster",
    "no-reply",
    "noreply",
    "do-not-reply",
    "donotreply",
    "bounce",
    "bounces",
}

ALLOWED_ACTIONS = frozenset(
    {
        "acknowledge_receipt",
        "send_brief_link",
        "request_brief_fill",
        "request_missing_materials",
        "remind_client_promise",
        "confirm_materials_received",
        "say_producer_will_join",
        "cc_producer",
        "notify_producer",
        "log_event",
    }
)

DEFAULT_THREAD_DAILY_CAP = 5

_URL_RE = re.compile(
    r"(?:https?://|//|\bwww\.)\S+",
    re.IGNORECASE,
)
_REMOTE_IMG_RE = re.compile(
    r"<img\b[^>]*\bsrc\s*=\s*['\"]https?://[^>]*>",
    re.IGNORECASE,
)
_ADDRESS_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DMARC_RE = re.compile(r"^dmarc\s*=\s*(\w+)", re.IGNORECASE)
_HEADER_FROM_RE = re.compile(r"header\.from\s*=\s*([^\s;]+)", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(
    r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)+(?:/\S*)?",
    re.IGNORECASE,
)
_MAILTO_RE = re.compile(r"\bmailto:\S+", re.IGNORECASE)
_LINK_PLACEHOLDER = "[link removed]"


def normalize_headers(raw: dict) -> dict[str, str]:
    """Lower-case header names and coerce values (possibly lists) to strings."""
    normalized: dict[str, str] = {}
    for name, value in (raw or {}).items():
        key = str(name).strip().lower()
        if isinstance(value, (list, tuple)):
            normalized[key] = ", ".join(str(item) for item in value)
        else:
            normalized[key] = str(value)
    return normalized


def _local_part(value: str) -> str:
    match = _ADDRESS_RE.search(value or "")
    address = match.group(0) if match else (value or "")
    return address.split("@", 1)[0].strip().lower()


def is_auto_or_bulk(raw_headers: dict) -> bool:
    """True for autoresponders, mailing lists, bounces, DSNs and no-reply senders."""
    headers = normalize_headers(raw_headers)

    auto_submitted = headers.get("auto-submitted", "").strip().lower()
    if auto_submitted and auto_submitted != "no":
        return True

    precedence = headers.get("precedence", "").strip().lower()
    if precedence in BULK_PRECEDENCE:
        return True

    if any(key.startswith("list-") for key in headers):
        return True

    if "return-path" in headers and headers["return-path"].strip() in {"<>", ""}:
        return True

    content_type = headers.get("content-type", "").lower()
    if "delivery-status" in content_type:
        return True

    return any(
        _local_part(headers.get(field, "")) in NO_REPLY_LOCAL_PARTS
        for field in ("from", "sender", "return-path")
    )


def first_header(raw_headers: dict, name: str) -> str:
    """The topmost occurrence of a header, which is the one our own MX added."""
    for key, value in (raw_headers or {}).items():
        if str(key).strip().lower() != name:
            continue
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else ""
        return str(value)
    return ""


def _dmarc_clause(results: str) -> str | None:
    """The dmarc clause of an Authentication-Results value, or None if absent.

    Clauses are ``;``-separated and the method token leads each one, so the dmarc
    verdict is only read from a clause that actually starts with ``dmarc=``. A
    bare first-match search would be spoofable: an MX echoes the envelope sender
    into the spf clause ahead of dmarc, and ``=`` is legal in a local part, so
    ``MAIL FROM:<dmarc=pass@evil>`` plants a fake ``dmarc=pass`` earlier in the
    string.
    """
    for clause in results.split(";"):
        stripped = clause.strip()
        if _DMARC_RE.match(stripped):
            return stripped
    return None


def is_authenticated_sender(raw_headers: dict, address: str) -> bool:
    """Whether the receiving MX vouched for the From domain (DMARC).

    Only the topmost Authentication-Results counts: it is the one our provider
    stamped on delivery, and anything below it can be forged by the sender.
    Fail-open when the header is missing or carries no dmarc clause — some
    transports do not stamp one, and dropping a real producer reply is worse than
    the alternative. Never used to authorize an action, only to decide whether an
    address may be trusted as an identity.
    """
    results = first_header(raw_headers, "authentication-results")
    if not results:
        return True
    clause = _dmarc_clause(results)
    if clause is None:
        return True
    verdict = _DMARC_RE.match(clause)
    if verdict is None or verdict.group(1).lower() != "pass":
        return False
    domain = (address or "").strip().lower().rpartition("@")[2]
    header_from = _HEADER_FROM_RE.search(clause)
    # A dmarc=pass we cannot bind to a header.from is not proof of anything: a real
    # MX always stamps header.from alongside the verdict, so its absence means an
    # injected or malformed clause. Fail closed here (unlike a wholly missing AR
    # header, which fails open) — this is an identity decision that grants the
    # producer's powers.
    if header_from is None or not domain:
        return False
    return header_from.group(1).strip().lower().strip("<>") == domain


def is_self_message(
    raw_headers: dict,
    vendor_id: str,
    known_out_message_ids: set[str] | None = None,
) -> bool:
    """True when the message is one of our own outbound mails.

    Identity is taken from the ``X-Aivus-Agent`` header and from a match against
    our recorded outbound Message-IDs, never from the From address (unreliable in
    a two-mailbox setup).
    """
    headers = normalize_headers(raw_headers)
    if headers.get(AGENT_HEADER, "").strip() == str(vendor_id):
        return True
    message_id = headers.get("message-id", "").strip()
    return bool(message_id) and message_id in (known_out_message_ids or set())


def should_ignore_inbound(
    raw_headers: dict,
    vendor_id: str,
    known_out_message_ids: set[str] | None = None,
) -> tuple[bool, str]:
    """Return (ignore, reason) for a cheap pre-LLM gate."""
    if is_self_message(raw_headers, vendor_id, known_out_message_ids):
        return True, "self"
    if is_auto_or_bulk(raw_headers):
        return True, "auto_or_bulk"
    return False, ""


def is_action_allowed(action: str) -> bool:
    """True only for the explicit safe-action allowlist; everything else escalates."""
    return action in ALLOWED_ACTIONS


def pin_recipients(
    participants: list[str],
    producer_email: str,
    agent_email: str,
) -> tuple[list[str], list[str]]:
    """Derive To/Cc purely from the pinned thread participants plus the producer.

    The model never supplies a recipient, so an injection cannot redirect the
    reply. To = thread participants except the agent and producer; Cc = producer.
    """
    agent = (agent_email or "").strip().lower()
    producer = (producer_email or "").strip().lower()
    to: list[str] = []
    seen: set[str] = set()
    for raw in participants or []:
        address = (raw or "").strip()
        key = address.lower()
        if not address or key in seen or key in {agent, producer}:
            continue
        seen.add(key)
        to.append(address)
    cc = [producer_email] if producer_email else []
    return to, cc


def wrap_untrusted(body: str) -> tuple[str, str]:
    """Wrap an untrusted email body in a nonce-delimited data block.

    The per-request nonce defeats delimiter spoofing: the body cannot forge the
    closing tag because it does not know the nonce.
    """
    nonce = secrets.token_hex(8)
    wrapped = (
        f'<untrusted_email_data nonce="{nonce}">\n'
        f"{body}\n"
        f'</untrusted_email_data nonce="{nonce}">'
    )
    return nonce, wrapped


def sanitize_outbound(body: str, allowed_urls: tuple[str, ...] = ()) -> str:
    """Strip exfiltration channels from an outbound reply body.

    Removes remote images and neutralizes any URL not on the allowlist (the only
    legitimate link, the brief link, is inserted by code, not by the model).
    Scheme-less ("www.pay-here.example") and protocol-relative ("//evil.example")
    links count: a mail client renders both as clickable, so matching only on
    http(s):// would wave the interesting half of them straight through.
    """
    cleaned = _REMOTE_IMG_RE.sub("", body or "")

    def _replace(match: re.Match[str]) -> str:
        url = match.group(0)
        if any(url.startswith(prefix) for prefix in allowed_urls):
            return url
        return "[link removed]"

    return _URL_RE.sub(_replace, cleaned)


def redact_for_notification(text: str, max_length: int = 300) -> str:
    """Strip every link shape and cap the length of client-derived preview text.

    Producer notifications quote the client's subject, promise text and extracted
    fields, all of which trace back to an untrusted email and go out over Aivus's
    own authenticated domain — so a planted link would arrive looking like Aivus
    asking the producer to click it. Notification lines never carry a legitimate
    link (the only real one is the code-built CTA button), so this is stricter
    than ``sanitize_outbound``: bare domains and ``mailto:`` go too.
    """
    collapsed = " ".join((text or "").split())
    cleaned = _REMOTE_IMG_RE.sub("", collapsed)
    cleaned = _URL_RE.sub(_LINK_PLACEHOLDER, cleaned)
    cleaned = _BARE_DOMAIN_RE.sub(_LINK_PLACEHOLDER, cleaned)
    cleaned = _MAILTO_RE.sub(_LINK_PLACEHOLDER, cleaned)
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3].rstrip() + "..."
    return cleaned


def outbound_count_since(thread: EmailThread, window: timedelta) -> int:
    """Number of outbound messages on the thread within the rolling window."""
    since = timezone.now() - window
    return thread.messages.filter(
        direction=EmailDirection.OUT,
        created_at__gte=since,
    ).count()


def within_thread_rate_cap(
    thread: EmailThread,
    daily_cap: int = DEFAULT_THREAD_DAILY_CAP,
) -> bool:
    """True while the thread is under its rolling 24h outbound cap."""
    return outbound_count_since(thread, timedelta(hours=24)) < daily_cap
