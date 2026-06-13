"""URL patterns for projects API."""

from django.urls import path

from . import views
from . import views_brief_v3

app_name = "projects_api"

urlpatterns = [
    # Projects
    path("projects", views.projects_list, name="projects_list"),
    path("projects/archived", views.projects_archived, name="projects_archived"),
    path("projects/<uuid:project_id>", views.project_detail, name="project_detail"),
    path(
        "projects/<uuid:project_id>/restore",
        views.project_restore,
        name="project_restore",
    ),
    path(
        "projects/<uuid:project_id>/thumbnail",
        views.project_thumbnail,
        name="project_thumbnail",
    ),
    path(
        "vendor/projects/<uuid:project_id>/brief/documents",
        views.vendor_project_brief_documents,
        name="vendor_project_brief_documents",
    ),
    path(
        "vendor/projects/<uuid:project_id>/brief/documents/<uuid:document_id>/pdf",
        views.vendor_project_brief_document_pdf,
        name="vendor_project_brief_document_pdf",
    ),
    # Briefs (legacy - vendor/system access)
    path("briefs", views.briefs_list, name="briefs_list"),
    path("briefs/<uuid:brief_id>", views.brief_detail, name="brief_detail"),
    # Offers
    path("offers", views.offers_list, name="offers_list"),
    path("offers/<uuid:offer_id>", views.offer_detail, name="offer_detail"),
    path(
        "offers/project/<uuid:project_id>",
        views.offers_by_project,
        name="offers_by_project",
    ),
    # Offer status
    path(
        "offers/<uuid:offer_id>/status",
        views.offer_status_update,
        name="offer_status_update",
    ),
    # Offer copy
    path(
        "offers/<uuid:offer_id>/copy",
        views.offer_copy,
        name="offer_copy",
    ),
    # Shares
    path("shares", views.shares_create, name="shares_create"),
    path("shares/<str:token>", views.share_get_public, name="share_get_public"),
    path("shares/<str:token>/manage", views.share_manage, name="share_manage"),
    path(
        "shares/<str:token>/link", views.share_link_to_brief, name="share_link_to_brief"
    ),
    path(
        "shares/<str:token>/export-data",
        views.share_export_data,
        name="share_export_data",
    ),
    # Templates (Sprint 3)
    path("templates", views.templates_list, name="templates_list"),
    path("templates/<uuid:template_id>", views.template_detail, name="template_detail"),
    path(
        "templates/<uuid:template_id>/apply",
        views.template_apply,
        name="template_apply",
    ),
    # Rate Cards (Sprint 3)
    path("rates", views.rate_cards_list, name="rate_cards_list"),
    path("rates/lookup", views.rate_card_lookup, name="rate_card_lookup"),
    path("rates/<uuid:rate_card_id>", views.rate_card_detail, name="rate_card_detail"),
    # Client Briefs (Sprint 3)
    path("client/briefs", views.client_briefs_list, name="client_briefs_list"),
    path(
        "client/briefs/<uuid:brief_id>",
        views.client_brief_detail,
        name="client_brief_detail",
    ),
    path(
        "client/briefs/<uuid:brief_id>/offers",
        views.client_brief_offers,
        name="client_brief_offers",
    ),
    # AI Brief Chat (Sprint 4)
    path("client/briefs/chat", views.client_brief_chat, name="client_brief_chat"),
    path(
        "client/briefs/chat/analyze",
        views.client_brief_chat_analyze,
        name="client_brief_chat_analyze",
    ),
    # Comparison API (Sprint 4)
    path(
        "client/briefs/<uuid:brief_id>/comparison",
        views.client_brief_comparison,
        name="client_brief_comparison",
    ),
    path(
        "client/briefs/<uuid:brief_id>/comparison/analyze",
        views.client_brief_comparison_analyze,
        name="client_brief_comparison_analyze",
    ),
    # Export Data
    path(
        "offers/<uuid:offer_id>/export-data",
        views.offer_export_data,
        name="offer_export_data",
    ),
    # XLSX Upload (Sprint 5)
    path("client/xlsx-upload", views.client_xlsx_upload, name="client_xlsx_upload"),
    # AI Brief V3 (client)
    path(
        "client/briefs/ai",
        views_brief_v3.client_brief_ai_list,
        name="client_brief_ai_list",
    ),
    path(
        "client/briefs/ai/drafts",
        views_brief_v3.client_brief_ai_drafts,
        name="client_brief_ai_drafts",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/start",
        views_brief_v3.client_brief_ai_start,
        name="client_brief_ai_start",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/status",
        views_brief_v3.client_brief_ai_status,
        name="client_brief_ai_status",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/chat",
        views_brief_v3.client_brief_ai_chat,
        name="client_brief_ai_chat",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/transcribe",
        views_brief_v3.client_brief_ai_chat_transcribe,
        name="client_brief_ai_chat_transcribe",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>",
        views_brief_v3.client_brief_ai_detail,
        name="client_brief_ai_detail",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/attachments",
        views_brief_v3.client_brief_ai_attachments,
        name="client_brief_ai_attachments",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/attachments/<uuid:attachment_id>",
        views_brief_v3.client_brief_ai_attachment_delete,
        name="client_brief_ai_attachment_delete",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/feedback",
        views_brief_v3.client_brief_ai_feedback,
        name="client_brief_ai_feedback",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/messages/<uuid:message_id>/trace",
        views_brief_v3.client_brief_ai_message_trace,
        name="client_brief_ai_message_trace",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/finalize",
        views_brief_v3.client_brief_ai_finalize,
        name="client_brief_ai_finalize",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/final-documents",
        views_brief_v3.client_brief_ai_final_documents,
        name="client_brief_ai_final_documents",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/final-documents/<uuid:document_id>",
        views_brief_v3.client_brief_ai_final_document_update,
        name="client_brief_ai_final_document_update",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/final-documents/<uuid:document_id>/pdf",
        views_brief_v3.client_brief_ai_final_document_pdf,
        name="client_brief_ai_final_document_pdf",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/send",
        views_brief_v3.client_brief_ai_send,
        name="client_brief_ai_send",
    ),
    # Brief share (authenticated owner)
    path(
        "client/briefs/ai/<uuid:brief_id>/share",
        views_brief_v3.client_brief_ai_share,
        name="client_brief_ai_share",
    ),
    # Brief share (public)
    path(
        "public/brief-shares/<str:token>",
        views_brief_v3.public_brief_share_get,
        name="public_brief_share_get",
    ),
    path(
        "public/brief-shares/<str:token>/documents/<uuid:document_id>/pdf",
        views_brief_v3.public_brief_share_document_pdf,
        name="public_brief_share_document_pdf",
    ),
    # AI Brief V3 (public/anonymous)
    path(
        "public/briefs/ai/from-wix",
        views_brief_v3.public_brief_ai_from_wix,
        name="public_brief_ai_from_wix",
    ),
    path(
        "public/briefs/ai/from-webhook",
        views_brief_v3.public_brief_ai_from_webhook,
        name="public_brief_ai_from_webhook",
    ),
    path(
        "public/briefs/ai/by-slug/<slug:slug>",
        views_brief_v3.public_brief_ai_by_slug,
        name="public_brief_ai_by_slug",
    ),
    path(
        "public/briefs/ai/by-slug/<slug:slug>/drafts",
        views_brief_v3.public_brief_ai_by_slug_drafts,
        name="public_brief_ai_by_slug_drafts",
    ),
    path(
        "public/briefs/ai/<uuid:brief_id>/send",
        views_brief_v3.public_brief_ai_send,
        name="public_brief_ai_send",
    ),
    path(
        "public/briefs/ai/drafts",
        views_brief_v3.public_brief_ai_drafts,
        name="public_brief_ai_drafts",
    ),
    path(
        "public/briefs/ai/<uuid:brief_id>/start",
        views_brief_v3.public_brief_ai_start,
        name="public_brief_ai_start",
    ),
    path(
        "public/briefs/ai/<uuid:brief_id>/status",
        views_brief_v3.public_brief_ai_status,
        name="public_brief_ai_status",
    ),
    path(
        "public/briefs/ai/<uuid:brief_id>/chat",
        views_brief_v3.public_brief_ai_chat,
        name="public_brief_ai_chat",
    ),
    path(
        "public/briefs/ai/<uuid:brief_id>/transcribe",
        views_brief_v3.public_brief_ai_chat_transcribe,
        name="public_brief_ai_chat_transcribe",
    ),
    path(
        "public/briefs/ai/<uuid:brief_id>/attachments",
        views_brief_v3.public_brief_ai_attachments,
        name="public_brief_ai_attachments",
    ),
    path(
        "public/briefs/ai/<uuid:brief_id>/attachments/<uuid:attachment_id>",
        views_brief_v3.public_brief_ai_attachment_delete,
        name="public_brief_ai_attachment_delete",
    ),
    path(
        "public/briefs/ai/<uuid:brief_id>/final-documents",
        views_brief_v3.public_brief_ai_final_documents,
        name="public_brief_ai_final_documents",
    ),
    path(
        "public/briefs/ai/<uuid:brief_id>/final-documents/<uuid:document_id>",
        views_brief_v3.public_brief_ai_final_document_update,
        name="public_brief_ai_final_document_update",
    ),
    path(
        "public/briefs/ai/<uuid:brief_id>",
        views_brief_v3.public_brief_ai_detail,
        name="public_brief_ai_detail",
    ),
    path(
        "client/briefs/ai/<uuid:brief_id>/claim",
        views_brief_v3.client_brief_ai_claim,
        name="client_brief_ai_claim",
    ),
]
