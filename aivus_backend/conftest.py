from collections.abc import Generator

import pytest
from django.core.cache import cache

from aivus_backend.users.models import User
from aivus_backend.users.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _media_storage(settings, tmpdir) -> None:
    settings.MEDIA_ROOT = tmpdir.strpath


@pytest.fixture(autouse=True)
def _clear_cache() -> Generator[None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(db) -> User:
    return UserFactory()  # type: ignore[return-value]
