"""Tests for inbound attachment storage and retention (S3-12/S3-13)."""

from datetime import UTC
from datetime import datetime

import pytest

from aivus_backend.email_agent import attachments
from aivus_backend.email_agent import ingest
from aivus_backend.email_agent import tasks
from aivus_backend.email_agent.models import ActionAssignee
from aivus_backend.email_agent.models import ActionItem
from aivus_backend.email_agent.models import ActionItemStatus
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailAttachment
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftKind
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.projects.models import Brief

pytestmark = pytest.mark.django_db

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
EXE_BYTES = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64
# Real mp4 (ftyp box + moov marker) sniff-checks as video/mp4 in libmagic.
MP4_BYTES = (
    b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    b"\x00\x00\x00\x08moov" + b"\x00" * 512
)


@pytest.fixture
def account(vendor):
    return EmailAccount.objects.create(
        vendor=vendor,
        role=EmailAccountRole.MONITOR,
        email="monitor@vendor.com",
    )


def _thread(vendor):
    return EmailThread.objects.create(
        vendor=vendor,
        provider_thread_id="t1",
        client_email="jane@client.com",
        canonical_subject="New project",
    )


def _message(account, thread):
    return EmailMessage.objects.create(
        account=account,
        thread=thread,
        provider_message_id="<m1@client>",
        direction=EmailDirection.IN,
        from_email="jane@client.com",
    )


def test_store_valid_attachment_anchored_to_message_and_thread(account, vendor):
    thread = _thread(vendor)
    message = _message(account, thread)

    stored = attachments.store_attachments(
        message,
        [
            {
                "filename": "brief.pdf",
                "content_type": "application/pdf",
                "payload": PDF_BYTES,
            }
        ],
    )

    assert stored == 1
    item = EmailAttachment.objects.get(message=message)
    assert item.thread_id == thread.id
    assert item.brief_id is None
    assert item.mime_type == "application/pdf"
    assert item.size_bytes == len(PDF_BYTES)


def test_store_rejects_disguised_executable(account, vendor):
    thread = _thread(vendor)
    message = _message(account, thread)

    stored = attachments.store_attachments(
        message,
        [
            {
                "filename": "invoice.pdf",
                "content_type": "application/pdf",
                "payload": EXE_BYTES,
            }
        ],
    )

    assert stored == 0
    assert not EmailAttachment.objects.exists()
    log = AgentLog.objects.get(thread=thread, event="attachment_dropped")
    assert log.payload["filename"] == "invoice.pdf"
    assert log.payload["reason"] == "disallowed_mime"


def test_store_accepts_video_attachment(account, vendor):
    thread = _thread(vendor)
    message = _message(account, thread)

    stored = attachments.store_attachments(
        message,
        [
            {
                "filename": "reference.mp4",
                "content_type": "video/mp4",
                "payload": MP4_BYTES,
            }
        ],
    )

    assert stored == 1
    item = EmailAttachment.objects.get(message=message)
    assert item.mime_type.startswith("video/")


def test_store_logs_empty_and_oversized_drops(account, vendor):
    thread = _thread(vendor)
    message = _message(account, thread)
    huge = b"%PDF-1.4\n" + b"0" * (attachments.MAX_ATTACHMENT_SIZE_BYTES + 1)

    attachments.store_attachments(
        message,
        [
            {
                "filename": "empty.pdf",
                "content_type": "application/pdf",
                "payload": b"",
            },
            {"filename": "big.pdf", "content_type": "application/pdf", "payload": huge},
        ],
    )

    reasons = set(
        AgentLog.objects.filter(thread=thread, event="attachment_dropped").values_list(
            "payload__reason", flat=True
        )
    )
    assert reasons == {"empty_payload", "oversized"}


def test_store_rejects_oversized_payload(account, vendor):
    thread = _thread(vendor)
    message = _message(account, thread)
    huge = b"%PDF-1.4\n" + b"0" * (attachments.MAX_ATTACHMENT_SIZE_BYTES + 1)

    stored = attachments.store_attachments(
        message,
        [{"filename": "big.pdf", "content_type": "application/pdf", "payload": huge}],
    )

    assert stored == 0


def test_ingest_stores_attachment_without_a_brief(account, vendor):
    parsed = {
        "message_id_header": "<m2@client>",
        "from_email": "jane@client.com",
        "to_emails": ["agent@vendor.com"],
        "cc_emails": [],
        "subject": "New project",
        "canonical_subject": "New project",
        "body_clean": "See attached.",
        "headers": {},
        "attachments": [
            {
                "filename": "brief.pdf",
                "content_type": "application/pdf",
                "payload": PDF_BYTES,
            }
        ],
    }

    message = ingest.ingest_parsed(account, parsed, uid=5)

    assert message is not None
    assert EmailAttachment.objects.filter(message=message).count() == 1


def test_link_thread_attachments_to_brief(account, vendor):
    thread = _thread(vendor)
    message = _message(account, thread)
    attachments.store_attachments(
        message,
        [
            {
                "filename": "brief.pdf",
                "content_type": "application/pdf",
                "payload": PDF_BYTES,
            }
        ],
    )
    brief = Brief.objects.create(document_language="en")

    linked = attachments.link_thread_attachments(thread.id, brief)

    assert linked == 1
    assert EmailAttachment.objects.get(message=message).brief_id == brief.id


def test_purge_old_messages_deletes_past_the_window(account, vendor):
    thread = _thread(vendor)
    old = _message(account, thread)
    attachments.store_attachments(
        old,
        [
            {
                "filename": "brief.pdf",
                "content_type": "application/pdf",
                "payload": PDF_BYTES,
            }
        ],
    )
    stored_name = EmailAttachment.objects.get(message=old).file.name
    storage = EmailAttachment.objects.get(message=old).file.storage
    assert storage.exists(stored_name)
    EmailMessage.objects.filter(id=old.id).update(
        created_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    fresh = EmailMessage.objects.create(
        account=account,
        thread=thread,
        provider_message_id="<fresh@client>",
        direction=EmailDirection.IN,
        from_email="jane@client.com",
    )

    deleted = tasks.purge_old_messages()

    assert deleted >= 1
    assert not EmailMessage.objects.filter(id=old.id).exists()
    assert EmailMessage.objects.filter(id=fresh.id).exists()
    assert not EmailAttachment.objects.filter(message_id=old.id).exists()
    assert not storage.exists(stored_name)


def test_purge_keeps_messages_referenced_by_open_action_items(account, vendor):
    thread = _thread(vendor)
    old = _message(account, thread)
    EmailMessage.objects.filter(id=old.id).update(
        created_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="Send updated brief",
        status=ActionItemStatus.OPEN,
        source_message=old,
    )

    tasks.purge_old_messages()

    assert EmailMessage.objects.filter(id=old.id).exists()


def test_purge_keeps_messages_referenced_by_pending_drafts(account, vendor):
    thread = _thread(vendor)
    old = _message(account, thread)
    EmailMessage.objects.filter(id=old.id).update(
        created_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    OutboundDraft.objects.create(
        thread=thread,
        in_reply_to_message=old,
        kind=OutboundDraftKind.FIRST_REPLY,
        body="Drafted reply",
        status=OutboundDraftStatus.PENDING,
    )

    tasks.purge_old_messages()

    assert EmailMessage.objects.filter(id=old.id).exists()


def test_purge_removes_gcs_blob_via_signal_on_direct_delete(account, vendor):
    thread = _thread(vendor)
    message = _message(account, thread)
    attachments.store_attachments(
        message,
        [
            {
                "filename": "brief.pdf",
                "content_type": "application/pdf",
                "payload": PDF_BYTES,
            }
        ],
    )
    item = EmailAttachment.objects.get(message=message)
    name = item.file.name
    storage = item.file.storage
    assert storage.exists(name)

    item.delete()

    assert not storage.exists(name)
