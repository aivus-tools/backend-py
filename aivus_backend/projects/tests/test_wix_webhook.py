"""Tests for the Wix landing-form webhook that starts a public brief."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.projects.api.serializers import serialize_brief_v3
from aivus_backend.projects.models import Brief
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User

WEBHOOK_SECRET = "wix-test-secret"


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def wix_url() -> str:
    return reverse("projects_api:public_brief_ai_from_wix")


@pytest.fixture
def enable_webhook(settings):
    settings.WIX_WEBHOOK_SECRET = WEBHOOK_SECRET
    settings.FRONTEND_URL = "https://go.aivus.co"
    return settings


def _headers(secret: str = WEBHOOK_SECRET) -> dict:
    return {"HTTP_X_AIVUS_WEBHOOK_SECRET": secret}


@pytest.mark.django_db
def test_webhook_happy_path_without_files(api_client, wix_url, enable_webhook):
    with patch(
        "aivus_backend.projects.api.views_brief_v3.transaction.on_commit"
    ) as on_commit_mock:
        response = api_client.post(
            wix_url,
            data=json.dumps(
                {"email": "lead@example.com", "name": "Jamie", "message": "30s film"}
            ),
            content_type="application/json",
            **_headers(),
        )

    assert response.status_code == 201
    data = response.json()
    assert data["taskId"]
    assert data["token"]
    brief_id = data["briefId"]
    assert data["briefUrl"] == (
        f"https://go.aivus.co/public-brief/{brief_id}"
        f"?token={data['token']}&taskId={data['taskId']}"
    )

    brief = Brief.objects.get(id=brief_id)
    assert brief.client_id is None
    assert brief.contact_email == "lead@example.com"
    assert brief.contact_name == "Jamie"
    assert brief.message_count == 1

    message = brief.chat_messages.get(role="user")
    assert message.content == "30s film"
    assert message.anonymous_token == brief.anonymous_token
    on_commit_mock.assert_called_once()


@pytest.mark.django_db
def test_webhook_with_files_chains_import_then_reply(
    api_client, wix_url, enable_webhook
):
    files = [
        {"url": f"https://static.wixstatic.com/media/file{i}.pdf"} for i in range(4)
    ]

    with (
        patch("aivus_backend.projects.api.views_brief_v3.chain") as chain_mock,
        patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"),
    ):
        response = api_client.post(
            wix_url,
            data=json.dumps({"message": "with files", "files": files}),
            content_type="application/json",
            **_headers(),
        )

    assert response.status_code == 201
    assert response.json()["taskId"]
    chain_mock.assert_called_once()
    import_signature = chain_mock.call_args.args[0]
    passed_specs = import_signature.args[1]
    assert len(passed_specs) == 3


@pytest.mark.django_db
def test_webhook_rejects_wrong_secret(api_client, wix_url, enable_webhook):
    response = api_client.post(
        wix_url,
        data=json.dumps({"message": "hi"}),
        content_type="application/json",
        **_headers("nope"),
    )
    assert response.status_code == 401
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_webhook_rejects_when_secret_unset(api_client, wix_url, settings):
    settings.WIX_WEBHOOK_SECRET = ""
    response = api_client.post(
        wix_url,
        data=json.dumps({"message": "hi"}),
        content_type="application/json",
        **_headers("anything"),
    )
    assert response.status_code == 401
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_webhook_requires_message(api_client, wix_url, enable_webhook):
    response = api_client.post(
        wix_url,
        data=json.dumps({"email": "lead@example.com"}),
        content_type="application/json",
        **_headers(),
    )
    assert response.status_code == 400
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_webhook_email_optional(api_client, wix_url, enable_webhook):
    with patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"):
        response = api_client.post(
            wix_url,
            data=json.dumps({"message": "no email here"}),
            content_type="application/json",
            **_headers(),
        )
    assert response.status_code == 201
    brief = Brief.objects.get(id=response.json()["briefId"])
    assert brief.contact_email == ""


@pytest.mark.django_db
def test_webhook_parses_automation_payload(api_client, wix_url, enable_webhook):
    payload = {
        "formName": "My form",
        "submissions": [{"label": "First name", "value": "Jaime"}],
        "field:long_answer": "I need a brand film for a product launch",
        "field:initial_files": [
            {
                "fileId": "abc",
                "displayName": "brief.pdf",
                "url": "https://static.wixstatic.com/media/brief.pdf",
                "fileType": "pdf",
            }
        ],
        "contact": {
            "name": {"first": "Jamie", "last": "Brooks"},
            "email": "example@email.com",
        },
    }
    with (
        patch("aivus_backend.projects.api.views_brief_v3.chain") as chain_mock,
        patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"),
    ):
        response = api_client.post(
            wix_url,
            data=json.dumps(payload),
            content_type="application/json",
            **_headers(),
        )

    assert response.status_code == 201
    brief = Brief.objects.get(id=response.json()["briefId"])
    assert brief.contact_email == "example@email.com"
    assert brief.contact_name == "Jamie"
    message = brief.chat_messages.get(role="user")
    assert message.content == "I need a brand film for a product launch"
    passed_specs = chain_mock.call_args.args[0].args[1]
    assert passed_specs == [
        {"url": "https://static.wixstatic.com/media/brief.pdf", "filename": "brief.pdf"}
    ]


@pytest.mark.django_db
def test_serialize_brief_v3_exposes_contact_email():
    brief = Brief.objects.create(client=None, contact_email="lead@example.com")
    assert serialize_brief_v3(brief)["contactEmail"] == "lead@example.com"


@pytest.mark.django_db
def test_serialize_brief_v3_exposes_contact_name():
    brief = Brief.objects.create(client=None, contact_name="Jamie Brooks")
    assert serialize_brief_v3(brief)["contactName"] == "Jamie Brooks"


@pytest.mark.django_db
def test_webhook_normalizes_contact_email(api_client, wix_url, enable_webhook):
    with patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"):
        response = api_client.post(
            wix_url,
            data=json.dumps({"email": "  Lead@Example.COM ", "message": "hi"}),
            content_type="application/json",
            **_headers(),
        )
    assert response.status_code == 201
    brief = Brief.objects.get(id=response.json()["briefId"])
    assert brief.contact_email == "lead@example.com"


@pytest.mark.django_db
def test_claim_preserves_contact_email(api_client, wix_url, enable_webhook):
    with patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"):
        response = api_client.post(
            wix_url,
            data=json.dumps({"email": "lead@example.com", "message": "hi"}),
            content_type="application/json",
            **_headers(),
        )
    brief_id = response.json()["briefId"]
    token = response.json()["token"]

    user = User.objects.create_user(
        email="claimer@example.com",
        password="p@ssw0rd",
        name="Claimer",
        group="CLIENT",
    )
    client_profile = ClientModel.objects.create(name="Acme", owner=user)

    from django.conf import settings

    claim_response = api_client.post(
        reverse("projects_api:public_brief_ai_claim", args=[brief_id]),
        HTTP_X_API_KEY=settings.API_KEY,
        HTTP_X_USER_ID=str(user.id),
        HTTP_X_USER_GROUP=user.group,
        HTTP_X_BRIEF_TOKEN=token,
    )

    assert claim_response.status_code == 200
    brief = Brief.objects.get(id=brief_id)
    assert brief.client_id == client_profile.id
    assert brief.anonymous_token is None
    assert brief.contact_email == "lead@example.com"
