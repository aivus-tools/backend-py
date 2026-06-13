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
def test_public_send_resend_allowed_when_only_rfp_project_soft_deleted(
    api_client, vendor, anon_brief
):
    """H2: _brief_already_sent_to_vendor must ignore soft-deleted projects. A
    soft-deleted RFP project means the brief was never really sent, so a resend
    must be accepted instead of falsely returning 409 already_sent."""
    Project.objects.filter(brief=anon_brief, vendor=vendor).update(
        status=ProjectStatus.RFP, deleted_at=timezone.now()
    )
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

    assert response.status_code == 200
    assert response.json()["ok"] is True
    chain_mock.assert_called_once()


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


@pytest.mark.django_db
def test_set_brief_pending_task_clears_stale_error():
    """A re-asserted marker must also clear any stale failure so a re-armed Send
    is not reported as failed."""
    from aivus_backend.projects.tasks import set_brief_pending_task

    brief = Brief.objects.create(
        client=None, pending_task_id="", pending_task_error="old-fail"
    )
    set_brief_pending_task.run(str(brief.id), "restored-id")
    brief.refresh_from_db()
    assert brief.pending_task_id == "restored-id"
    assert brief.pending_task_error == ""


# --- SF-1: finalize inside a Send chain must not drop the pending marker ------


@pytest.mark.django_db
def test_finalize_keep_pending_leaves_marker_set():
    """SF-1: finalize_brief_task(keep_pending=True) is the first Send-chain step.
    It must NOT clear pending_task_id; otherwise a status poll between finalize
    finishing and the project being promoted would see no marker and report
    "done", redirecting the client to success before the RFP promotion + emails.
    The Send chain arms the marker before finalize runs, so the marker must still
    be set after finalize completes."""
    from unittest.mock import patch as _patch

    from aivus_backend.projects import tasks
    from aivus_backend.projects.models import BriefFinalDocument

    brief = Brief.objects.create(
        client=None,
        title="Existing Title",
        conversation_status="ready_to_finalize",
        document_language="en",
        pending_task_id="send-chain-id",
    )
    document = BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<h1>Brief</h1>"
    )
    fake_docs = {
        "documents": [document],
        "input_tokens": 1,
        "output_tokens": 1,
        "cost_usd": 0.0,
        "model_used": "fake",
        "traces": [],
    }
    with _patch.object(tasks, "generate_final_documents", return_value=fake_docs):
        tasks.finalize_brief_task.run(str(brief.id), keep_pending=True)

    brief.refresh_from_db()
    assert brief.pending_task_id == "send-chain-id"
    assert brief.conversation_status == "finalized"


@pytest.mark.django_db
def test_finalize_keep_pending_skip_path_leaves_marker():
    """SF-1: the already-finalized fast path must also honour keep_pending and
    leave the Send-chain marker intact."""
    from unittest.mock import patch as _patch

    from aivus_backend.projects import tasks
    from aivus_backend.projects.models import BriefFinalDocument

    brief = Brief.objects.create(
        client=None,
        conversation_status="finalized",
        status="COMPLETED",
        pending_task_id="send-chain-id",
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>doc</p>"
    )
    with _patch.object(tasks, "generate_final_documents") as generate_mock:
        tasks.finalize_brief_task.run(str(brief.id), keep_pending=True)

    generate_mock.assert_not_called()
    brief.refresh_from_db()
    assert brief.pending_task_id == "send-chain-id"


@pytest.mark.django_db
def test_finalize_standalone_still_clears_marker():
    """A standalone finalize (chat flow, keep_pending default False) must still
    clear the marker so the client stops polling."""
    from unittest.mock import patch as _patch

    from aivus_backend.projects import tasks
    from aivus_backend.projects.models import BriefFinalDocument

    brief = Brief.objects.create(
        client=None,
        title="Existing Title",
        conversation_status="ready_to_finalize",
        document_language="en",
        pending_task_id="standalone-id",
    )
    document = BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<h1>Brief</h1>"
    )
    fake_docs = {
        "documents": [document],
        "input_tokens": 1,
        "output_tokens": 1,
        "cost_usd": 0.0,
        "model_used": "fake",
        "traces": [],
    }
    with _patch.object(tasks, "generate_final_documents", return_value=fake_docs):
        tasks.finalize_brief_task.run(str(brief.id))

    brief.refresh_from_db()
    assert brief.pending_task_id == ""


@pytest.mark.django_db
def test_public_status_pending_during_send_after_finalize(api_client, vendor):
    """SF-1 end-to-end: after the Send chain's finalize completes but before the
    project is promoted to RFP, the status endpoint must report "pending", not
    "done". With keep_pending the marker is still set, so the AsyncResult branch
    runs and returns pending."""
    from unittest.mock import patch as _patch

    brief = Brief.objects.create(
        client=None,
        anonymous_token="sf1-window",
        conversation_status="finalized",
        source="personal_link",
        pending_task_id="send-chain-id",
    )
    # Project still at DRAFT (not yet promoted by mark_project_sent_task).
    Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.DRAFT
    )

    class _Running:
        def failed(self):
            return False

    with _patch(
        "aivus_backend.projects.api.views_brief_v3.AsyncResult",
        return_value=_Running(),
    ):
        response = api_client.get(
            reverse("projects_api:public_brief_ai_status", args=[brief.id]),
            HTTP_X_BRIEF_TOKEN="sf1-window",
        )
    assert response.status_code == 200
    assert response.json() == {"status": "pending"}


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
def test_concurrent_send_enqueues_single_chain(api_client, vendor, anon_brief):
    """MF-1: two Sends fired before any chain runs (the real race) must enqueue
    exactly one chain. The project promotion to RFP happens async, after the lock
    is released, so the second Send still sees the project at DRAFT and would pass
    the already-sent guard — the pending_task_id marker armed by the first Send is
    what rejects it. The masked variant (test_double_send_yields_single_project)
    artificially promotes the project between the two Sends; this one does not."""
    BriefFinalDocument.objects.create(
        brief=anon_brief, kind="production_brief", html="<p>doc</p>"
    )

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

    # No mark_project_sent_task runs between the two Sends: the project is still at
    # DRAFT. The pending marker from the first Send must reject the second.
    assert (
        Project.objects.get(brief=anon_brief, vendor=vendor).status
        == ProjectStatus.DRAFT
    )

    second, second_chain = _send()
    assert second.status_code == 409
    assert "being sent" in second.json()["error"].lower()
    second_chain.assert_not_called()


@pytest.mark.django_db
def test_send_emails_task_is_idempotent_across_chains(vendor, anon_brief):
    """MF-1 defence-in-depth: even if a duplicate chain slips past the view guard,
    send_emails_task stamps emails_sent_at under a lock and the second run is a
    no-op, so the client and vendor are emailed only once."""
    with (
        patch(
            "aivus_backend.projects.brief_emails.send_client_lead_email"
        ) as client_mock,
        patch(
            "aivus_backend.projects.brief_emails.send_vendor_lead_email"
        ) as vendor_mock,
    ):
        first = send_emails_task.run(
            str(anon_brief.id), str(vendor.id), "client@example.com", "en"
        )
        second = send_emails_task.run(
            str(anon_brief.id), str(vendor.id), "client@example.com", "en"
        )
    assert first["ok"] is True
    assert second.get("alreadySent") is True
    client_mock.assert_called_once()
    vendor_mock.assert_called_once()
    project = Project.objects.get(brief=anon_brief, vendor=vendor)
    assert project.emails_sent_at is not None


@pytest.mark.django_db
def test_mark_project_sent_ignores_soft_deleted_project(vendor, anon_brief):
    """SF-12: a soft-deleted lead project must not be resurrected. mark_project_sent
    filters deleted_at to match the conditional unique constraint and creates a
    fresh active project instead of promoting the deleted one."""
    deleted = Project.objects.get(brief=anon_brief, vendor=vendor)
    deleted.deleted_at = timezone.now()
    deleted.save(update_fields=["deleted_at"])

    result = mark_project_sent_task.run(str(anon_brief.id), str(vendor.id))
    assert result["ok"] is True

    deleted.refresh_from_db()
    assert deleted.deleted_at is not None
    assert deleted.status == ProjectStatus.DRAFT

    active = Project.objects.get(
        brief=anon_brief, vendor=vendor, deleted_at__isnull=True
    )
    assert active.id != deleted.id
    assert active.status == ProjectStatus.RFP


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


# --- MF-2: machine-readable error codes on every Send error branch -----------


@pytest.mark.django_db
def test_public_send_brief_not_found_code(api_client, vendor):
    """An unknown brief token returns 404 with code=brief_not_found."""
    missing_id = "00000000-0000-0000-0000-000000000000"
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[missing_id]),
        data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="whatever",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "brief_not_found"


@pytest.mark.django_db
def test_public_send_agency_not_found_code(api_client, anon_brief):
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "ghost", "email": "client@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "agency_not_found"


@pytest.mark.django_db
def test_public_send_soft_deleted_vendor_agency_not_found_code(
    api_client, vendor, anon_brief
):
    Vendor.objects.filter(id=vendor.id).update(deleted_at=timezone.now())
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "agency_not_found"


@pytest.mark.django_db
def test_public_send_vendor_mismatch_code(api_client, vendor, anon_brief):
    other_user = User.objects.create_user(
        email="mismatch-vendor@example.com",
        password="p@ssw0rd",
        name="Other",
        group="VENDOR",
    )
    other_vendor = Vendor.objects.create(name="Other Studio", owner=other_user)
    VendorSettings.objects.create(
        vendor=other_vendor, slug="mismatch-studio", company_name="Other Studio Co"
    )
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "mismatch-studio", "email": "c@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "vendor_mismatch"


@pytest.mark.django_db
def test_public_send_email_required_code(api_client, vendor, anon_brief):
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "send-studio"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "email_required"


@pytest.mark.django_db
def test_public_send_invalid_email_code(api_client, vendor, anon_brief):
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[anon_brief.id]),
        data=json.dumps({"slug": "send-studio", "email": "not-an-email"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="send-token",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_email"


@pytest.mark.django_db
def test_public_send_not_ready_code(api_client, vendor):
    brief = Brief.objects.create(
        client=None,
        anonymous_token="not-ready-code",
        conversation_status="in_progress",
    )
    Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.DRAFT
    )
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[brief.id]),
        data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="not-ready-code",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "not_ready"


@pytest.mark.django_db
def test_public_send_already_sent_code(api_client, vendor, anon_brief):
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
    assert response.json()["code"] == "already_sent"


@pytest.mark.django_db
def test_public_send_still_generating_code(api_client, vendor):
    """needs_finalize (no documents yet) + pending marker yields still_generating."""
    brief = Brief.objects.create(
        client=None,
        anonymous_token="still-generating",
        conversation_status="ready_to_finalize",
        pending_task_id="inflight-finalize",
    )
    Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.DRAFT
    )
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[brief.id]),
        data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="still-generating",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "still_generating"


@pytest.mark.django_db
def test_public_send_already_being_sent_code(api_client, vendor):
    """No finalize needed (documents exist) + pending marker yields
    already_being_sent: a previous Send chain already owns the brief."""
    brief = Brief.objects.create(
        client=None,
        anonymous_token="already-being-sent",
        conversation_status="finalized",
        pending_task_id="inflight-send",
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>doc</p>"
    )
    Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.DRAFT
    )
    response = api_client.post(
        reverse("projects_api:public_brief_ai_send", args=[brief.id]),
        data=json.dumps({"slug": "send-studio", "email": "c@example.com"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="already-being-sent",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "already_being_sent"


@pytest.mark.django_db
def test_client_send_brief_not_found_code(api_client, client_user):
    user, _client_profile = client_user
    missing_id = "00000000-0000-0000-0000-000000000000"
    response = api_client.post(
        reverse("projects_api:client_brief_ai_send", args=[missing_id]),
        data=json.dumps({"slug": "send-studio"}),
        content_type="application/json",
        **_client_auth(user),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "brief_not_found"


@pytest.mark.django_db
def test_client_send_agency_not_found_code(api_client, vendor, client_user):
    user, client_profile = client_user
    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="finalized",
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>doc</p>"
    )
    response = api_client.post(
        reverse("projects_api:client_brief_ai_send", args=[brief.id]),
        data=json.dumps({"slug": "ghost"}),
        content_type="application/json",
        **_client_auth(user),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "agency_not_found"
