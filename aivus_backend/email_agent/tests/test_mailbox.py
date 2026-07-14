"""Tests for the IMAP/SMTP mailbox wrapper (mocked, no live server)."""

import smtplib
from email.message import EmailMessage
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from imapclient.exceptions import IMAPClientError

from aivus_backend.email_agent import mailbox
from aivus_backend.email_agent.models import EmailAccount

ACCOUNT = EmailAccount(
    provider="gmail",
    email="agent@gmail.com",
    credential="app-password",
)

IMAP = "aivus_backend.email_agent.mailbox.imapclient.IMAPClient"
SMTP = "aivus_backend.email_agent.mailbox.smtplib.SMTP_SSL"


def test_open_imap_login_failure_raises_auth_error():
    with patch(IMAP) as client_cls:
        client_cls.return_value.login.side_effect = IMAPClientError("nope")
        with pytest.raises(mailbox.MailboxAuthError):
            mailbox.open_imap(ACCOUNT)


def test_unknown_provider_raises():
    account = EmailAccount(provider="outlook", email="x@y.z", credential="p")
    with pytest.raises(mailbox.MailboxError):
        mailbox.open_imap(account)


def test_test_connection_success():
    with patch(IMAP) as imap_cls, patch(SMTP) as smtp_cls:
        mailbox.test_connection(ACCOUNT)
        imap_cls.return_value.login.assert_called_once_with(
            "agent@gmail.com", "app-password"
        )
        smtp_cls.return_value.login.assert_called_once()


def test_test_connection_smtp_auth_failure():
    with patch(IMAP), patch(SMTP) as smtp_cls:
        smtp_cls.return_value.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"bad"
        )
        with pytest.raises(mailbox.MailboxAuthError):
            mailbox.test_connection(ACCOUNT)


def test_search_new_uids_filters_cursor():
    client = MagicMock()
    client.search.return_value = [5, 6, 7]
    assert mailbox.search_new_uids(client, "INBOX", 6) == [7]
    client.search.return_value = [10]
    assert mailbox.search_new_uids(client, "INBOX", 10) == []


def test_fetch_raw_maps_uid_to_bytes():
    client = MagicMock()
    client.fetch.return_value = {7: {b"RFC822": b"raw-bytes"}}
    assert mailbox.fetch_raw(client, [7]) == {7: b"raw-bytes"}
    assert mailbox.fetch_raw(client, []) == {}


def test_parse_raw_message_keeps_repeated_headers_in_order():
    message = EmailMessage()
    message["From"] = "jane@client.com"
    message["Subject"] = "Hi"
    message["Authentication-Results"] = (
        "mx.google.com; dmarc=fail header.from=vendor.com"
    )
    message["Authentication-Results"] = "forged; dmarc=pass header.from=vendor.com"
    message.set_content("hello")

    parsed = mailbox.parse_raw_message(message.as_bytes())

    results = parsed["headers"]["authentication-results"]
    assert isinstance(results, list)
    assert results[0].startswith("mx.google.com")
    assert parsed["headers"]["subject"] == "Hi"


def test_parse_raw_message_extracts_fields_and_attachment():
    message = EmailMessage()
    message["From"] = "Jane <jane@client.com>"
    message["To"] = "agent@vendor.com"
    message["Cc"] = "ivan@vendor.com"
    message["Subject"] = "Re: New project"
    message["Message-ID"] = "<abc@client>"
    message["In-Reply-To"] = "<prev@agent>"
    message["References"] = "<root@client> <prev@agent>"
    message.set_content(
        "Hello, we need a corporate video.\n\nOn Mon Jane wrote:\n> old quoted message"
    )
    message.add_attachment(
        b"PDFDATA", maintype="application", subtype="pdf", filename="brief.pdf"
    )

    parsed = mailbox.parse_raw_message(message.as_bytes())

    assert parsed["from_email"] == "jane@client.com"
    assert parsed["to_emails"] == ["agent@vendor.com"]
    assert parsed["cc_emails"] == ["ivan@vendor.com"]
    assert "Hello, we need a corporate video." in parsed["body_clean"]
    assert "old quoted message" not in parsed["body_clean"]
    assert parsed["canonical_subject"] == "New project"
    assert parsed["message_id_header"] == "<abc@client>"
    assert len(parsed["attachments"]) == 1
    assert parsed["attachments"][0]["filename"] == "brief.pdf"
    assert parsed["attachments"][0]["content_type"] == "application/pdf"


def _raw(message_id: str, body: str) -> bytes:
    message = EmailMessage()
    message["From"] = "jane@client.com"
    message["To"] = "agent@gmail.com"
    message["Subject"] = "New project"
    message["Message-ID"] = message_id
    message.set_content(body)
    return message.as_bytes()


def _client(uidvalidity: int) -> MagicMock:
    client = MagicMock()
    client.folder_status.return_value = {b"UIDVALIDITY": uidvalidity}
    return client


def test_plan_sync_seed_ignores_existing_mail():
    account = EmailAccount(
        provider="gmail", email="a@gmail.com", credential="p", uid_validity=""
    )
    client = _client(200)
    client.search.return_value = [8, 9, 10]

    result = mailbox.plan_sync(account, client)

    assert result["mode"] == "seed"
    assert result["uid_validity"] == "200"
    assert result["last_uid"] == 10
    assert result["messages"] == []


def test_plan_sync_incremental_fetches_past_cursor():
    account = EmailAccount(
        provider="gmail",
        email="a@gmail.com",
        credential="p",
        uid_validity="200",
        last_seen_uid=5,
    )
    client = _client(200)
    client.search.return_value = [6, 7]
    client.fetch.return_value = {
        6: {b"RFC822": _raw("<m6@c>", "Hello six")},
        7: {b"RFC822": _raw("<m7@c>", "Hello seven")},
    }

    result = mailbox.plan_sync(account, client)

    assert result["mode"] == "incremental"
    assert result["last_uid"] == 7
    assert [uid for uid, _msg in result["messages"]] == [6, 7]
    assert result["messages"][0][1]["message_id_header"] == "<m6@c>"


def test_plan_sync_reset_on_uidvalidity_change():
    account = EmailAccount(
        provider="gmail",
        email="a@gmail.com",
        credential="p",
        uid_validity="100",
        last_seen_uid=5,
    )
    client = _client(200)
    client.search.return_value = [6, 7]
    client.fetch.return_value = {
        6: {b"RFC822": _raw("<m6@c>", "recent six")},
        7: {b"RFC822": _raw("<m7@c>", "recent seven")},
    }

    result = mailbox.plan_sync(account, client)

    assert result["mode"] == "reset"
    assert result["uid_validity"] == "200"
    assert result["last_uid"] == 7
    assert len(result["messages"]) == 2


def test_smtp_send_raw_sends_and_returns_message_id():
    message = EmailMessage()
    message["From"] = "agent@gmail.com"
    message["To"] = "jane@client.com"
    message["Message-ID"] = "<out@agent>"
    message.set_content("Thanks for reaching out.")

    with patch(SMTP) as smtp_cls:
        message_id = mailbox.smtp_send_raw(ACCOUNT, message)
        smtp_cls.return_value.send_message.assert_called_once_with(message)
        smtp_cls.return_value.quit.assert_called_once()

    assert message_id == "<out@agent>"
