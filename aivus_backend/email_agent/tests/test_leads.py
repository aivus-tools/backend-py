"""Tests for the inbound-email lead-seam service."""

from unittest.mock import patch

import pytest

from aivus_backend.core.enums import BriefSource
from aivus_backend.email_agent.leads import create_email_lead
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.projects.api.views_brief_v3 import _create_inbound_brief

pytestmark = pytest.mark.django_db

ENQUEUE = "aivus_backend.projects.api.views_brief_v3._enqueue_first_reply"


def test_email_lead_creates_brief_and_project_without_first_reply(vendor):
    thread = EmailThread.objects.create(vendor=vendor, provider_thread_id="t-lead")

    with patch(ENQUEUE) as enqueue:
        brief, project = create_email_lead(
            vendor=vendor,
            message="Hi, we need a corporate video in NYC next month.",
            contact_email="Client@Example.com ",
            contact_name="Jane",
            thread=thread,
        )

    enqueue.assert_not_called()
    assert brief.source == BriefSource.EMAIL
    assert brief.contact_email == "client@example.com"
    assert brief.pending_task_id == ""
    assert project is not None
    thread.refresh_from_db()
    assert thread.project_id == project.id


def test_non_email_source_still_enqueues_first_reply(vendor):
    with patch(ENQUEUE) as enqueue:
        _create_inbound_brief(
            message="Direct brief",
            source=BriefSource.DIRECT,
            vendor=vendor,
        )

    enqueue.assert_called_once()
