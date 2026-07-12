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
    path("drafts", views.list_drafts, name="list-drafts"),
    path("drafts/<uuid:draft_id>/approve", views.approve_draft, name="approve-draft"),
    path("drafts/<uuid:draft_id>/edit", views.edit_draft, name="edit-draft"),
    path("drafts/<uuid:draft_id>/reject", views.reject_draft, name="reject-draft"),
]
