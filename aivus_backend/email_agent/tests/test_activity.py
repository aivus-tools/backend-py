"""Tests for the thread activity timeline and its API (S3-32)."""

import json

import pytest
from django.conf import settings as django_settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.email_agent import activity
from aivus_backend.email_agent.models import ActionAssignee
from aivus_backend.email_agent.models import ActionItem
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor

pytestmark = pytest.mark.django_db


def test_render_covers_key_events():
    thread = EmailThread(provider_thread_id="t1")
    cases = {
        "classified": AgentLog(
            thread=thread, event="classified", payload={"intent": "order"}
        ),
        "draft_sent": AgentLog(
            thread=thread, event="draft_sent", payload={"edited": True}
        ),
        "human_takeover": AgentLog(thread=thread, event="human_takeover", payload={}),
        "promise_tracked": AgentLog(
            thread=thread,
            event="promise_tracked",
            payload={"assignee": "client", "text": "send footage"},
        ),
        "unknown_event": AgentLog(thread=thread, event="unknown_event", payload={}),
    }
    assert "order" in activity.render_log_entry(cases["classified"])
    assert "edited" in activity.render_log_entry(cases["draft_sent"])
    assert "Producer" in activity.render_log_entry(cases["human_takeover"])
    assert "send footage" in activity.render_log_entry(cases["promise_tracked"])
    assert activity.render_log_entry(cases["unknown_event"]) == "Unknown event"


def test_serialize_activity_orders_events(db):
    user = User.objects.create_user(
        email="act-vendor@example.com", password="p@ss", name="V", group="VENDOR"
    )
    vendor = Vendor.objects.create(name="Studio", owner=user)
    account = EmailAccount.objects.create(
        vendor=vendor, role=EmailAccountRole.MONITOR, email="mon@vendor.com"
    )
    thread = EmailThread.objects.create(
        vendor=vendor,
        provider_thread_id="t1",
        canonical_subject="New project",
        memory={"whos_ball": "client"},
    )
    EmailMessage.objects.create(
        account=account,
        thread=thread,
        provider_message_id="<m1@client>",
        direction=EmailDirection.IN,
        subject="New project",
    )
    AgentLog.objects.create(
        thread=thread, event="classified", payload={"intent": "order"}
    )
    ActionItem.objects.create(
        thread=thread, assignee=ActionAssignee.CLIENT, text="send footage"
    )

    data = activity.serialize_activity(thread)

    assert data["memory"]["whos_ball"] == "client"
    assert len(data["actionItems"]) == 1
    assert len(data["events"]) == 2
    assert any("Received email" in e["text"] for e in data["events"])
    assert any("Classified" in e["text"] for e in data["events"])


def test_thread_activity_endpoint(db):
    user = User.objects.create_user(
        email="act-api@example.com", password="p@ss", name="V", group="VENDOR"
    )
    vendor = Vendor.objects.create(name="Studio", owner=user)
    thread = EmailThread.objects.create(
        vendor=vendor, provider_thread_id="t1", canonical_subject="Lead"
    )
    client = DjangoTestClient()
    auth = {
        "HTTP_X_API_KEY": django_settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }

    response = client.get(
        reverse("email_agent_api:thread-activity", args=[thread.id]), **auth
    )

    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["threadId"] == str(thread.id)
    assert "events" in body
    assert "actionItems" in body


def test_thread_activity_other_vendor_404(db):
    user = User.objects.create_user(
        email="act-a@example.com", password="p@ss", name="A", group="VENDOR"
    )
    Vendor.objects.create(name="A Studio", owner=user)
    other_owner = User.objects.create_user(
        email="act-b@example.com", password="p@ss", name="B", group="VENDOR"
    )
    other_vendor = Vendor.objects.create(name="B Studio", owner=other_owner)
    thread = EmailThread.objects.create(vendor=other_vendor, provider_thread_id="t1")

    client = DjangoTestClient()
    auth = {
        "HTTP_X_API_KEY": django_settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }
    response = client.get(
        reverse("email_agent_api:thread-activity", args=[thread.id]), **auth
    )

    assert response.status_code == 404
