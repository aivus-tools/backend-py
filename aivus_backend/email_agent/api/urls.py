"""Email-agent URL routes."""

from django.urls import path

from aivus_backend.email_agent.api import views

app_name = "email_agent_api"

urlpatterns = [
    path("mailboxes", views.list_mailboxes, name="list-mailboxes"),
    path("mailboxes/connect", views.connect_mailbox, name="connect-mailbox"),
    path(
        "mailboxes/<uuid:account_id>/disconnect",
        views.disconnect_mailbox,
        name="disconnect-mailbox",
    ),
]
