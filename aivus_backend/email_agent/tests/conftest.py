"""Shared fixtures for email_agent tests."""

import pytest

from aivus_backend.users.models import Vendor


@pytest.fixture
def vendor(db, user):
    return Vendor.objects.create(name="Test Agency", owner=user)
