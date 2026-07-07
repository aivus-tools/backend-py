"""Tests for the personal-link email path (Stage 2 S2-8)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail
from django.test import override_settings

from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects import brief_emails
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorSettings
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

    with (
        patch("aivus_backend.users.tasks.send_to_recipient_email.delay") as anon_mock,
        patch("aivus_backend.users.tasks.send_templated_email.delay") as auth_mock,
    ):
        brief_emails.send_client_lead_email(brief, "fresh@example.com", "en")

    anon_mock.assert_called_once()
    auth_mock.assert_not_called()
    # The client email no longer carries a brief copy: it routes into the cabinet.
    assert not anon_mock.call_args.kwargs.get("attachments")


@pytest.mark.django_db
@override_settings(FRONTEND_URL="https://go.aivus.co")
def test_vendor_lead_email_links_to_brief():
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
        brief_emails.send_vendor_lead_email(project, brief)

    send_mock.assert_called_once()
    project_url = send_mock.call_args.kwargs["context"]["project_url"]
    assert project_url == f"https://go.aivus.co/app/dashboard/{project.id}/brief"
    assert "/details" not in project_url


@pytest.mark.django_db
def test_vendor_lead_email_language_from_vendor_settings_not_document_language():
    """SF-10: the vendor notification language follows the vendor's own settings,
    not the brief's document_language. Inbound webhook/wix leads carry an empty
    document_language, so a vendor configured for Russian must still get a Russian
    email instead of defaulting to English."""
    from aivus_backend.users.models import UserSettings

    owner = User.objects.create_user(
        email="ru-vendor@example.com",
        password="p@ssw0rd",
        name="RU Owner",
        group="VENDOR",
    )
    UserSettings.objects.create(user=owner, language="ru")
    vendor = Vendor.objects.create(name="RU Studio", owner=owner)
    brief = Brief.objects.create(
        client=None, contact_email="lead@example.com", document_language=""
    )
    project = Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.RFP
    )

    with patch("aivus_backend.users.tasks.send_to_recipient_email.delay") as send_mock:
        brief_emails.send_vendor_lead_email(project, brief)

    send_mock.assert_called_once()
    assert send_mock.call_args.kwargs["template"] == "emails/vendor_lead_ru.html"
    assert send_mock.call_args.kwargs["subject"] == brief_emails.VENDOR_SUBJECTS["ru"]


@pytest.mark.django_db
def test_resolve_vendor_email_language_defaults_to_en_without_settings():
    owner = User.objects.create_user(
        email="no-settings-vendor@example.com",
        password="p@ssw0rd",
        name="Owner",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="Default Studio", owner=owner)
    assert brief_emails.resolve_vendor_email_language(vendor) == "en"


@pytest.mark.django_db
def test_client_email_names_real_vendor_when_project_passed():
    """The client lead email must name the actual vendor, not "your agency"."""
    owner = User.objects.create_user(
        email="named-vendor@example.com",
        password="p@ssw0rd",
        name="Owner",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="Fallback Name", owner=owner)
    VendorSettings.objects.create(vendor=vendor, company_name="Acme Productions")
    brief = Brief.objects.create(
        client=None, anonymous_token="tok-named", document_language="en"
    )
    project = Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.RFP
    )

    with patch("aivus_backend.users.tasks.send_to_recipient_email.delay") as anon_mock:
        brief_emails.send_client_lead_email(
            brief, "fresh@example.com", "en", project=project
        )

    anon_mock.assert_called_once()
    assert anon_mock.call_args.kwargs["context"]["vendor_name"] == "Acme Productions"


@pytest.mark.django_db
def test_client_email_falls_back_to_generic_without_project():
    """Without a project the email keeps the generic copy (no vendor to name)."""
    brief = Brief.objects.create(
        client=None, anonymous_token="tok-no-project", document_language="en"
    )
    with patch("aivus_backend.users.tasks.send_to_recipient_email.delay") as anon_mock:
        brief_emails.send_client_lead_email(brief, "fresh@example.com", "en")

    anon_mock.assert_called_once()
    assert anon_mock.call_args.kwargs["context"]["vendor_name"] == "your agency"


@pytest.mark.django_db
def test_client_email_existing_account_uses_login_template():
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
    ):
        brief_emails.send_client_lead_email(brief, "known@example.com", "en")

    anon_mock.assert_called_once()
    auth_mock.assert_not_called()
    assert anon_mock.call_args.kwargs["context"]["is_existing_account"] is True
    assert anon_mock.call_args.kwargs["recipient_email"] == "known@example.com"
    assert not anon_mock.call_args.kwargs.get("attachments")


@pytest.mark.django_db
def test_client_email_not_resent_to_same_recipient_for_same_brief():
    """H4: re-sending the same brief to the same address is deduplicated, so a
    bot pool cannot bomb a victim by replaying the same Send."""
    brief = Brief.objects.create(
        client=None, anonymous_token="tok-dedup", document_language="en"
    )

    with patch("aivus_backend.users.tasks.send_to_recipient_email.delay") as anon_mock:
        brief_emails.send_client_lead_email(brief, "victim@example.com", "en")
        brief_emails.send_client_lead_email(brief, "victim@example.com", "en")

    anon_mock.assert_called_once()


@pytest.mark.django_db
def test_client_email_dedup_released_when_enqueue_fails():
    """BE-3: the dedup key is claimed before the enqueue. If .delay() fails the
    email never goes out, so the key must be released — otherwise a resend is
    blocked for the full 24h window. A second attempt must therefore re-enqueue."""
    brief = Brief.objects.create(
        client=None, anonymous_token="tok-enqueue-fail", document_language="en"
    )

    with patch("aivus_backend.users.tasks.send_to_recipient_email.delay") as anon_mock:
        anon_mock.side_effect = [RuntimeError("broker down"), None]
        # First attempt: enqueue blows up after the dedup key is claimed. The
        # failure must propagate (so the caller logs it) but release the key.
        with pytest.raises(RuntimeError):
            brief_emails.send_client_lead_email(brief, "retry@example.com", "en")
        # Second attempt: the released key lets the email re-enqueue.
        brief_emails.send_client_lead_email(brief, "retry@example.com", "en")

    assert anon_mock.call_count == 2


@pytest.mark.django_db
def test_client_email_throttled_per_recipient_across_briefs():
    """H4: one recipient address cannot be bombed with branded mail from many
    different briefs. After the per-recipient ceiling the dispatch is suppressed."""
    with patch("aivus_backend.users.tasks.send_to_recipient_email.delay") as anon_mock:
        for i in range(brief_emails.CLIENT_LEAD_EMAIL_PER_RECIPIENT_MAX + 3):
            brief = Brief.objects.create(
                client=None, anonymous_token=f"tok-bomb-{i}", document_language="en"
            )
            brief_emails.send_client_lead_email(brief, "target@example.com", "en")

    assert anon_mock.call_count == brief_emails.CLIENT_LEAD_EMAIL_PER_RECIPIENT_MAX
