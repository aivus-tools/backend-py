"""Tests for the Wix attachment import task and remote-download guards."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aivus_backend.projects import attachments
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefAttachment
from aivus_backend.projects.models import ChatMessage
from aivus_backend.projects.tasks import import_wix_attachments_task


@pytest.fixture
def brief_with_message(db) -> Brief:
    brief = Brief.objects.create(client=None, anonymous_token="tok-import")
    ChatMessage.objects.create(
        brief=brief,
        user=None,
        anonymous_token="tok-import",
        role="user",
        content="hello",
    )
    return brief


@pytest.mark.django_db
def test_import_task_attaches_downloaded_files(brief_with_message):
    specs = [
        {"url": "https://static.wixstatic.com/a.pdf", "filename": "a.pdf"},
        {"url": "https://static.wixstatic.com/b.png", "filename": "b.png"},
    ]
    with patch(
        "aivus_backend.projects.tasks.download_remote_file",
        return_value=(b"%PDF-1.4 data", "application/pdf"),
    ):
        result = import_wix_attachments_task(str(brief_with_message.id), specs)

    assert result == {"imported": 2}
    created = BriefAttachment.objects.filter(brief=brief_with_message)
    assert created.count() == 2
    first_message = brief_with_message.chat_messages.get(role="user")
    assert all(a.message_id == first_message.id for a in created)


@pytest.mark.django_db
def test_import_task_skips_failed_downloads(brief_with_message):
    specs = [
        {"url": "https://static.wixstatic.com/ok.pdf", "filename": "ok.pdf"},
        {"url": "https://static.wixstatic.com/bad.exe", "filename": "bad.exe"},
    ]

    def fake_download(url, **_kwargs):
        if url.endswith("ok.pdf"):
            return (b"%PDF-1.4", "application/pdf")
        return None

    with patch(
        "aivus_backend.projects.tasks.download_remote_file", side_effect=fake_download
    ):
        result = import_wix_attachments_task(str(brief_with_message.id), specs)

    assert result == {"imported": 1}
    assert BriefAttachment.objects.filter(brief=brief_with_message).count() == 1


@pytest.mark.django_db
def test_import_task_no_message_returns_zero(db):
    brief = Brief.objects.create(client=None, anonymous_token="tok-empty")
    result = import_wix_attachments_task(str(brief.id), [{"url": "x", "filename": "y"}])
    assert result == {"imported": 0}


def test_download_rejects_non_http_scheme():
    result = attachments.download_remote_file(
        "ftp://static.wixstatic.com/file.pdf",
        allowed_host_suffixes=attachments.WIX_FILE_HOST_SUFFIXES,
    )
    assert result is None


def test_download_rejects_disallowed_host():
    result = attachments.download_remote_file(
        "https://evil.example.com/file.pdf",
        allowed_host_suffixes=attachments.WIX_FILE_HOST_SUFFIXES,
    )
    assert result is None
