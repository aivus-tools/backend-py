"""IMAP/SMTP mailbox wrapper (Stage 3, app-password transport).

Connects to a vendor mailbox with an app password (no OAuth, no CASA), and
exposes the primitives the poller and sender need: open/test a connection, list
new messages by UID, fetch and parse them, and send a raw message. The mailbox
provider (Gmail today) only decides the host/port; everything else is generic
IMAP/SMTP so other providers slot in later.
"""

from __future__ import annotations

import contextlib
import smtplib
from datetime import date
from datetime import timedelta
from email import message_from_bytes
from email.utils import getaddresses
from email.utils import parseaddr
from typing import TYPE_CHECKING

import imapclient
from django.utils import timezone
from imapclient.exceptions import IMAPClientError

from aivus_backend.email_agent import parsing

if TYPE_CHECKING:
    from email.message import EmailMessage
    from email.message import Message

    from aivus_backend.email_agent.models import EmailAccount


class MailboxError(Exception):
    """A mailbox connection or transfer failure."""


class MailboxAuthError(MailboxError):
    """Login was rejected (wrong app password, IMAP disabled, revoked)."""


PROVIDER_HOSTS = {
    "gmail": {"imap": ("imap.gmail.com", 993), "smtp": ("smtp.gmail.com", 465)},
}

DEFAULT_FOLDER = "INBOX"
RESYNC_DAYS = 14
_AUTH_FAIL = "Mailbox login rejected"
_UNKNOWN_PROVIDER = "Unknown mailbox provider"


def _hosts(account: EmailAccount) -> dict[str, tuple[str, int]]:
    config = PROVIDER_HOSTS.get(account.provider)
    if config is None:
        raise MailboxError(_UNKNOWN_PROVIDER)
    return config


def open_imap(account: EmailAccount) -> imapclient.IMAPClient:
    """Return a logged-in IMAP client for the account."""
    host, port = _hosts(account)["imap"]
    client = imapclient.IMAPClient(host, port=port, ssl=True, timeout=30)
    try:
        client.login(account.email, account.credential)
    except IMAPClientError as error:
        raise MailboxAuthError(_AUTH_FAIL) from error
    return client


def open_smtp(account: EmailAccount) -> smtplib.SMTP_SSL:
    """Return a logged-in SMTP client for the account."""
    host, port = _hosts(account)["smtp"]
    server = smtplib.SMTP_SSL(host, port, timeout=30)
    try:
        server.login(account.email, account.credential)
    except smtplib.SMTPAuthenticationError as error:
        server.close()
        raise MailboxAuthError(_AUTH_FAIL) from error
    return server


def test_connection(account: EmailAccount) -> None:
    """Verify both IMAP and SMTP login. Raise MailboxAuthError on failure."""
    client = open_imap(account)
    with contextlib.suppress(IMAPClientError):
        client.logout()
    server = open_smtp(account)
    server.quit()


def folder_uidvalidity(client: imapclient.IMAPClient, folder: str) -> str:
    status = client.folder_status(folder, ["UIDVALIDITY"])
    return str(status.get(b"UIDVALIDITY", ""))


def search_new_uids(
    client: imapclient.IMAPClient,
    folder: str,
    last_uid: int,
) -> list[int]:
    """UIDs strictly greater than last_uid in the folder, ascending."""
    client.select_folder(folder, readonly=True)
    uids = client.search(["UID", f"{last_uid + 1}:*"])
    # A "N:*" search always returns at least the highest UID even when nothing is
    # newer, so drop anything not actually past the cursor.
    return sorted(uid for uid in uids if uid > last_uid)


def fetch_raw(client: imapclient.IMAPClient, uids: list[int]) -> dict[int, bytes]:
    if not uids:
        return {}
    response = client.fetch(uids, ["RFC822"])
    return {uid: data[b"RFC822"] for uid, data in response.items() if b"RFC822" in data}


def current_max_uid(client: imapclient.IMAPClient, folder: str) -> int:
    client.select_folder(folder, readonly=True)
    uids = client.search("ALL")
    return max(uids) if uids else 0


def search_since(client: imapclient.IMAPClient, folder: str, since: date) -> list[int]:
    client.select_folder(folder, readonly=True)
    return sorted(client.search(["SINCE", since]))


def plan_sync(account: EmailAccount, client: imapclient.IMAPClient) -> dict:
    """Decide what to ingest for an account and return the parsed new messages.

    First connect seeds the cursor and ignores pre-existing mail. A changed
    UIDVALIDITY forces a bounded (last RESYNC_DAYS) resync instead of the whole
    mailbox. Otherwise fetch UIDs past the stored cursor. Returns a dict with
    mode, uid_validity, last_uid (new cursor), and messages [(uid, parsed)].
    """
    folder = DEFAULT_FOLDER
    validity = folder_uidvalidity(client, folder)

    if not account.uid_validity:
        return {
            "mode": "seed",
            "uid_validity": validity,
            "last_uid": current_max_uid(client, folder),
            "messages": [],
        }

    if account.uid_validity != validity:
        since = (timezone.now() - timedelta(days=RESYNC_DAYS)).date()
        uids = search_since(client, folder, since)
        raw = fetch_raw(client, uids)
        messages = [(uid, parse_raw_message(raw[uid])) for uid in uids if uid in raw]
        return {
            "mode": "reset",
            "uid_validity": validity,
            "last_uid": max(uids) if uids else current_max_uid(client, folder),
            "messages": messages,
        }

    uids = search_new_uids(client, folder, account.last_seen_uid)
    raw = fetch_raw(client, uids)
    messages = [(uid, parse_raw_message(raw[uid])) for uid in uids if uid in raw]
    return {
        "mode": "incremental",
        "uid_validity": validity,
        "last_uid": max(uids) if uids else account.last_seen_uid,
        "messages": messages,
    }


def _collect_headers(message: Message) -> dict:
    """Header map that keeps every occurrence of a repeated header, in order.

    A flat ``dict(message.items())`` silently keeps the LAST duplicate, which is
    the attacker-controlled one: our own MX prepends its Authentication-Results
    at the top, so a forged copy pasted lower down would win. Repeats are kept as
    a list so trust decisions can read the topmost value.
    """
    collected: dict[str, str | list[str]] = {}
    for name, value in message.items():
        key = name.lower()
        existing = collected.get(key)
        if existing is None:
            collected[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            collected[key] = [existing, value]
    return collected


def parse_raw_message(raw: bytes) -> dict:
    """Parse a raw RFC822 message into the fields EmailMessage stores."""
    message = message_from_bytes(raw)
    headers = _collect_headers(message)

    text_body, html_body, attachments = _walk_parts(message)
    body_clean = parsing.clean_body(text=text_body, html=html_body)
    threading = parsing.threading_fields(headers)

    from_name, from_email = parseaddr(message.get("From", ""))
    to_list = [addr for _name, addr in getaddresses(message.get_all("To", []))]
    cc_list = [addr for _name, addr in getaddresses(message.get_all("Cc", []))]

    return {
        "from_email": from_email,
        "from_name": from_name,
        "to_emails": to_list,
        "cc_emails": cc_list,
        "subject": message.get("Subject", ""),
        "body_clean": body_clean,
        "headers": headers,
        "message_id_header": threading["message_id_header"],
        "in_reply_to": threading["in_reply_to"],
        "references": threading["references"],
        "canonical_subject": threading["canonical_subject"],
        "attachments": attachments,
    }


def _walk_parts(message: Message) -> tuple[str, str, list[dict]]:
    text_body = ""
    html_body = ""
    attachments: list[dict] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if disposition == "attachment" or filename:
            raw_payload = part.get_payload(decode=True)
            payload = bytes(raw_payload) if isinstance(raw_payload, bytes) else b""
            attachments.append(
                {
                    "filename": filename or "attachment",
                    "content_type": content_type,
                    "size": len(payload),
                    "payload": payload,
                }
            )
            continue
        if content_type == "text/plain" and not text_body:
            text_body = _decode(part)
        elif content_type == "text/html" and not html_body:
            html_body = _decode(part)
    return text_body, html_body, attachments


def _decode(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def smtp_send_raw(account: EmailAccount, message: EmailMessage) -> str:
    """Send a prepared MIME message via SMTP. Return its Message-ID."""
    server = open_smtp(account)
    try:
        server.send_message(message)
    finally:
        server.quit()
    return message.get("Message-ID", "")
