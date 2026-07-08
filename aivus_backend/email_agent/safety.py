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

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_REMOTE_IMG_RE = re.compile(
    r"<img\b[^>]*\bsrc\s*=\s*['\"]https?://[^>]*>",
    re.IGNORECASE,
)
_ADDRESS_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


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
    """
    cleaned = _REMOTE_IMG_RE.sub("", body or "")

    def _replace(match: re.Match[str]) -> str:
        url = match.group(0)
        if any(url.startswith(prefix) for prefix in allowed_urls):
            return url
        return "[link removed]"

    return _URL_RE.sub(_replace, cleaned)


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
