"""Integration tests for the auth and public transcribe endpoints."""

from __future__ import annotations

import secrets
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.projects import stt
from aivus_backend.projects.models import Brief
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def client_user(db) -> User:
    return User.objects.create_user(
        email="transcribe-client@example.com",
        password="p@ssw0rd",
        name="Transcribe Client",
        group="CLIENT",
    )


@pytest.fixture
def client_profile(client_user) -> ClientModel:
    return ClientModel.objects.create(name="Acme", owner=client_user)


@pytest.fixture
def auth_brief(client_profile) -> Brief:
    return Brief.objects.create(client=client_profile, title="Test brief")


@pytest.fixture
def public_brief(db) -> Brief:
    return Brief.objects.create(
        client=None,
        anonymous_token=secrets.token_hex(16),
        title="Public test brief",
    )


def _auth_headers(user: User) -> dict:
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


def _public_headers(token: str) -> dict:
    return {"HTTP_X_BRIEF_TOKEN": token}


def _audio_file(name="voice.webm", content=b"FAKEWEBM", mime="audio/webm"):
    return SimpleUploadedFile(name, content, content_type=mime)


def _patch_sniff_to(monkeypatch, mime: str) -> None:
    monkeypatch.setattr(
        "aivus_backend.projects.api.views_brief_v3.sniff_mime",
        lambda *_args, **_kw: mime,
    )


def _patch_transcribe_ok(monkeypatch, text: str = "Hello world") -> None:
    monkeypatch.setattr(
        stt,
        "transcribe_audio",
        lambda *_args, **_kw: {
            "text": text,
            "language": "en-US",
            "model": "chirp_3",
        },
    )


def _patch_transcribe_raise(monkeypatch, code: str) -> None:
    def raiser(*_args, **_kw):
        raise stt.TranscriptionError(code, "boom")

    monkeypatch.setattr(stt, "transcribe_audio", raiser)


# ---------------------------------------------------------------------------
# Auth endpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_auth_transcribe_returns_text(api_client, client_user, auth_brief, monkeypatch):
    _patch_sniff_to(monkeypatch, "audio/webm")
    _patch_transcribe_ok(monkeypatch, "Hello brief")
    response = api_client.post(
        reverse("projects_api:client_brief_ai_chat_transcribe", args=[auth_brief.id]),
        data={"audio": _audio_file()},
        **_auth_headers(client_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Hello brief"
    assert body["language"] == "en-US"
    assert body["model"] == "chirp_3"


@pytest.mark.django_db
def test_auth_transcribe_404_when_brief_not_owned(api_client, client_user):
    other_owner = User.objects.create_user(
        email="other@example.com", password="p@ssw0rd", name="Other", group="CLIENT"
    )
    other_client = ClientModel.objects.create(name="Other", owner=other_owner)
    other_brief = Brief.objects.create(client=other_client)
    response = api_client.post(
        reverse("projects_api:client_brief_ai_chat_transcribe", args=[other_brief.id]),
        data={"audio": _audio_file()},
        **_auth_headers(client_user),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_auth_transcribe_400_without_audio_file(api_client, client_user, auth_brief):
    response = api_client.post(
        reverse("projects_api:client_brief_ai_chat_transcribe", args=[auth_brief.id]),
        data={},
        **_auth_headers(client_user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_auth_transcribe_400_unsupported_mime(api_client, client_user, auth_brief):
    response = api_client.post(
        reverse("projects_api:client_brief_ai_chat_transcribe", args=[auth_brief.id]),
        data={"audio": _audio_file(mime="video/mp4")},
        **_auth_headers(client_user),
    )
    assert response.status_code == 400
    assert response.json()["code"] == stt.ERROR_UNSUPPORTED_FORMAT


@pytest.mark.django_db
def test_auth_transcribe_400_sniff_mismatch(
    api_client, client_user, auth_brief, monkeypatch
):
    _patch_sniff_to(monkeypatch, "application/octet-stream")
    response = api_client.post(
        reverse("projects_api:client_brief_ai_chat_transcribe", args=[auth_brief.id]),
        data={"audio": _audio_file()},
        **_auth_headers(client_user),
    )
    assert response.status_code == 400
    assert response.json()["code"] == stt.ERROR_UNSUPPORTED_FORMAT


@pytest.mark.django_db
def test_auth_transcribe_429_when_cost_limit_reached(
    api_client, client_user, client_profile, monkeypatch
):
    brief = Brief.objects.create(client=client_profile, total_cost_usd=Decimal("99.99"))
    _patch_sniff_to(monkeypatch, "audio/webm")
    _patch_transcribe_ok(monkeypatch)
    response = api_client.post(
        reverse("projects_api:client_brief_ai_chat_transcribe", args=[brief.id]),
        data={"audio": _audio_file()},
        **_auth_headers(client_user),
    )
    assert response.status_code == 429
    assert response.json()["code"] == "cost_limit_reached"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        (stt.ERROR_UNSUPPORTED_FORMAT, 400),
        (stt.ERROR_AUDIO_TOO_LARGE, 400),
        (stt.ERROR_AUDIO_TOO_LONG, 400),
        (stt.ERROR_NO_SPEECH, 422),
        (stt.ERROR_QUOTA_EXCEEDED, 503),
        (stt.ERROR_INTERNAL, 500),
    ],
)
def test_auth_transcribe_maps_error_codes_to_http_statuses(
    api_client, client_user, auth_brief, monkeypatch, code, expected_status
):
    _patch_sniff_to(monkeypatch, "audio/webm")
    _patch_transcribe_raise(monkeypatch, code)
    response = api_client.post(
        reverse("projects_api:client_brief_ai_chat_transcribe", args=[auth_brief.id]),
        data={"audio": _audio_file()},
        **_auth_headers(client_user),
    )
    assert response.status_code == expected_status
    assert response.json()["code"] == code


# ---------------------------------------------------------------------------
# Public endpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_public_transcribe_returns_text_with_token(
    api_client, public_brief, monkeypatch
):
    _patch_sniff_to(monkeypatch, "audio/webm")
    _patch_transcribe_ok(monkeypatch, "Public hello")
    response = api_client.post(
        reverse("projects_api:public_brief_ai_chat_transcribe", args=[public_brief.id]),
        data={"audio": _audio_file()},
        **_public_headers(public_brief.anonymous_token),
    )
    assert response.status_code == 200
    assert response.json()["text"] == "Public hello"


@pytest.mark.django_db
def test_public_transcribe_404_without_token(api_client, public_brief, monkeypatch):
    _patch_sniff_to(monkeypatch, "audio/webm")
    response = api_client.post(
        reverse("projects_api:public_brief_ai_chat_transcribe", args=[public_brief.id]),
        data={"audio": _audio_file()},
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_public_transcribe_404_with_wrong_token(api_client, public_brief, monkeypatch):
    _patch_sniff_to(monkeypatch, "audio/webm")
    response = api_client.post(
        reverse("projects_api:public_brief_ai_chat_transcribe", args=[public_brief.id]),
        data={"audio": _audio_file()},
        **_public_headers("not-the-real-token"),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_public_transcribe_429_when_cost_limit_reached(api_client, monkeypatch):
    brief = Brief.objects.create(
        client=None,
        anonymous_token=secrets.token_hex(16),
        total_cost_usd=Decimal("99.99"),
    )
    _patch_sniff_to(monkeypatch, "audio/webm")
    _patch_transcribe_ok(monkeypatch)
    response = api_client.post(
        reverse("projects_api:public_brief_ai_chat_transcribe", args=[brief.id]),
        data={"audio": _audio_file()},
        **_public_headers(brief.anonymous_token or ""),
    )
    assert response.status_code == 429
