from django.test import Client as DjangoTestClient


def test_healthz_returns_200_without_auth():
    response = DjangoTestClient().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_no_trailing_slash_redirect():
    response = DjangoTestClient().get("/healthz", follow=False)
    assert response.status_code == 200


def test_healthz_view_is_public():
    from aivus_backend.core import views

    assert getattr(views.healthz, "is_public", False) is True
