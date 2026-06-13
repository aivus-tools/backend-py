"""Tests for the personal-link email path (Stage 2 S2-8)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail
from django.test import override_settings

from aivus_backend.core.enums import FinalDocumentKind
from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects import brief_emails
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefFinalDocument
from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.tasks import send_to_recipient_email


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_send_to_recipient_email_renders_and_attaches():
    mail.outbox = []
    send_to_recipient_email.run(
        recipient_email="lead@example.com",
        template="emails/brief_sent_client_en.html",
        subject="Your brief is ready",
        context={
            "vendor_name": "Acme",
            "recipient_email": "lead@example.com",
            "register_url": "https://go.aivus.co/app/brief/claim/x",
            "share_url": "https://go.aivus.co/shared-brief/tok",
            "frontend_url": "https://go.aivus.co",
            "is_existing_account": False,
        },
        attachments=[("Brief.pdf", "JVBERi0=", "application/pdf")],
    )
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["lead@example.com"]
    assert "Acme" in message.body
    assert len(message.attachments) == 1
    assert message.attachments[0][0] == "Brief.pdf"


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_send_to_recipient_email_empty_recipient_noop():
    mail.outbox = []
    send_to_recipient_email.run(
        recipient_email="",
        template="emails/brief_sent_client_en.html",
        subject="x",
        context={},
    )
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_resolve_email_language_prefers_document_language():
    brief = Brief.objects.create(client=None, document_language="ru")
    assert brief_emails.resolve_email_language(brief) == "ru"


@pytest.mark.django_db
def test_resolve_email_language_falls_back_to_accept_language():
    brief = Brief.objects.create(client=None)
    assert brief_emails.resolve_email_language(brief, "ru-RU,ru;q=0.9") == "ru"


@pytest.mark.django_db
def test_resolve_email_language_default_en():
    brief = Brief.objects.create(client=None)
    assert brief_emails.resolve_email_language(brief) == "en"


@pytest.mark.django_db
def test_client_email_new_account_uses_recipient_task():
    brief = Brief.objects.create(
        client=None, anonymous_token="tok-new", document_language="en"
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind=FinalDocumentKind.PRODUCTION_BRIEF, html="<p>hi</p>"
    )

    with (
        patch("aivus_backend.users.tasks.send_to_recipient_email.delay") as anon_mock,
        patch("aivus_backend.users.tasks.send_templated_email.delay") as auth_mock,
        patch(
            "aivus_backend.projects.brief_emails._brief_pdf_attachment",
            return_value=("Brief.pdf", "JVBERi0=", "application/pdf"),
        ),
    ):
        brief_emails.send_client_lead_email(
            brief, "fresh@example.com", "share-tok", "en"
        )

    anon_mock.assert_called_once()
    auth_mock.assert_not_called()
    assert anon_mock.call_args.kwargs["attachments"]


@pytest.mark.django_db
@override_settings(FRONTEND_URL="https://go.aivus.co")
def test_vendor_lead_email_links_to_dashboard_project():
    owner = User.objects.create_user(
        email="vendor-owner@example.com",
        password="p@ssw0rd",
        name="Vendor Owner",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="Dash Studio", owner=owner)
    brief = Brief.objects.create(
        client=None, contact_email="lead@example.com", document_language="en"
    )
    project = Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.RFP
    )

    with patch("aivus_backend.users.tasks.send_to_recipient_email.delay") as send_mock:
        brief_emails.send_vendor_lead_email(project, brief, "en")

    send_mock.assert_called_once()
    project_url = send_mock.call_args.kwargs["context"]["project_url"]
    assert project_url == f"https://go.aivus.co/app/dashboard/{project.id}"
    assert "/app/projects/" not in project_url


@pytest.mark.django_db
def test_client_email_existing_account_uses_login_template_with_pdf():
    User.objects.create_user(
        email="known@example.com",
        password="p@ssw0rd",
        name="Known",
        group="CLIENT",
    )
    brief = Brief.objects.create(
        client=None, anonymous_token="tok-known", document_language="en"
    )

    with (
        patch("aivus_backend.users.tasks.send_to_recipient_email.delay") as anon_mock,
        patch("aivus_backend.users.tasks.send_templated_email.delay") as auth_mock,
        patch(
            "aivus_backend.projects.brief_emails._brief_pdf_attachment",
            return_value=("Brief.pdf", "JVBERi0=", "application/pdf"),
        ),
    ):
        brief_emails.send_client_lead_email(
            brief, "known@example.com", "share-tok", "en"
        )

    anon_mock.assert_called_once()
    auth_mock.assert_not_called()
    assert anon_mock.call_args.kwargs["context"]["is_existing_account"] is True
    assert anon_mock.call_args.kwargs["recipient_email"] == "known@example.com"
    assert anon_mock.call_args.kwargs["attachments"]
