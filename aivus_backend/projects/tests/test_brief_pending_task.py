"""Tests for Brief.pending_task_id: status endpoints, task clearing, serializer."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient
from django.urls import reverse
from django.utils import timezone

from aivus_backend.projects import tasks
from aivus_backend.projects.api.serializers import serialize_brief_v3
from aivus_backend.projects.api.views_brief_v3 import SEND_PENDING_MAX_AGE_SECONDS
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefPrompt
from aivus_backend.projects.models import ChatMessage
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User


@pytest.fixture
def seeded_prompts(db):
    for slug in (
        "main_system_prompt",
        "master_brief_template",
        "archetypes_reference",
        "finalization_prompt",
    ):
        BriefPrompt.objects.get_or_create(
            slug=slug,
            is_active=True,
            defaults={
                "title": slug,
                "body": f"test {slug}",
                "version": 1,
                "model_name": "gemini-3.1-pro-preview",
            },
        )


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def client_user(db) -> User:
    return User.objects.create_user(
        email="pending-client@example.com",
        password="p@ssw0rd",
        name="Pending Client",
        group="CLIENT",
    )


@pytest.fixture
def client_profile(client_user) -> ClientModel:
    return ClientModel.objects.create(name="Pending Acme", owner=client_user)


def _auth_headers(user: User) -> dict:
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


class _AsyncResultStub:
    def __init__(self, *, failed: bool) -> None:
        self._failed = failed
        self.result = "boom" if failed else None

    def failed(self) -> bool:
        return self._failed


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_serialize_brief_v3_pending_task_id_none_when_empty(client_profile):
    brief = Brief.objects.create(client=client_profile)
    assert serialize_brief_v3(brief)["pendingTaskId"] is None


@pytest.mark.django_db
def test_serialize_brief_v3_pending_task_id_string_when_set(client_profile):
    brief = Brief.objects.create(client=client_profile, pending_task_id="task-xyz")
    assert serialize_brief_v3(brief)["pendingTaskId"] == "task-xyz"


# ---------------------------------------------------------------------------
# Status endpoint (authority = pending_task_id)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_client_status_done_when_no_pending_task(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile, pending_task_id="")
    resp = api_client.get(
        reverse("projects_api:client_brief_ai_status", args=[brief.id]),
        **_auth_headers(client_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert data["result"]["id"] == str(brief.id)


@pytest.mark.django_db
def test_client_status_pending_when_task_running(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile, pending_task_id="task-run")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.AsyncResult",
        return_value=_AsyncResultStub(failed=False),
    ):
        resp = api_client.get(
            reverse("projects_api:client_brief_ai_status", args=[brief.id]),
            **_auth_headers(client_user),
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "pending"}


@pytest.mark.django_db
def test_client_status_failed_returns_http_200(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile, pending_task_id="task-fail")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.AsyncResult",
        return_value=_AsyncResultStub(failed=True),
    ):
        resp = api_client.get(
            reverse("projects_api:client_brief_ai_status", args=[brief.id]),
            **_auth_headers(client_user),
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


@pytest.mark.django_db
def test_public_status_failed_returns_http_200(api_client, seeded_prompts):
    brief = Brief.objects.create(
        client=None, anonymous_token="pub-tok", pending_task_id="task-fail"
    )
    with patch(
        "aivus_backend.projects.api.views_brief_v3.AsyncResult",
        return_value=_AsyncResultStub(failed=True),
    ):
        resp = api_client.get(
            reverse("projects_api:public_brief_ai_status", args=[brief.id]),
            HTTP_X_BRIEF_TOKEN="pub-tok",
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


@pytest.mark.django_db
def test_public_status_done_when_no_pending_task(api_client, seeded_prompts):
    brief = Brief.objects.create(
        client=None, anonymous_token="pub-done", pending_task_id=""
    )
    resp = api_client.get(
        reverse("projects_api:public_brief_ai_status", args=[brief.id]),
        HTTP_X_BRIEF_TOKEN="pub-done",
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"


# ---------------------------------------------------------------------------
# BE-R15-2: Send-chain pending marker has a TTL. The chain is enqueued without a
# tracked task id, so AsyncResult on the marker never sees a SIGKILL/OOM that kills
# the worker without raising; the tail clear and link_error then never fire and the
# marker would wedge "pending" forever. A marker stamped with pending_task_started_at
# older than SEND_PENDING_MAX_AGE_SECONDS is treated as a dead chain.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_client_status_pending_when_send_marker_fresh(
    api_client, client_user, client_profile, seeded_prompts
):
    """Happy-path: a Send marker stamped within the TTL still reports pending."""
    brief = Brief.objects.create(
        client=client_profile,
        pending_task_id="send-fresh",
        pending_task_started_at=timezone.now(),
    )
    with patch(
        "aivus_backend.projects.api.views_brief_v3.AsyncResult",
        return_value=_AsyncResultStub(failed=False),
    ):
        resp = api_client.get(
            reverse("projects_api:client_brief_ai_status", args=[brief.id]),
            **_auth_headers(client_user),
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "pending"}
    brief.refresh_from_db()
    assert brief.pending_task_id == "send-fresh"


@pytest.mark.django_db
def test_client_status_failed_when_send_marker_expired(
    api_client, client_user, client_profile, seeded_prompts
):
    """An expired Send marker reports failed and is released so re-Send is unblocked.
    AsyncResult must not even be consulted: the standalone chain id is unknowable."""
    brief = Brief.objects.create(
        client=client_profile,
        pending_task_id="send-dead",
        pending_task_started_at=timezone.now()
        - timedelta(seconds=SEND_PENDING_MAX_AGE_SECONDS + 60),
    )
    with patch("aivus_backend.projects.api.views_brief_v3.AsyncResult") as async_result:
        resp = api_client.get(
            reverse("projects_api:client_brief_ai_status", args=[brief.id]),
            **_auth_headers(client_user),
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    async_result.assert_not_called()
    brief.refresh_from_db()
    assert brief.pending_task_id == ""
    assert brief.pending_task_started_at is None


@pytest.mark.django_db
def test_public_status_failed_when_send_marker_expired(api_client, seeded_prompts):
    """Anonymous white-label flow: an expired Send marker reports failed too."""
    brief = Brief.objects.create(
        client=None,
        anonymous_token="pub-dead",
        pending_task_id="send-dead-pub",
        pending_task_started_at=timezone.now()
        - timedelta(seconds=SEND_PENDING_MAX_AGE_SECONDS + 60),
    )
    with patch("aivus_backend.projects.api.views_brief_v3.AsyncResult") as async_result:
        resp = api_client.get(
            reverse("projects_api:public_brief_ai_status", args=[brief.id]),
            HTTP_X_BRIEF_TOKEN="pub-dead",
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    async_result.assert_not_called()
    brief.refresh_from_db()
    assert brief.pending_task_id == ""


@pytest.mark.django_db
def test_status_single_task_marker_without_stamp_uses_asyncresult(
    api_client, client_user, client_profile, seeded_prompts
):
    """Single-task flows (first reply, finalize-on-ready) leave the stamp null even
    when old; their AsyncResult reporting must stay untouched by the TTL path."""
    brief = Brief.objects.create(
        client=client_profile,
        pending_task_id="single-task",
        pending_task_started_at=None,
    )
    with patch(
        "aivus_backend.projects.api.views_brief_v3.AsyncResult",
        return_value=_AsyncResultStub(failed=False),
    ) as async_result:
        resp = api_client.get(
            reverse("projects_api:client_brief_ai_status", args=[brief.id]),
            **_auth_headers(client_user),
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "pending"}
    async_result.assert_called_once_with("single-task")


# ---------------------------------------------------------------------------
# Task-level clearing of pending_task_id
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_clear_brief_pending_task_clears(client_profile):
    brief = Brief.objects.create(client=client_profile, pending_task_id="task-clear")
    tasks.clear_brief_pending_task(str(brief.id))
    brief.refresh_from_db()
    assert brief.pending_task_id == ""


@pytest.mark.django_db
def test_generate_first_reply_task_clears_pending_on_success(
    client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile, pending_task_id="task-first")
    ChatMessage.objects.create(
        brief=brief,
        user=client_user,
        role="user",
        content="Need a brand film",
    )
    Brief.objects.filter(id=brief.id).update(message_count=1)

    fake_result = {
        "reply": "Here is a clarifying question",
        "conversation_status": "in_progress",
        "document_language": "en",
        "ready_to_finalize": False,
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.001,
        "model_used": "fake",
        "traces": [],
    }
    with patch.object(tasks, "process_brief_turn", return_value=fake_result):
        tasks.generate_first_reply_task(str(brief.id))

    brief.refresh_from_db()
    assert brief.pending_task_id == ""


@pytest.mark.django_db
def test_finalize_brief_task_clears_pending_on_success(
    client_user, client_profile, seeded_prompts
):
    from aivus_backend.projects.models import BriefFinalDocument

    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="ready_to_finalize",
        document_language="en",
        pending_task_id="task-final",
    )
    ChatMessage.objects.create(
        brief=brief,
        user=client_user,
        role="user",
        content="Need a product video",
    )
    document = BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<h1>Brief</h1>"
    )
    fake_docs = {
        "documents": [document],
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.05,
        "model_used": "fake",
        "traces": [],
    }

    class _TitleResp:
        content = "Product Video"
        model_used = "fake-flash"
        input_tokens = 10
        output_tokens = 5
        cost_usd = 0.0001
        latency_ms = 120
        request_messages: list = []
        request_params: dict = {}

    with (
        patch.object(tasks, "generate_final_documents", return_value=fake_docs),
        patch("aivus_backend.projects.ai_brief_v3.call_llm", return_value=_TitleResp()),
    ):
        tasks.finalize_brief_task(str(brief.id))

    brief.refresh_from_db()
    assert brief.pending_task_id == ""
    # Title must be persisted in the same op that clears pending_task_id, so the
    # client never renders the finalized brief as "Untitled".
    assert brief.title == "Product Video"


@pytest.mark.django_db
def test_clear_brief_pending_task_is_idempotent(client_profile):
    """clear_brief_pending_task is safe to call when nothing is pending: it must
    not error and must leave pending_task_id empty. This matters because the
    errback may run on a brief whose task already cleared the field."""
    brief = Brief.objects.create(client=client_profile, pending_task_id="")
    tasks.clear_brief_pending_task(str(brief.id))
    tasks.clear_brief_pending_task(str(brief.id))
    brief.refresh_from_db()
    assert brief.pending_task_id == ""


@pytest.mark.django_db
def test_clear_brief_pending_task_missing_brief_is_noop():
    """Errback on an unknown brief id must not raise (terminal failure cleanup
    should never crash the worker)."""
    tasks.clear_brief_pending_task("00000000-0000-0000-0000-000000000000")


# ---------------------------------------------------------------------------
# Enqueue wiring: every apply_async that owns pending_task_id must carry
# link_error=clear_brief_pending_task.si(brief_id). This pins the regression
# surface: dropping the errback on any enqueue point fails here. The errback
# only fires on terminal FAILURE (not on intermediate RETRY), so this is what
# guarantees pending_task_id is reset when a task ultimately dies.
# ---------------------------------------------------------------------------


def _assert_clears_pending_errback(errback, brief_id: str) -> None:
    assert errback.task == "aivus_backend.projects.tasks.clear_brief_pending_task"
    assert errback.args == (brief_id,)
    assert errback.immutable is True


def _run_on_commit(func):
    func()


@pytest.mark.django_db
def test_client_start_wires_link_error_errback(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile)
    captured: dict = {}

    def _spy(*args, **kwargs):
        captured["kwargs"] = kwargs

    with (
        patch.object(tasks.generate_first_reply_task, "apply_async", side_effect=_spy),
        patch(
            "aivus_backend.projects.api.views_brief_v3.transaction.on_commit",
            side_effect=_run_on_commit,
        ),
    ):
        resp = api_client.post(
            reverse("projects_api:client_brief_ai_start", args=[brief.id]),
            data=json.dumps({"message": "Need a brand film"}),
            content_type="application/json",
            **_auth_headers(client_user),
        )

    assert resp.status_code == 201
    _assert_clears_pending_errback(captured["kwargs"]["link_error"], str(brief.id))


@pytest.mark.django_db
def test_public_start_wires_link_error_errback(api_client, seeded_prompts):
    brief = Brief.objects.create(client=None, anonymous_token="tok-wire")
    captured: dict = {}

    def _spy(*args, **kwargs):
        captured["kwargs"] = kwargs

    with (
        patch.object(tasks.generate_first_reply_task, "apply_async", side_effect=_spy),
        patch(
            "aivus_backend.projects.api.views_brief_v3.transaction.on_commit",
            side_effect=_run_on_commit,
        ),
    ):
        resp = api_client.post(
            reverse("projects_api:public_brief_ai_start", args=[brief.id]),
            data=json.dumps({"message": "Need a brand film"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="tok-wire",
        )

    assert resp.status_code == 201
    _assert_clears_pending_errback(captured["kwargs"]["link_error"], str(brief.id))


@pytest.mark.django_db
def test_client_finalize_wires_link_error_errback(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="ready_to_finalize",
        document_language="en",
    )
    ChatMessage.objects.create(
        brief=brief, user=client_user, role="user", content="Need a product video"
    )
    captured: dict = {}

    def _spy(*args, **kwargs):
        captured["kwargs"] = kwargs

    with (
        patch.object(tasks.finalize_brief_task, "apply_async", side_effect=_spy),
        patch(
            "aivus_backend.projects.api.views_brief_v3.transaction.on_commit",
            side_effect=_run_on_commit,
        ),
    ):
        resp = api_client.post(
            reverse("projects_api:client_brief_ai_finalize", args=[brief.id]),
            **_auth_headers(client_user),
        )

    assert resp.status_code == 200
    _assert_clears_pending_errback(captured["kwargs"]["link_error"], str(brief.id))
