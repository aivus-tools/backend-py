"""URL patterns for projects API."""

from django.urls import path

from . import views

app_name = "projects_api"

urlpatterns = [
    # Projects
    path("projects", views.projects_list, name="projects_list"),
    path("projects/<uuid:project_id>", views.project_detail, name="project_detail"),
    path(
        "projects/<uuid:project_id>/thumbnail",
        views.project_thumbnail,
        name="project_thumbnail",
    ),
    # Briefs
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
]

