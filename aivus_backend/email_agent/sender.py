"""Outbound reply builder and sender (Stage 3).

Builds a correctly threaded MIME reply and sends it over SMTP from the agent
mailbox. Recipients are pinned in code (thread participants plus the producer,
never the model), the body is sanitized, and anti-loop headers plus the
X-Aivus-Agent marker are set so our own mail and autoresponders are recognized.
The monitoring mailbox can never send.
"""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from email.utils import make_msgid
from typing import TYPE_CHECKING

from aivus_backend.email_agent import parsing
from aivus_backend.email_agent import safety
from aivus_backend.email_agent.mailbox import MailboxError
from aivus_backend.email_agent.mailbox import smtp_send_raw
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage

if TYPE_CHECKING:
    from aivus_backend.email_agent.models import EmailAccount
    from aivus_backend.email_agent.models import EmailThread

_REFERENCES_MAX = 2000
_MONITOR_CANNOT_SEND = "Monitoring mailbox must never send"


def _references(parent: EmailMessage | None) -> str:
    if parent is None:
        return ""
    chain = f"{parent.references or ''} {parent.message_id_header or ''}".split()
    joined = " ".join(chain)
    if len(joined) <= _REFERENCES_MAX:
        return joined
    # Keep the tail (most recent ancestors) within the cap.
    kept: list[str] = []
    for message_id in reversed(chain):
        if len(" ".join([message_id, *kept])) > _REFERENCES_MAX:
            break
        kept.insert(0, message_id)
    return " ".join(kept)


def build_reply_mime(  # noqa: PLR0913
    account: EmailAccount,
    thread: EmailThread,
    body: str,
    *,
    producer_email: str,
    parent: EmailMessage | None = None,
    allowed_urls: tuple[str, ...] = (),
) -> MimeMessage:
    """Build a threaded, recipient-pinned, sanitized reply from the agent mailbox."""
    to_addresses, cc_addresses = safety.pin_recipients(
        list(thread.participants or []),
        producer_email,
        account.email,
    )

    canonical = parsing.canonical_subject(thread.canonical_subject)
    subject = f"Re: {canonical}" if canonical else "Re:"

    message = MimeMessage()
    message["From"] = account.email
    message["To"] = ", ".join(to_addresses)
    if cc_addresses:
        message["Cc"] = ", ".join(cc_addresses)
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=account.email.split("@")[-1])
    if parent is not None and parent.message_id_header:
        message["In-Reply-To"] = parent.message_id_header
    references = _references(parent)
    if references:
        message["References"] = references
    message["X-Aivus-Agent"] = str(account.vendor_id)
    message["Auto-Submitted"] = "auto-replied"
    message["X-Auto-Response-Suppress"] = "All"

    message.set_content(safety.sanitize_outbound(body, allowed_urls))
    return message


def send_reply(  # noqa: PLR0913
    account: EmailAccount,
    thread: EmailThread,
    body: str,
    *,
    producer_email: str,
    parent: EmailMessage | None = None,
    allowed_urls: tuple[str, ...] = (),
) -> EmailMessage:
    """Send a reply from the agent mailbox and record it as an outbound message."""
    if account.role != EmailAccountRole.AGENT:
        raise MailboxError(_MONITOR_CANNOT_SEND)

    to_addresses, cc_addresses = safety.pin_recipients(
        list(thread.participants or []),
        producer_email,
        account.email,
    )
    mime = build_reply_mime(
        account,
        thread,
        body,
        producer_email=producer_email,
        parent=parent,
        allowed_urls=allowed_urls,
    )
    message_id = smtp_send_raw(account, mime)

    return EmailMessage.objects.create(
        account=account,
        thread=thread,
        provider_message_id=message_id,
        direction=EmailDirection.OUT,
        from_email=account.email,
        to_emails=to_addresses,
        cc_emails=cc_addresses,
        subject=mime["Subject"],
        body_clean=body,
        message_id_header=message_id,
        in_reply_to=mime.get("In-Reply-To", ""),
        references=mime.get("References", ""),
    )
