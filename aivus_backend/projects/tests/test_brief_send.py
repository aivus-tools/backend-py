"""Tests for the personal-link Send flow (Stage 2 S2-9)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.db import IntegrityError
from django.db import transaction
from django.test import Client as DjangoTestClient
from django.urls import reverse
from django.utils import timezone

from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefFinalDocument
from aivus_backend.projects.models import Project
from aivus_backend.projects.tasks import mark_project_sent_task
from aivus_backend.projects.tasks import send_emails_task
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorSettings


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def vendor(db):
    user = User.objects.create_user(
        email="send-vendor@example.com",
        password="p@ssw0rd",
        name="Send Vendor",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="Send Studio", owner=user)
    VendorSettings.objects.create(
        vendor=vendor, slug="send-studio", company_name="Send Studio Co"
    )
    return vendor


@pytest.fixture
def anon_brief(db, vendor):
    brief = Brief.objects.create(
        client=None,
        anonymous_token="send-token",
        conversation_status="ready_to_finalize",
        source="personal_link",
    )
    Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.DRAFT
    )
    return brief


def _run_on_commit(func):
    func()


# --- public Send -------------------------------------------------------------


@pytest.mark.django_db
def test_public_send_dispatches_chain(api_client, vendor, anon_brief):
    with (
        patch(
            "aivus_backend.projects.api.views_brief_v3.transaction.on_commit",
            side_effect=_run_on_commit,
        ),
        patch("aivus_backend.projects.api.views_brief_v3.chain") as chain_mock,
    ):
        response = api_client.post(
            reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
            data=json.dumps({"slug": "send-studio", "email": "client@example.com"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="send-token",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["finalizingTaskId"]
    chain_mock.assert_called_once()
    anon_brief.refresh_from_db()
    assert anon_brief.contact_email == "client@example.com"


@pytest.mark.django_db
def test_public_send_requires_email(api_client, vendor, anon_brief):
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "send-studio"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_public_send_unknown_slug_404(api_client, anon_brief):
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "ghost", "email": "client@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_public_send_soft_deleted_vendor_404(api_client, vendor, anon_brief):
    Vendor.objects.filter(id=vendor.id).update(deleted_at=timezone.now())
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_public_send_not_ready_400(api_client, vendor):
    brief = Brief.objects.create(
        client=None,
        anonymous_token="not-ready",
        conversation_status="in_progress",
    )
    Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.DRAFT
    )
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[brief.id]),
        data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="not-ready",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_public_send_no_resend_when_already_rfp(api_client, vendor, anon_brief):
    Project.objects.filter(brief=anon_brief, vendor=vendor).update(
        status=ProjectStatus.RFP
    )
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 409


@pytest.mark.django_db
def test_public_send_finalized_skips_finalize(api_client, vendor):
    brief = Brief.objects.create(
        client=None,
        anonymous_token="already-final",
        conversation_status="finalized",
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>doc</p>"
    )
    Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.DRAFT
    )
    with (
        patch(
            "aivus_backend.projects.api.views_brief_v3.transaction.on_commit",
            side_effect=_run_on_commit,
        ),
        patch("aivus_backend.projects.api.views_brief_v3.chain") as chain_mock,
    ):
        response = api_client.post(
            reverse("projects_api:public_brief_ai_send", args=[brief.id]),
            data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="already-final",
        )
    assert response.status_code == 200
    assert "finalizingTaskId" not in response.json()
    chain_mock.assert_called_once()


# --- authenticated Send ------------------------------------------------------


@pytest.fixture
def client_user(db):
    user = User.objects.create_user(
        email="send-client@example.com",
        password="p@ssw0rd",
        name="Send Client",
        group="CLIENT",
    )
    client_profile = ClientModel.objects.create(name="Client Co", owner=user)
    return user, client_profile


def _client_auth(user) -> dict:
    return {
        "HTTP_X_API_KEY": django_settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


@pytest.mark.django_db
def test_client_send_creates_project_at_rfp(api_client, vendor, client_user):
    user, client_profile = client_user
    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="finalized",
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>doc</p>"
    )

    with (
        patch(
            "aivus_backend.projects.api.views_brief_v3.transaction.on_commit",
            side_effect=_run_on_commit,
        ),
        patch("aivus_backend.projects.api.views_brief_v3.chain") as chain_mock,
    ):
        response = api_client.post(
            reverse("projects_api:client_brief_ai_send", args=[brief.id]),
            data=json.dumps({"slug": "send-studio"}),
            content_type="application/json",
            **_client_auth(user),
        )

    assert response.status_code == 200
    assert "finalizingTaskId" not in response.json()
    chain_mock.assert_called_once()


# --- task idempotency --------------------------------------------------------


@pytest.mark.django_db
def test_mark_project_sent_promotes_draft(vendor, anon_brief):
    result = mark_project_sent_task.run(str(anon_brief.id), str(vendor.id))
    assert result["ok"] is True
    assert result["alreadySent"] is False
    project = Project.objects.get(brief=anon_brief, vendor=vendor)
    assert project.status == ProjectStatus.RFP


@pytest.mark.django_db
def test_mark_project_sent_idempotent(vendor, anon_brief):
    mark_project_sent_task.run(str(anon_brief.id), str(vendor.id))
    second = mark_project_sent_task.run(str(anon_brief.id), str(vendor.id))
    assert second["alreadySent"] is True
    assert Project.objects.filter(brief=anon_brief, vendor=vendor).count() == 1


@pytest.mark.django_db
def test_mark_project_sent_creates_when_missing(vendor, client_user):
    _user, client_profile = client_user
    brief = Brief.objects.create(client=client_profile, conversation_status="finalized")
    result = mark_project_sent_task.run(str(brief.id), str(vendor.id))
    assert result["ok"] is True
    project = Project.objects.get(brief=brief, vendor=vendor)
    assert project.status == ProjectStatus.RFP
    assert project.client_id == client_profile.id


# --- double-Send race --------------------------------------------------------


@pytest.mark.django_db
def test_unique_constraint_blocks_duplicate_active_project(vendor, anon_brief):
    with pytest.raises(IntegrityError), transaction.atomic():
        Project.objects.create(
            vendor=vendor, brief=anon_brief, name="dup", status=ProjectStatus.RFP
        )
    assert Project.objects.filter(brief=anon_brief, vendor=vendor).count() == 1


@pytest.mark.django_db
def test_double_send_yields_single_project(api_client, vendor, anon_brief):
    """A second Send after the first chain promoted the project to RFP must be
    rejected, leaving exactly one RFP project. The locked re-read in
    _dispatch_send sees the promotion regardless of the in-memory brief copy."""

    def _send():
        with (
            patch(
                "aivus_backend.projects.api.views_brief_v3.transaction.on_commit",
                side_effect=_run_on_commit,
            ),
            patch("aivus_backend.projects.api.views_brief_v3.chain") as chain_mock,
        ):
            response = api_client.post(
                reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
                data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
                content_type="application/json",
                HTTP_X_BRIEF_TOKEN="send-token",
            )
        return response, chain_mock

    first, first_chain = _send()
    assert first.status_code == 200
    first_chain.assert_called_once()

    # The first chain runs: finalize + promote the lead project to RFP.
    mark_project_sent_task.run(str(anon_brief.id), str(vendor.id))

    second, second_chain = _send()
    assert second.status_code == 409
    second_chain.assert_not_called()

    assert (
        Project.objects.filter(
            brief=anon_brief, vendor=vendor, status=ProjectStatus.RFP
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_send_emails_task_creates_share_and_emails(vendor, anon_brief):
    with (
        patch(
            "aivus_backend.projects.brief_emails.send_client_lead_email"
        ) as client_mock,
        patch(
            "aivus_backend.projects.brief_emails.send_vendor_lead_email"
        ) as vendor_mock,
    ):
        result = send_emails_task.run(
            str(anon_brief.id), str(vendor.id), "client@example.com", "en"
        )
    assert result["ok"] is True
    assert result["shareToken"]
    client_mock.assert_called_once()
    vendor_mock.assert_called_once()


@pytest.mark.django_db
def test_send_emails_task_skips_client_email_when_no_recipient(vendor, anon_brief):
    with (
        patch(
            "aivus_backend.projects.brief_emails.send_client_lead_email"
        ) as client_mock,
        patch("aivus_backend.projects.brief_emails.send_vendor_lead_email"),
    ):
        send_emails_task.run(str(anon_brief.id), str(vendor.id), "", "en")
    client_mock.assert_not_called()
