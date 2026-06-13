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
def test_public_send_rejects_malformed_email(api_client, vendor, anon_brief):
    """SF-2: a syntactically invalid email is rejected with 400 before dispatch."""
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "send-studio", "email": "not-an-email"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 400
    anon_brief.refresh_from_db()
    assert anon_brief.contact_email == ""


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
def test_public_send_rejects_slug_of_other_vendor(api_client, vendor, anon_brief):
    """SF-5: the brief's DRAFT project belongs to `vendor`. A Send whose slug
    resolves to a different vendor (slug swap) must be rejected with 409."""
    other_user = User.objects.create_user(
        email="other-send-vendor@example.com",
        password="p@ssw0rd",
        name="Other",
        group="VENDOR",
    )
    other_vendor = Vendor.objects.create(name="Other Studio", owner=other_user)
    VendorSettings.objects.create(
        vendor=other_vendor, slug="other-studio", company_name="Other Studio Co"
    )

    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "other-studio", "email": "c@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 409
    # The lead must not have leaked to the wrong vendor.
    assert not Project.objects.filter(brief=anon_brief, vendor=other_vendor).exists()


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
def test_public_send_ready_with_documents_skips_finalize(api_client, vendor):
    """A ready_to_finalize brief whose document already exists (rendered on ready
    and possibly edited by the anonymous client) must not be re-finalized: the
    Send chain skips generation so manual edits survive."""
    brief = Brief.objects.create(
        client=None,
        anonymous_token="ready-edited",
        conversation_status="ready_to_finalize",
    )
    document = BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>Manually edited</p>"
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
        patch(
            "aivus_backend.projects.api.views_brief_v3.finalize_brief_task"
        ) as finalize_mock,
    ):
        response = api_client.post(
            reverse("projects_api:public_brief_ai_send", args=[brief.id]),
            data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="ready-edited",
        )
    assert response.status_code == 200
    # The Send chain is always pollable, even when no finalize runs.
    assert response.json()["finalizingTaskId"]
    chain_mock.assert_called_once()
    finalize_mock.si.assert_not_called()
    document.refresh_from_db()
    assert "Manually edited" in document.html


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
    assert response.json()["finalizingTaskId"]
    chain_mock.assert_called_once()


@pytest.mark.django_db
def test_public_send_rejects_when_finalize_in_flight(api_client, vendor):
    """MF-2: Send pressed while a GET-triggered finalize is still running (no
    documents yet, pending_task_id armed) must not enqueue a second finalize that
    would race the first and discard the in-flight document. It returns 409 and
    leaves the existing finalize marker untouched so the client keeps polling."""
    brief = Brief.objects.create(
        client=None,
        anonymous_token="finalize-in-flight",
        conversation_status="ready_to_finalize",
        pending_task_id="inflight-finalize-id",
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
            HTTP_X_BRIEF_TOKEN="finalize-in-flight",
        )
    assert response.status_code == 409
    assert "generating" in response.json()["error"].lower()
    chain_mock.assert_not_called()
    brief.refresh_from_db()
    assert brief.pending_task_id == "inflight-finalize-id"


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
    assert response.json()["finalizingTaskId"]
    chain_mock.assert_called_once()


# --- SF-8: pending stays set until promotion ---------------------------------


@pytest.mark.django_db
def test_send_marks_brief_pending_until_chain_clears(api_client, vendor):
    """Send must leave the brief "pending" so the client cannot see "sent" before
    the project is promoted. The returned finalizingTaskId equals the pending
    marker and a clear step runs only at the end of the chain."""
    brief = Brief.objects.create(
        client=None,
        anonymous_token="pending-send",
        conversation_status="finalized",
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>doc</p>"
    )
    Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.DRAFT
    )

    with patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"):
        response = api_client.post(
            reverse("projects_api:public_brief_ai_send", args=[brief.id]),
            data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="pending-send",
        )

    assert response.status_code == 200
    task_id = response.json()["finalizingTaskId"]
    assert task_id
    brief.refresh_from_db()
    assert brief.pending_task_id == task_id


@pytest.mark.django_db
def test_set_brief_pending_task_restores_marker():
    from aivus_backend.projects.tasks import set_brief_pending_task

    brief = Brief.objects.create(client=None, pending_task_id="")
    result = set_brief_pending_task.run(str(brief.id), "restored-id")
    assert result["ok"] is True
    brief.refresh_from_db()
    assert brief.pending_task_id == "restored-id"


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


@pytest.mark.django_db
def test_finalize_task_skips_when_already_finalized_with_documents():
    """MF-2: a second finalize on an already-finalized brief must be a no-op.
    generate_final_documents deletes and recreates documents, so without this
    guard a racing or retried finalize would wipe the existing document and any
    manual edits the client made before Send."""
    from aivus_backend.projects.tasks import finalize_brief_task

    brief = Brief.objects.create(
        client=None,
        conversation_status="finalized",
        status="COMPLETED",
        pending_task_id="stale-second-finalize",
    )
    document = BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>Manually edited</p>"
    )

    with patch(
        "aivus_backend.projects.tasks.generate_final_documents"
    ) as generate_mock:
        finalize_brief_task.run(str(brief.id))

    generate_mock.assert_not_called()
    document.refresh_from_db()
    assert "Manually edited" in document.html
    assert BriefFinalDocument.objects.filter(brief=brief).count() == 1
    brief.refresh_from_db()
    assert brief.pending_task_id == ""


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


# --- Send failure surfacing (MF-2) -------------------------------------------


@pytest.mark.django_db
def test_send_wires_failure_link_error(api_client, vendor, anon_brief):
    """The Send chain's link_error must point at mark_brief_send_failed_task so a
    chain failure is recorded, not silently cleared into a "done" status."""
    with (
        patch(
            "aivus_backend.projects.api.views_brief_v3.transaction.on_commit",
            side_effect=_run_on_commit,
        ),
        patch("aivus_backend.projects.api.views_brief_v3.chain") as chain_mock,
        patch(
            "aivus_backend.projects.api.views_brief_v3.mark_brief_send_failed_task"
        ) as failed_mock,
    ):
        response = api_client.post(
            reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
            data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="send-token",
        )

    assert response.status_code == 200
    task_id = response.json()["finalizingTaskId"]
    workflow = chain_mock.return_value
    _, kwargs = workflow.apply_async.call_args
    assert kwargs["link_error"] is failed_mock.si.return_value
    failed_mock.si.assert_called_once_with(str(anon_brief.id), task_id)


@pytest.mark.django_db
def test_mark_brief_send_failed_task_records_error():
    """The link_error task clears the pending marker and stamps the error so the
    status endpoint can report "failed"."""
    from aivus_backend.projects.tasks import mark_brief_send_failed_task

    brief = Brief.objects.create(client=None, pending_task_id="chain-1")
    mark_brief_send_failed_task.run(str(brief.id), "chain-1")
    brief.refresh_from_db()
    assert brief.pending_task_id == ""
    assert brief.pending_task_error == "chain-1"


@pytest.mark.django_db
def test_mark_brief_send_failed_task_ignores_stale_chain():
    """A stale chain id must not clobber a freshly re-armed Send."""
    from aivus_backend.projects.tasks import mark_brief_send_failed_task

    brief = Brief.objects.create(client=None, pending_task_id="chain-2")
    mark_brief_send_failed_task.run(str(brief.id), "chain-1")
    brief.refresh_from_db()
    assert brief.pending_task_id == "chain-2"
    assert brief.pending_task_error == ""


@pytest.mark.django_db
def test_public_status_reports_failed_on_send_error(api_client, vendor):
    """A brief whose Send chain failed must report status=failed, never done."""
    brief = Brief.objects.create(
        client=None,
        anonymous_token="failed-send",
        conversation_status="ready_to_finalize",
        source="personal_link",
        pending_task_id="",
        pending_task_error="chain-x",
    )
    response = api_client.get(
        reverse("projects_api:public_brief_ai_status", args=[brief.id]),
        HTTP_X_BRIEF_TOKEN="failed-send",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    # The flag is cleared after reporting so a fresh Send can re-arm.
    brief.refresh_from_db()
    assert brief.pending_task_error == ""
